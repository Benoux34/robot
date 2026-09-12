import json

import pytest

from robot.recon import parse_coverage, to_ranges


def file_entry(statements, percent, missing, branches=()):
    return {
        "summary": {"num_statements": statements, "percent_covered": percent},
        "missing_lines": missing,
        "missing_branches": [list(b) for b in branches],
    }


REPORT = json.dumps({
    "totals": {"percent_covered": 70.0, "num_statements": 100, "missing_lines": 30},
    "files": {
        "src/full.py": file_entry(10, 100.0, []),
        "src/empty.py": file_entry(0, 100.0, []),
        "src/big.py": file_entry(60, 50.0, [10, 11, 12, 20, 30, 31], [(9, 20)]),
        "src/small.py": file_entry(30, 50.0, [5]),
        "src/dead.py": file_entry(5, 0.0, [1, 2, 3, 4, 5]),
    },
})


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([], ""),
        ([7], "7"),
        ([3, 1, 2], "1-3"),
        ([10, 11, 12, 20, 30, 31], "10-12, 20, 30-31"),
    ],
)
def test_to_ranges(lines, expected):
    assert to_ranges(lines) == expected


def test_parse_coverage():
    report = parse_coverage(REPORT)
    assert report.percent == 70.0
    assert report.summary() == "70% couvert (30/100 lignes jamais exécutées)"
    big = next(f for f in report.files if f.path == "src/big.py")
    assert big.missing_ranges == "10-12, 20, 30-31"
    assert big.missing_branches == [(9, 20)]


def test_least_covered_ordering():
    report = parse_coverage(REPORT)
    assert [f.path for f in report.least_covered()] == ["src/dead.py", "src/big.py", "src/small.py"]
    assert len(report.least_covered(limit=1)) == 1


def test_parse_coverage_rejects_garbage():
    with pytest.raises((KeyError, ValueError)):
        parse_coverage('{"pas": "un rapport"}')
