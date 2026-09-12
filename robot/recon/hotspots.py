from __future__ import annotations

import ast
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from robot.recon.stack import IGNORED_DIRS, LANGUAGES

GIT_TIMEOUT = 120
DEFAULT_SINCE = "18 months ago"
FIX_PATTERN = re.compile(r"\b(fix|bug|patch|hotfix|regression|crash|broken|revert)", re.IGNORECASE)

COMPLEXITY_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.AsyncWith,
    ast.Assert, ast.IfExp, ast.Match,
)


@dataclass
class Hotspot:
    path: str
    commits: int
    fix_commits: int
    authors: int
    days_since_change: int
    complexity: int
    lines: int

    @property
    def score(self) -> float:
        return (self.commits + 2 * self.fix_commits) * self.complexity


@dataclass
class HotspotReport:
    status: str
    files: list[Hotspot] = field(default_factory=list)
    commits_analysed: int = 0

    def top(self, limit: int = 10) -> list[Hotspot]:
        return sorted(self.files, key=lambda h: -h.score)[:limit]

    def summary(self) -> str:
        if self.status != "ok":
            return self.status
        return f"{len(self.files)} fichiers sur {self.commits_analysed} commits"


def find_hotspots(repo_path: Path, since: str = DEFAULT_SINCE) -> HotspotReport:
    try:
        log = _git_log(repo_path, since)
    except (subprocess.SubprocessError, OSError):
        return HotspotReport(status="unavailable")

    commits, changes = parse_git_log(log)
    files = []
    for path, stats in changes.items():
        full = repo_path / path
        if not full.is_file() or full.is_symlink():
            continue
        source = _read(full)
        files.append(
            Hotspot(
                path=path,
                commits=stats["commits"],
                fix_commits=stats["fixes"],
                authors=len(stats["authors"]),
                days_since_change=int((time.time() - stats["last"]) // 86_400),
                complexity=complexity(source),
                lines=source.count("\n"),
            )
        )
    return HotspotReport(status="ok", files=files, commits_analysed=commits)


def parse_git_log(log: str) -> tuple[int, dict[str, dict]]:
    changes: dict[str, dict] = defaultdict(lambda: {"commits": 0, "fixes": 0, "authors": set(), "last": 0})
    commits = 0
    author, timestamp, is_fix = "", 0, False

    for line in log.splitlines():
        if line.startswith("\x01"):
            _, author, raw_time, subject = line[1:].split("\x02", 3)
            timestamp, is_fix = int(raw_time), bool(FIX_PATTERN.search(subject))
            commits += 1
        elif line.strip() and _is_source(line):
            stats = changes[line]
            stats["commits"] += 1
            stats["fixes"] += is_fix
            stats["authors"].add(author)
            stats["last"] = max(stats["last"], timestamp)

    return commits, dict(changes)


def complexity(source: str) -> int:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return 1
    return 1 + sum(_weight(node) for node in ast.walk(tree))


def _weight(node: ast.AST) -> int:
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return 1 + len(node.ifs)
    return 1 if isinstance(node, COMPLEXITY_NODES) else 0


def _git_log(repo_path: Path, since: str) -> str:
    result = subprocess.run(
        ["git", "log", f"--since={since}", "--no-merges", "--name-only",
         "--pretty=format:\x01%H\x02%an\x02%ct\x02%s"],
        cwd=repo_path, capture_output=True, text=True, timeout=GIT_TIMEOUT, check=True,
    )
    return result.stdout


def _is_source(path: str) -> bool:
    parts = Path(path).parts
    return Path(path).suffix in LANGUAGES and not any(part in IGNORED_DIRS for part in parts)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""
