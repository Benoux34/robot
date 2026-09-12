import json

from typer.testing import CliRunner

from robot.audit import AuditReport, compute_risks
from robot.cli import app
from robot.install import InstallReport, InstallStep
from robot.recon.coverage_report import CoverageReport, FileCoverage
from robot.recon.hotspots import Hotspot, HotspotReport
from robot.recon.lint import LintIssue, LintReport
from robot.recon.stack import Stack
from robot.recon.testsuite import CaseResult, SuiteResult
from robot.sandbox import ExecResult


def hotspot(path, commits=10, fixes=3, complexity=20):
    return Hotspot(path=path, commits=commits, fix_commits=fixes, authors=2,
                   days_since_change=5, complexity=complexity, lines=200)


def file_coverage(path, percent, missing=(1, 2), branches=()):
    return FileCoverage(path=path, statements=100, percent=percent,
                        missing_lines=list(missing), missing_branches=list(branches))


def lint_issue(path, code="F821"):
    return LintIssue(code=code, message="Undefined name `y`", path=path, line=3, column=1, category="error")


def sample_report():
    return AuditReport(
        source="https://github.com/demo/demo",
        commit="a" * 40,
        created_at=AuditReport.now(),
        duration=42.0,
        stack=Stack(main_language="Python", lines_by_language={"Python": 500},
                    python_requires=">=3.10", image="python:3.12-slim",
                    dependency_files=["pyproject.toml"], source_files=10, test_files=3, has_ci=True),
        install=InstallReport(steps=[InstallStep("pip install -e .", ExecResult(0, "", "", 1.0, False))]),
        tests=SuiteResult(
            status="failed", exit_code=1, duration=3.0,
            cases=[CaseResult("tests.test_a::test_ok", "passed", 0.1),
                   CaseResult("tests.test_a::test_ko", "failed", 0.1, message="assert 1 | 2 == 3")],
            coverage=CoverageReport(percent=80.0, statements=100, missing=20,
                                    files=[file_coverage("src/risky.py", 40.0, (10, 11, 12))]),
        ),
        lint=LintReport(status="ok", issues=[lint_issue("src/risky.py")]),
        hotspots=HotspotReport(status="ok", files=[hotspot("src/risky.py")], commits_analysed=50),
        risks=compute_risks(
            HotspotReport(status="ok", files=[hotspot("src/risky.py")], commits_analysed=50),
            CoverageReport(percent=80.0, statements=100, missing=20,
                           files=[file_coverage("src/risky.py", 40.0, (10, 11, 12))]),
            LintReport(status="ok", issues=[lint_issue("src/risky.py")]),
        ),
    )


def test_risk_combines_three_signals():
    risks = compute_risks(
        HotspotReport(status="ok", files=[hotspot("src/a.py"), hotspot("src/b.py")]),
        CoverageReport(percent=50.0, statements=200, missing=100,
                       files=[file_coverage("src/a.py", 0.0), file_coverage("src/b.py", 100.0, ())]),
        LintReport(status="ok", issues=[lint_issue("src/a.py")]),
    )

    assert [r.path for r in risks] == ["src/a.py", "src/b.py"]
    assert risks[0].score == 320 * 3 * 1.5
    assert len(risks[0].reasons) == 3
    assert "couverture 0%" in risks[0].reasons[1]
    assert risks[1].reasons == ["10 commits dont 3 correctifs, 2 auteurs, complexité 20"]
    assert compute_risks(HotspotReport(status="ok", files=[hotspot("src/c.py", commits=1, fixes=1)]), None, LintReport(status="ok"))[0].reasons == ["1 commit dont 1 correctif, 2 auteurs, complexité 20"]


def test_test_files_are_excluded():
    risks = compute_risks(
        HotspotReport(status="ok", files=[hotspot("tests/test_big.py", complexity=500), hotspot("src/a.py")]),
        None,
        LintReport(status="ok"),
    )
    assert [r.path for r in risks] == ["src/a.py"]


def test_risk_limit_and_lint_only_file():
    risks = compute_risks(
        HotspotReport(status="unavailable"),
        None,
        LintReport(status="ok", issues=[lint_issue("src/z.py"), lint_issue("src/z.py", "B006")]),
        limit=1,
    )
    assert [r.path for r in risks] == ["src/z.py"]
    assert risks[0].reasons == ["2 problèmes de lint (F821 x1, B006 x1)"]


def test_markdown_report():
    md = sample_report().to_markdown()
    assert md.startswith("# Audit — https://github.com/demo/demo")
    assert "| Tests | failed (1 passed, 1 failed) en 3.0s |" in md
    assert "| Couverture | 80% couvert (20/100 lignes jamais exécutées) |" in md
    assert "**`src/risky.py`**" in md
    assert "assert 1 \\| 2 == 3" in md
    assert "## Hotspots git" in md


def test_json_report():
    data = sample_report().to_dict()
    assert data["tests"]["counts"] == {"passed": 1, "failed": 1, "error": 0, "skipped": 0}
    assert data["coverage"]["least_covered"][0]["missing_lines"] == "10-12"
    assert data["hotspots"]["top"][0]["score"] == 320
    assert data["risks"][0]["path"] == "src/risky.py"
    assert json.loads(json.dumps(data))


def test_cli_help():
    result = CliRunner().invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    assert "--json" in result.stdout


def test_cli_reports_clone_error(tmp_path):
    result = CliRunner().invoke(app, ["audit", str(tmp_path / "nope")])
    assert result.exit_code == 2


def test_warnings_on_non_python_repo():
    report = sample_report()
    report.stack.main_language = "JavaScript"
    report.lint = LintReport(status="no_python_files")
    report.tests.coverage = None

    alerts = report.warnings()
    assert "JavaScript" in alerts[0]
    assert "Zéro problème signalé ne veut pas dire code sain" in alerts[1]
    assert "Couverture indisponible" in alerts[2]

    md = report.to_markdown()
    assert "> ⚠️ **À lire avant le rapport**" in md
    assert md.index("⚠️") < md.index("## Résumé")
    assert report.to_dict()["warnings"] == alerts


def test_no_warning_on_healthy_python_repo():
    assert sample_report().warnings() == []
