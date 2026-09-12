import pytest

from robot.evals import STRATEGIES
from robot.evals.strategies import by_coverage, by_hotspots, by_random, candidate_files, rank_of
from robot.recon.coverage_report import CoverageReport, FileCoverage
from robot.recon.hotspots import Hotspot, HotspotReport
from robot.recon.lint import LintIssue, LintReport


def hotspot(path, commits=10, complexity=10):
    return Hotspot(path=path, commits=commits, fix_commits=1, authors=2,
                   days_since_change=5, complexity=complexity, lines=100)


HOTSPOTS = HotspotReport(status="ok", files=[
    hotspot("src/big.py", commits=30, complexity=40),
    hotspot("src/small.py", commits=2, complexity=2),
    hotspot("tests/test_big.py", commits=99, complexity=99),
])
COVERAGE = CoverageReport(percent=80, statements=200, missing=40, files=[
    FileCoverage(path="src/big.py", statements=100, percent=95.0, missing_lines=[1], missing_branches=[]),
    FileCoverage(path="src/small.py", statements=100, percent=10.0, missing_lines=[2], missing_branches=[]),
])
LINT = LintReport(status="ok", issues=[
    LintIssue(code="F821", message="", path="src/small.py", line=1, column=1, category="error"),
])


def test_candidates_exclude_test_files():
    assert candidate_files(HOTSPOTS, COVERAGE, LINT) == ["src/big.py", "src/small.py"]


def test_hotspots_ranking_prefers_churn_times_complexity():
    assert by_hotspots(HOTSPOTS, COVERAGE, LINT) == ["src/big.py", "src/small.py"]


def test_coverage_ranking_prefers_least_covered():
    assert by_coverage(HOTSPOTS, COVERAGE, LINT) == ["src/small.py", "src/big.py"]


def test_random_is_deterministic_per_seed():
    assert by_random(HOTSPOTS, COVERAGE, LINT, seed=1) == by_random(HOTSPOTS, COVERAGE, LINT, seed=1)
    seeds = {tuple(by_random(HOTSPOTS, COVERAGE, LINT, seed=s)) for s in range(10)}
    assert len(seeds) == 2


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_every_strategy_ranks_all_candidates(name):
    ranking = STRATEGIES[name](HOTSPOTS, COVERAGE, LINT, 0)
    assert sorted(ranking) == ["src/big.py", "src/small.py"]


def test_rank_of():
    assert rank_of("src/small.py", ["src/big.py", "src/small.py"]) == 2
    assert rank_of("absent.py", ["src/big.py"]) is None
