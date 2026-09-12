import pytest
from typer.testing import CliRunner

from robot.cli import app
from robot.evals import Group, Run, Store, compare, group_from_runs, results_markdown, score_svg, wilson
from robot.evals.runner import EvalReport, TaskResult


def result(task_id, rank, strategy="robot"):
    ranks = {name: rank for name in ("robot", "random")} if strategy == "both" else {strategy: rank}
    return TaskResult(task_id=task_id, bug_file="src/a.py", status="ok", ranks=ranks, candidates=20)


def group(name, ranks, seed=0, created="2026-09-01T10:00:00+00:00"):
    results = [result(f"t{i}", rank, "both") for i, rank in enumerate(ranks)]
    return Group(group_id=name + "0" * 10, label=name, created_at=created, seed=seed,
                 report=EvalReport(results=results))


@pytest.mark.parametrize(
    ("hits", "total", "expected"),
    [(0, 0, (0.0, 0.0)), (7, 12, (0.32, 0.81)), (58, 100, (0.48, 0.67)), (10, 10, (0.72, 1.0))],
)
def test_wilson(hits, total, expected):
    low, high = wilson(hits, total)
    assert (round(low, 2), round(high, 2)) == expected


def test_wilson_narrows_with_sample_size():
    small = wilson(6, 12)
    large = wilson(50, 100)
    assert (small[1] - small[0]) > (large[1] - large[0])


def test_compare_detects_movements():
    before = group("v1", [1, 3, 9, 4])
    after = group("v2", [2, 3, 1, 4])

    diff = compare(before, after)

    assert [t for t, _, _ in diff["improved"]] == ["t2"]
    assert [t for t, _, _ in diff["regressed"]] == ["t0"]
    assert len(diff["unchanged"]) == 2
    assert diff["delta"] == pytest.approx(0.25)


def test_compare_reports_missing_tasks():
    before = group("v1", [1, 2])
    after = group("v2", [1, 2, 3])
    after.report.results[2].task_id = "nouvelle"

    diff = compare(before, after)

    assert diff["shared"] == 2
    assert diff["only_after"] == ["nouvelle"]


def test_results_markdown():
    markdown = results_markdown([group("v1", [1, 2, 9, 9]), group("v2", [1, 2, 3, 9])])

    assert "| `v1` |" in markdown and "| `v2` |" in markdown
    assert "**50%**" in markdown and "**75%**" in markdown
    assert "## Détail du dernier run — `v2`" in markdown
    assert "un écart de moins de 25 points vaut une seule tâche" in markdown


def test_score_svg():
    svg = score_svg([group("v1", [1, 9]), group("v2", [1, 1])])

    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "v1" in svg and "v2" in svg and "hasard" in svg
    assert score_svg([]).startswith("<svg")


def test_group_from_runs(tmp_path):
    with Store(tmp_path / "runs.db") as store:
        for index in range(2):
            store.save(Run(kind="eval", dataset="audit", task_id=f"t{index}", source="src/a.py",
                           status="ok", duration=3.0, label="v9", group_id="g9",
                           params={"seed": 4}, metrics={"ranks": {"robot": index + 1}, "candidates": 10}))
        runs = store.runs_in_group("g9")

    built = group_from_runs(runs)
    assert built.name == "v9" and built.seed == 4
    assert built.report.hit_at("robot", 5) == 1.0


def test_cli_results_and_compare(tmp_path):
    db = tmp_path / "runs.db"
    with Store(db) as store:
        for group_id, label, rank in (("ga", "avant", 1), ("gb", "après", 7)):
            store.save(Run(kind="eval", dataset="audit", task_id="t0", source="src/a.py", status="ok",
                           duration=1.0, label=label, group_id=group_id, params={"seed": 0},
                           metrics={"ranks": {"robot": rank, "random": 9}, "candidates": 20}))
    runner = CliRunner()

    written = runner.invoke(app, ["results", "--db", str(db), "--out", str(tmp_path / "R.md"),
                                  "--chart", str(tmp_path / "c.svg")])
    assert written.exit_code == 0
    assert "2 version(s)" in written.stdout
    assert (tmp_path / "R.md").read_text().count("| `") >= 2
    assert (tmp_path / "c.svg").read_text().startswith("<svg")

    diff = runner.invoke(app, ["compare", "avant", "après", "--db", str(db)])
    assert diff.exit_code == 0
    assert "rang 1 → 7" in diff.stdout
    assert "regressed : 1" in diff.stdout

    missing = runner.invoke(app, ["compare", "avant", "inconnu", "--db", str(db)])
    assert missing.exit_code == 1


def test_results_without_data(tmp_path):
    result = CliRunner().invoke(app, ["results", "--db", str(tmp_path / "vide.db"),
                                      "--out", str(tmp_path / "R.md")])
    assert result.exit_code == 1
