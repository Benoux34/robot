from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from robot.recon.coverage_report import CoverageReport
from robot.recon.hotspots import HotspotReport
from robot.recon.lint import LintReport
from robot.recon.stack import is_test_file


def plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("s" if count > 1 else "")


@dataclass
class FileRisk:
    path: str
    score: float
    reasons: list[str] = field(default_factory=list)


def compute_risks(
    hotspots: HotspotReport,
    coverage: CoverageReport | None,
    lint: LintReport,
    limit: int = 10,
) -> list[FileRisk]:
    by_path = {h.path: h for h in hotspots.files}
    covered = {f.path: f for f in (coverage.files if coverage else [])}
    lint_by_path: dict[str, list] = {}
    for issue in lint.issues:
        lint_by_path.setdefault(issue.path, []).append(issue)

    risks = []
    for path in sorted(set(by_path) | set(covered) | set(lint_by_path)):
        if is_test_file(Path(path)):
            continue

        hotspot = by_path.get(path)
        file_coverage = covered.get(path)
        issues = lint_by_path.get(path, [])

        base = float(hotspot.score) if hotspot else float(file_coverage.statements if file_coverage else 1)
        uncovered = (100.0 - file_coverage.percent) / 100.0 if file_coverage else 0.0
        score = base * (1 + 2 * uncovered) * (1 + 0.5 * len(issues))

        reasons = []
        if hotspot:
            reasons.append(
                f"{plural(hotspot.commits, 'commit')} dont {plural(hotspot.fix_commits, 'correctif')}, "
                f"{plural(hotspot.authors, 'auteur')}, complexité {hotspot.complexity}"
            )
        if file_coverage and file_coverage.percent < 100:
            detail = f"couverture {file_coverage.percent:.0f}%"
            if file_coverage.missing_lines:
                detail += f" (lignes {file_coverage.missing_ranges})"
            if file_coverage.missing_branches:
                detail += f", {plural(len(file_coverage.missing_branches), 'branche')} jamais prise"
                detail += "s" if len(file_coverage.missing_branches) > 1 else ""
            reasons.append(detail)
        if issues:
            codes = ", ".join(f"{code} x{n}" for code, n in Counter(i.code for i in issues).most_common(3))
            reasons.append(f"{plural(len(issues), 'problème')} de lint ({codes})")

        risks.append(FileRisk(path=path, score=round(score, 1), reasons=reasons))

    return sorted(risks, key=lambda r: -r.score)[:limit]
