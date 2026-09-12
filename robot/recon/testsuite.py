from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree

from robot.recon.coverage_report import CoverageReport, parse_coverage
from robot.sandbox import Sandbox
from robot.sandbox.docker_sandbox import truncate

JUNIT_PATH = "/tmp/robot-junit.xml"
COVERAGE_FILE = "/tmp/robot.coverage"
COVERAGE_JSON = "/tmp/robot-coverage.json"
TEST_TIMEOUT = 600
MAX_DETAILS_CHARS = 3_000
MAX_OUTPUT_CHARS = 5_000

PYTEST_ARGS = (
    "-q --color=no --tb=short -p no:cacheprovider "
    f"--continue-on-collection-errors --junitxml={JUNIT_PATH}"
)
COVERAGE_OMIT = ",".join([
    "tests/*", "*/tests/*", "test_*.py", "*/test_*.py", "*_test.py",
    "conftest.py", "*/conftest.py", "setup.py", "docs/*",
])
PYTEST_CMD = f"python -m pytest {PYTEST_ARGS}"
COVERAGE_CMD = (
    f"python -m coverage run --branch --source=. --omit={shlex.quote(COVERAGE_OMIT)} "
    f"-m pytest {PYTEST_ARGS}"
)
TEST_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_FILE": COVERAGE_FILE}

PYTEST_STATUS = {
    0: "passed",
    1: "failed",
    2: "interrupted",
    3: "internal_error",
    4: "usage_error",
    5: "no_tests",
}

JUNIT_OUTCOMES = {"failure": "failed", "error": "error", "skipped": "skipped"}


@dataclass
class CaseResult:
    id: str
    outcome: str
    duration: float
    message: str = ""
    details: str = ""


@dataclass
class SuiteResult:
    status: str
    exit_code: int
    duration: float
    cases: list[CaseResult] = field(default_factory=list)
    output: str = ""
    coverage: CoverageReport | None = None

    def count(self, outcome: str) -> int:
        return sum(1 for case in self.cases if case.outcome == outcome)

    @property
    def problems(self) -> list[CaseResult]:
        return [case for case in self.cases if case.outcome in ("failed", "error")]

    def summary(self) -> str:
        counts = ", ".join(
            f"{self.count(o)} {o}" for o in ("passed", "failed", "error", "skipped") if self.count(o)
        )
        return f"{self.status} ({counts or 'aucun test'}) en {self.duration:.1f}s"


def run_tests(sandbox: Sandbox, timeout: int = TEST_TIMEOUT, with_coverage: bool = False) -> SuiteResult:
    sandbox.run(f"rm -f {JUNIT_PATH} {COVERAGE_FILE} {COVERAGE_JSON}")
    with_coverage = with_coverage and sandbox.run("python -m coverage --version").ok
    result = sandbox.run(COVERAGE_CMD if with_coverage else PYTEST_CMD, timeout=timeout, env=TEST_ENV)
    status = "timeout" if result.timed_out else PYTEST_STATUS.get(result.exit_code, "unknown")

    cases: list[CaseResult] = []
    try:
        cases = parse_junit(sandbox.read_file(JUNIT_PATH))
    except FileNotFoundError:
        pass
    except (ParseError, ValueError):
        status = "report_error"

    return SuiteResult(
        status=status,
        exit_code=result.exit_code,
        duration=result.duration,
        cases=cases,
        output=truncate(result.stdout + result.stderr, MAX_OUTPUT_CHARS),
        coverage=_collect_coverage(sandbox) if with_coverage and not result.timed_out else None,
    )


def _collect_coverage(sandbox: Sandbox) -> CoverageReport | None:
    sandbox.run(f"python -m coverage json -o {COVERAGE_JSON}", env=TEST_ENV)
    try:
        return parse_coverage(sandbox.read_file(COVERAGE_JSON))
    except (FileNotFoundError, ValueError, KeyError, TypeError, RecursionError):
        return None


def parse_junit(xml: str) -> list[CaseResult]:
    cases = []
    for node in ElementTree.fromstring(xml).iter("testcase"):
        classname, name = node.get("classname", ""), node.get("name", "")
        outcome, message, details = "passed", "", ""
        for tag, tag_outcome in JUNIT_OUTCOMES.items():
            child = node.find(tag)
            if child is not None:
                outcome = tag_outcome
                message = child.get("message", "")
                details = truncate(child.text or "", MAX_DETAILS_CHARS)
                break
        cases.append(
            CaseResult(
                id=f"{classname}::{name}" if classname else name,
                outcome=outcome,
                duration=float(node.get("time") or 0),
                message=message,
                details=details,
            )
        )
    return cases
