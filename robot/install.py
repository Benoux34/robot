from __future__ import annotations

import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from robot.sandbox import ExecResult, Sandbox

INSTALL_TIMEOUT = 600
TEST_NAMES = ("test", "tests", "testing")
FALLBACK_NAMES = ("dev",)
REQUIREMENT_STEMS = ("requirements", "base")
RUFF_VERSION = "0.16.7"
COVERAGE_VERSION = "7.16.0"
TOOLS_CMD = f"pip install pytest 'ruff=={RUFF_VERSION}' 'coverage[toml]=={COVERAGE_VERSION}'"

PIP_ENV = {
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_INPUT": "1",
    "PIP_ROOT_USER_ACTION": "ignore",
}


@dataclass
class InstallStep:
    cmd: str
    result: ExecResult


@dataclass
class InstallReport:
    steps: list[InstallStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(step.result.ok for step in self.steps)


def plan_install(repo_path: Path) -> list[str]:
    pyproject = read_pyproject(repo_path)
    cmds = [f"pip install -r {shlex.quote(str(req))}" for req in _requirement_files(repo_path)]

    if _is_installable(repo_path, pyproject):
        extras = _pick_test_sections(pyproject.get("project", {}).get("optional-dependencies", {}))
        target = f".[{','.join(extras)}]" if extras else "."
        cmds.append(f"pip install -e {shlex.quote(target)}")

    groups = _pick_test_sections(pyproject.get("dependency-groups", {}))
    if groups:
        cmds.insert(0, "pip install --upgrade 'pip>=25.1'")
        cmds.append("pip install " + " ".join(f"--group {shlex.quote(g)}" for g in groups))

    cmds.append(TOOLS_CMD)
    return cmds


def install(sandbox: Sandbox, repo_path: Path) -> InstallReport:
    report = InstallReport()
    for cmd in plan_install(repo_path):
        result = sandbox.run(cmd, timeout=INSTALL_TIMEOUT, env=PIP_ENV)
        report.steps.append(InstallStep(cmd=cmd, result=result))
    return report


def read_pyproject(repo_path: Path) -> dict:
    path = repo_path / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def _is_installable(repo_path: Path, pyproject: dict) -> bool:
    return (repo_path / "setup.py").is_file() or "project" in pyproject or "build-system" in pyproject


def _pick_test_sections(sections: dict) -> list[str]:
    names = [name for name in sections if name.lower() in TEST_NAMES]
    return names or [name for name in sections if name.lower() in FALLBACK_NAMES]


def _requirement_files(repo_path: Path) -> list[Path]:
    candidates = sorted(repo_path.glob("requirements*.txt")) + sorted(repo_path.glob("requirements/*.txt"))
    keywords = (*TEST_NAMES, *FALLBACK_NAMES)
    return [
        path.relative_to(repo_path)
        for path in candidates
        if path.stem in REQUIREMENT_STEMS or any(word in path.stem for word in keywords)
    ]
