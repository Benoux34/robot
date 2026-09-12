from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from robot.audit.risks import FileRisk
from robot.install import InstallReport
from robot.recon.hotspots import HotspotReport
from robot.recon.lint import LintReport
from robot.recon.stack import Stack
from robot.recon.testsuite import SuiteResult

MAX_LISTED = 15


@dataclass
class AuditReport:
    source: str
    commit: str
    created_at: str
    duration: float
    stack: Stack
    install: InstallReport
    tests: SuiteResult
    lint: LintReport
    hotspots: HotspotReport
    risks: list[FileRisk] = field(default_factory=list)
    cache: str = "off"
    cache_key: str = ""
    timings: list[tuple[str, float]] = field(default_factory=list)
    environment: dict = field(default_factory=dict)

    def metrics(self) -> dict:
        coverage = self.tests.coverage
        return {
            "install_ok": self.install.ok,
            "cache": self.cache,
            "source_files": self.stack.source_files,
            "lines": sum(self.stack.lines_by_language.values()),
            "tests_status": self.tests.status,
            "tests_passed": self.tests.count("passed"),
            "tests_failed": self.tests.count("failed"),
            "tests_error": self.tests.count("error"),
            "coverage_percent": round(coverage.percent, 1) if coverage else None,
            "lint_status": self.lint.status,
            "lint_issues": len(self.lint.issues),
            "lint_security": self.lint.count("security"),
            "hotspot_files": len(self.hotspots.files),
            "commits_analysed": self.hotspots.commits_analysed,
            "risk_top1": self.risks[0].path if self.risks else None,
            "warnings": len(self.warnings()),
        }

    def warnings(self) -> list[str]:
        alerts = []
        if self.stack.main_language != "Python":
            alerts.append(
                f"Repo principalement en **{self.stack.main_language or 'langage inconnu'}** : "
                "Robot n'analyse que du Python. Seuls la structure et l'historique git sont fiables ici."
            )
        if not self.install.ok and self.cache != "hit":
            failed = [s.cmd for s in self.install.steps if not s.result.ok]
            alerts.append(f"Installation incomplète ({len(failed)} commande(s) en échec) : les tests peuvent être faussés.")
        if self.lint.status != "ok":
            alerts.append(f"Lint non exploitable : `{self.lint.summary()}`. **Zéro problème signalé ne veut pas dire code sain.**")
        if self.tests.status in ("no_tests", "timeout", "usage_error", "internal_error"):
            alerts.append(f"Tests non exploitables : `{self.tests.status}`.")
        if self.tests.coverage is None:
            alerts.append("Couverture indisponible : impossible de savoir quel code n'est jamais exécuté.")
        if self.hotspots.status != "ok":
            alerts.append("Historique git indisponible : pas de churn ni de hotspots.")
        return alerts

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        coverage = self.tests.coverage
        return {
            "source": self.source,
            "commit": self.commit,
            "created_at": self.created_at,
            "duration": round(self.duration, 1),
            "stack": asdict(self.stack),
            "install": {
                "ok": self.install.ok,
                "steps": [
                    {"cmd": s.cmd, "ok": s.result.ok, "duration": s.result.duration}
                    for s in self.install.steps
                ],
            },
            "tests": {
                "status": self.tests.status,
                "duration": self.tests.duration,
                "counts": {
                    outcome: self.tests.count(outcome)
                    for outcome in ("passed", "failed", "error", "skipped")
                },
                "problems": [asdict(case) for case in self.tests.problems[:MAX_LISTED]],
            },
            "coverage": {
                "percent": round(coverage.percent, 1),
                "statements": coverage.statements,
                "missing": coverage.missing,
                "least_covered": [
                    {
                        "path": f.path,
                        "percent": round(f.percent, 1),
                        "missing_lines": f.missing_ranges,
                        "missing_branches": len(f.missing_branches),
                    }
                    for f in coverage.least_covered(MAX_LISTED)
                ],
            } if coverage else None,
            "lint": {
                "status": self.lint.status,
                "by_code": dict(self.lint.by_code()),
                "issues": [asdict(issue) for issue in self.lint.issues[:MAX_LISTED]],
            },
            "hotspots": {
                "status": self.hotspots.status,
                "commits_analysed": self.hotspots.commits_analysed,
                "top": [asdict(h) | {"score": h.score} for h in self.hotspots.top(MAX_LISTED)],
            },
            "risks": [asdict(risk) for risk in self.risks],
            "warnings": self.warnings(),
            "cache": {"state": self.cache, "key": self.cache_key},
            "metrics": self.metrics(),
            "environment": self.environment,
            "timings": [{"step": name, "duration": duration} for name, duration in self.timings],
        }

    def to_markdown(self) -> str:
        coverage = self.tests.coverage
        lines = [
            f"# Audit — {self.source}",
            "",
            f"- **Commit** : `{self.commit[:12]}`",
            f"- **Date** : {self.created_at}",
            f"- **Durée** : {self.duration:.0f}s",
            "",
        ]

        alerts = self.warnings()
        if alerts:
            lines += ["> ⚠️ **À lire avant le rapport**", ">"]
            lines += [f"> - {alert}" for alert in alerts]
            lines.append("")

        lines += [
            "## Résumé",
            "",
            "| Signal | Résultat |",
            "|---|---|",
            f"| Stack | {self.stack.main_language}, {self.stack.source_files} fichiers, "
            f"{sum(self.stack.lines_by_language.values())} lignes, image `{self.stack.image}` |",
            f"| Installation | {_install_label(self.install, self.cache)} |",
            f"| Tests | {self.tests.summary()} |",
            f"| Couverture | {coverage.summary() if coverage else 'indisponible'} |",
            f"| Lint | {self.lint.summary()} |",
            f"| Hotspots | {self.hotspots.summary()} |",
            "",
        ]

        lines += ["## À regarder en priorité", ""]
        if self.risks:
            for i, risk in enumerate(self.risks, 1):
                lines.append(f"{i}. **`{risk.path}`** (score {risk.score:.0f})")
                lines += [f"   - {reason}" for reason in risk.reasons]
            lines.append("")
        else:
            lines += ["Aucun signal croisé.", ""]

        if self.tests.problems:
            lines += ["## Tests en échec", "", "| Test | Message |", "|---|---|"]
            lines += [
                f"| `{case.id}` | {_cell(case.message)} |" for case in self.tests.problems[:MAX_LISTED]
            ]
            lines.append("")

        if self.lint.issues:
            lines += ["## Lint", "", "| Fichier | Code | Message |", "|---|---|---|"]
            lines += [
                f"| `{i.path}:{i.line}` | {i.code} | {_cell(i.message)} |"
                for i in self.lint.issues[:MAX_LISTED]
            ]
            lines.append("")

        if coverage:
            lines += ["## Fichiers les moins couverts", "", "| Fichier | Couverture | Lignes non exécutées |", "|---|---|---|"]
            lines += [
                f"| `{f.path}` | {f.percent:.0f}% | {_cell(f.missing_ranges)} |"
                for f in coverage.least_covered(MAX_LISTED)
            ]
            lines.append("")

        if self.hotspots.files:
            lines += ["## Hotspots git", "", "| Fichier | Score | Commits | Correctifs | Auteurs | Complexité |", "|---|---|---|---|---|---|"]
            lines += [
                f"| `{h.path}` | {h.score:.0f} | {h.commits} | {h.fix_commits} | {h.authors} | {h.complexity} |"
                for h in self.hotspots.top(MAX_LISTED)
            ]
            lines.append("")

        if not self.install.ok and self.cache != "hit":
            lines += ["## Installation", "", "| Commande | Résultat |", "|---|---|"]
            lines += [
                f"| `{s.cmd}` | {'ok' if s.result.ok else '**échec**'} |" for s in self.install.steps
            ]
            lines.append("")

        return "\n".join(lines)


def _install_label(install: InstallReport, cache: str) -> str:
    if cache == "hit":
        return "environnement repris du cache (aucune installation)"
    return "réussie" if install.ok else "**échouée**"


def _cell(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[:limit] + "…"
    return flat.replace("|", "\\|")
