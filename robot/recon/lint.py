from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from robot.sandbox import Sandbox
from robot.sandbox.docker_sandbox import WORKDIR, truncate

RUFF_OUTPUT = "/tmp/robot-ruff.json"
LINT_TIMEOUT = 120
RULES = "F,E9,B,S"
IGNORED_RULES = "S101"
TEST_FILE_PATTERNS = ("**/tests/**", "**/test_*.py", "**/*_test.py", "**/conftest.py")

RUFF_CMD = (
    "ruff check . --isolated --no-cache --exit-zero "
    f"--select {RULES} --ignore {IGNORED_RULES} "
    + " ".join(f"--per-file-ignores '{pattern}:S'" for pattern in TEST_FILE_PATTERNS)
    + f" --output-format json --output-file {RUFF_OUTPUT}"
)

PYTHON_FILES_CMD = "find . -name '*.py' -not -path '*/node_modules/*' -print -quit"

CATEGORIES = {"F": "error", "E": "error", "B": "bug-risk", "S": "security"}


@dataclass
class LintIssue:
    code: str
    message: str
    path: str
    line: int
    column: int
    category: str


@dataclass
class LintReport:
    status: str
    issues: list[LintIssue] = field(default_factory=list)
    output: str = ""

    def count(self, category: str) -> int:
        return sum(1 for issue in self.issues if issue.category == category)

    def by_code(self) -> list[tuple[str, int]]:
        return Counter(issue.code for issue in self.issues).most_common()

    def summary(self) -> str:
        if self.status == "no_python_files":
            return "aucun fichier Python à analyser"
        if self.status != "ok":
            return self.status
        counts = ", ".join(
            f"{self.count(c)} {c}" for c in ("error", "bug-risk", "security") if self.count(c)
        )
        return f"{len(self.issues)} problèmes ({counts or 'aucun'})"


def run_lint(sandbox: Sandbox) -> LintReport:
    if not sandbox.run(PYTHON_FILES_CMD).stdout.strip():
        return LintReport(status="no_python_files")

    sandbox.run(f"rm -f {RUFF_OUTPUT}")
    result = sandbox.run(RUFF_CMD, timeout=LINT_TIMEOUT)
    if not result.ok:
        status = "unavailable" if result.exit_code == 127 else "error"
        return LintReport(status=status, output=truncate(result.stdout + result.stderr, 2_000))

    try:
        issues = parse_ruff(sandbox.read_file(RUFF_OUTPUT))
    except (FileNotFoundError, ValueError, RecursionError):
        return LintReport(status="error")
    return LintReport(status="ok", issues=issues)


def parse_ruff(raw: str, root: str = WORKDIR) -> list[LintIssue]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("la sortie de ruff doit être une liste")

    issues = []
    for item in data:
        if not isinstance(item, dict):
            continue
        code = item.get("code") or "syntax-error"
        location = item.get("location") or {}
        issues.append(
            LintIssue(
                code=code,
                message=str(item.get("message", "")),
                path=_relative(str(item.get("filename", "")), root),
                line=int(location.get("row", 0)),
                column=int(location.get("column", 0)),
                category="error" if code == "syntax-error" else CATEGORIES.get(code[0], "other"),
            )
        )
    return issues


def _relative(path: str, root: str) -> str:
    prefix = root.rstrip("/") + "/"
    return path[len(prefix):] if path.startswith(prefix) else path
