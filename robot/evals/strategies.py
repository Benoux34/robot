from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path

from robot.audit.risks import compute_risks
from robot.recon.coverage_report import CoverageReport
from robot.recon.hotspots import HotspotReport
from robot.recon.lint import LintReport
from robot.recon.stack import is_test_file

Ranking = Callable[[HotspotReport, CoverageReport | None, LintReport, int], list[str]]
ALL_FILES = 1_000_000


def candidate_files(hotspots: HotspotReport, coverage: CoverageReport | None, lint: LintReport) -> list[str]:
    paths = {h.path for h in hotspots.files}
    paths |= {f.path for f in (coverage.files if coverage else [])}
    paths |= {issue.path for issue in lint.issues}
    return sorted(path for path in paths if not is_test_file(Path(path)))


def by_robot(hotspots, coverage, lint, seed=0) -> list[str]:
    return [risk.path for risk in compute_risks(hotspots, coverage, lint, limit=ALL_FILES)]


def by_hotspots(hotspots, coverage, lint, seed=0) -> list[str]:
    scores = {h.path: h.score for h in hotspots.files}
    return sorted(candidate_files(hotspots, coverage, lint), key=lambda p: -scores.get(p, 0))


def by_coverage(hotspots, coverage, lint, seed=0) -> list[str]:
    files = {f.path: f for f in (coverage.files if coverage else [])}

    def key(path: str) -> tuple[float, int]:
        entry = files.get(path)
        return (entry.percent, -entry.statements) if entry else (101.0, 0)

    return sorted(candidate_files(hotspots, coverage, lint), key=key)


def by_lint(hotspots, coverage, lint, seed=0) -> list[str]:
    counts: dict[str, int] = {}
    for issue in lint.issues:
        counts[issue.path] = counts.get(issue.path, 0) + 1
    return sorted(candidate_files(hotspots, coverage, lint), key=lambda p: -counts.get(p, 0))


def by_random(hotspots, coverage, lint, seed=0) -> list[str]:
    files = candidate_files(hotspots, coverage, lint)
    random.Random(seed).shuffle(files)
    return files


def by_churn(hotspots, coverage, lint, seed=0) -> list[str]:
    churn = {h.path: h.commits + 2 * h.fix_commits for h in hotspots.files}
    return sorted(candidate_files(hotspots, coverage, lint), key=lambda p: -churn.get(p, 0))


def by_hotspots_and_lint(hotspots, coverage, lint, seed=0) -> list[str]:
    scores = {h.path: h.score for h in hotspots.files}
    counts: dict[str, int] = {}
    for issue in lint.issues:
        counts[issue.path] = counts.get(issue.path, 0) + 1
    return sorted(
        candidate_files(hotspots, coverage, lint),
        key=lambda p: -scores.get(p, 0) * (1 + 0.5 * counts.get(p, 0)),
    )


STRATEGIES: dict[str, Ranking] = {
    "robot": by_robot,
    "hotspots": by_hotspots,
    "churn": by_churn,
    "hotspots+lint": by_hotspots_and_lint,
    "coverage": by_coverage,
    "lint": by_lint,
    "random": by_random,
}


def rank_of(path: str, ranking: list[str]) -> int | None:
    return ranking.index(path) + 1 if path in ranking else None
