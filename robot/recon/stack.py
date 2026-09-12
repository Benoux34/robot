from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from robot.install import read_pyproject

PYTHON_VERSIONS = ("3.12", "3.13", "3.11", "3.10", "3.9")
MAX_FILE_BYTES = 1_000_000

IGNORED_DIRS = {
    ".git", ".hg", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".tox", ".nox", "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++",
    ".cs": "C#", ".swift": "Swift",
}

TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs"}

DEPENDENCY_FILES = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "uv.lock", "poetry.lock", "Pipfile", "environment.yml", "package.json",
)


@dataclass
class Stack:
    main_language: str | None
    lines_by_language: dict[str, int]
    python_requires: str | None
    image: str
    dependency_files: list[str]
    source_files: int
    test_files: int
    has_ci: bool


def detect_stack(repo_path: Path) -> Stack:
    files = list(_walk(repo_path))
    lines = Counter()
    for path in files:
        language = LANGUAGES.get(path.suffix)
        if language:
            lines[language] += _count_lines(path)

    requires = read_pyproject(repo_path).get("project", {}).get("requires-python")
    return Stack(
        main_language=lines.most_common(1)[0][0] if lines else None,
        lines_by_language=dict(lines.most_common()),
        python_requires=requires,
        image=pick_image(requires),
        dependency_files=[name for name in DEPENDENCY_FILES if (repo_path / name).is_file()],
        source_files=sum(1 for path in files if path.suffix in LANGUAGES),
        test_files=sum(1 for path in files if is_test_file(path.relative_to(repo_path))),
        has_ci=(repo_path / ".github" / "workflows").is_dir() or (repo_path / ".gitlab-ci.yml").is_file(),
    )


def pick_image(requires_python: str | None) -> str:
    try:
        spec = SpecifierSet(requires_python or "")
    except InvalidSpecifier:
        spec = SpecifierSet()
    version = next((v for v in PYTHON_VERSIONS if spec.contains(v)), PYTHON_VERSIONS[0])
    return f"python:{version}-slim"


def _walk(repo_path: Path) -> Iterator[Path]:
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")]
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                yield path


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return 0
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def is_test_file(path: Path) -> bool:
    name = path.name
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith(("_test.py", "_test.go", "_spec.rb"))
        or ".test." in name
        or ".spec." in name
        or path.stem in ("test", "spec", "conftest")
        or any(part in TEST_DIR_NAMES for part in path.parts[:-1])
    )
