import pytest

import subprocess

from robot.evals import EvalReport, Task, TaskResult, run_task


def result(task_id, robot_rank, status="ok"):
    return TaskResult(task_id=task_id, bug_file="src/a.py", status=status,
                      ranks={"robot": robot_rank, "random": 9}, candidates=20)


def test_hit_at_and_mrr():
    report = EvalReport(results=[result("a", 1), result("b", 4), result("c", None)])

    assert report.hit_at("robot", 1) == 1 / 3
    assert report.hit_at("robot", 5) == 2 / 3
    assert report.mrr("robot") == pytest.approx((1 + 0.25 + 0) / 3)
    assert report.hit_at("random", 5) == 0.0


def test_unusable_tasks_are_excluded():
    report = EvalReport(results=[result("a", 1), result("b", None, status="killed_now")])

    assert len(report.usable) == 1
    assert report.hit_at("robot", 1) == 1.0
    assert "1 tâches évaluées (1 ignorées)" in report.summary()


def test_empty_report_is_safe():
    assert EvalReport().hit_at("robot", 5) == 0.0
    assert EvalReport().mrr("robot") == 0.0


def test_run_task_on_a_local_repo(tmp_path, cache):
    repo = tmp_path / "demo"
    (repo / "src" / "demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pyproject.toml").write_text(
        '# runner\n[project]\nname = "demo"\nversion = "0.3.0"\nrequires-python = ">=3.10"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )
    (repo / "src" / "demo" / "__init__.py").write_text(
        "def sign(n):\n    if n > 0:\n        return 1\n    return -1\n"
    )
    (repo / "tests" / "test_demo.py").write_text("from demo import sign\ndef test_p(): assert sign(5) == 1\n")
    for args in (["init", "--quiet"], ["add", "."], ["commit", "--quiet", "-m", "fix: init"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=repo, check=True, capture_output=True)

    task = Task(
        id="demo-1", dataset="audit", source=str(repo), commit="HEAD", image="python:3.12-slim",
        bug={"file": "src/demo/__init__.py", "line": 2, "col": 9, "kind": "comparison",
             "before": ">", "after": ">=", "description": "off-by-one"},
        patch="", validation={"baseline_failures": []},
    )

    outcome = run_task(task)

    assert outcome.status == "ok", outcome.detail
    assert outcome.ranks["robot"] == 1
    assert outcome.candidates == 1


def test_run_task_reports_a_killed_mutation(tmp_path, cache):
    task = Task(id="demo-2", dataset="audit", source=str(tmp_path / "absent"), commit="HEAD",
                image="python:3.12-slim", bug={"file": "a.py", "line": 1, "col": 0, "kind": "number",
                                               "before": "1", "after": "2", "description": ""},
                patch="", validation={})

    outcome = run_task(task)

    assert outcome.status == "error"
    assert not outcome.usable
