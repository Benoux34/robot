from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class FileCoverage:
    path: str
    statements: int
    percent: float
    missing_lines: list[int]
    missing_branches: list[tuple[int, int]]

    @property
    def missing_ranges(self) -> str:
        return to_ranges(self.missing_lines)


@dataclass
class CoverageReport:
    percent: float
    statements: int
    missing: int
    files: list[FileCoverage] = field(default_factory=list)

    def least_covered(self, limit: int = 10) -> list[FileCoverage]:
        measurable = [f for f in self.files if f.statements > 0 and f.percent < 100]
        return sorted(measurable, key=lambda f: (f.percent, -f.statements))[:limit]

    def summary(self) -> str:
        return f"{self.percent:.0f}% couvert ({self.missing}/{self.statements} lignes jamais exécutées)"


def parse_coverage(raw: str) -> CoverageReport:
    data = json.loads(raw)
    totals = data["totals"]
    files = [
        FileCoverage(
            path=path,
            statements=int(info["summary"]["num_statements"]),
            percent=float(info["summary"]["percent_covered"]),
            missing_lines=[int(n) for n in info.get("missing_lines", [])],
            missing_branches=[(int(a), int(b)) for a, b in info.get("missing_branches", [])],
        )
        for path, info in data["files"].items()
    ]
    return CoverageReport(
        percent=float(totals["percent_covered"]),
        statements=int(totals["num_statements"]),
        missing=int(totals["missing_lines"]),
        files=files,
    )


def to_ranges(lines: list[int]) -> str:
    ranges: list[list[int]] = []
    for line in sorted(lines):
        if ranges and line == ranges[-1][1] + 1:
            ranges[-1][1] = line
        else:
            ranges.append([line, line])
    return ", ".join(f"{start}-{end}" if start != end else str(start) for start, end in ranges)
