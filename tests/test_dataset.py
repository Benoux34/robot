import subprocess

from robot.evals import Task, build_audit_dataset, load_tasks


def make_repo(path):
    (path / "src" / "demo").mkdir(parents=True)
    (path / "tests").mkdir()
    (path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.2.0"\nrequires-python = ">=3.10"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )
    (path / "src" / "demo" / "__init__.py").write_text(
        "def sign(n):\n"
        "    if n > 0:\n"
        "        return 1\n"
        "    return -1\n"
    )
    (path / "tests" / "test_demo.py").write_text(
        "from demo import sign\ndef test_positive(): assert sign(5) == 1\n"
    )
    for args in (["init", "--quiet"], ["add", "."], ["commit", "--quiet", "-m", "init"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=path, check=True, capture_output=True)
    return path


def test_task_roundtrip(tmp_path):
    task = Task(id="demo-abc123", dataset="audit", source="/repo", commit="a" * 40,
                image="python:3.12-slim", bug={"file": "src/a.py", "line": 3}, patch="--- a\n+++ b\n")
    task.save(tmp_path)

    loaded = load_tasks(tmp_path)
    assert loaded == [task]
    assert loaded[0].review["status"] == "unreviewed"
    assert load_tasks(tmp_path / "vide") == []


def test_build_audit_dataset(tmp_path, cache):
    repo = make_repo(tmp_path / "demo")
    out = tmp_path / "dataset"

    report = build_audit_dataset(str(repo), limit=3, out=out)

    assert report.status == "ok", report.detail
    assert report.killed >= 1, "la mutation de `return 1` doit être détectée par le test"
    assert len(report.tasks) >= 1

    lines = [t.bug["line"] for t in report.tasks]
    assert len(lines) == len(set(lines)), "une seule tâche par ligne"

    task = next(t for t in report.tasks if t.bug["line"] == 2)
    assert task.bug["file"] == "src/demo/__init__.py"
    assert task.bug["kind"] in ("comparison", "number")
    assert "-    if n > 0:" in task.patch
    assert task.validation["survived"] is True
    assert task.review["status"] == "unreviewed"
    assert sorted(load_tasks(out), key=lambda t: t.id) == sorted(report.tasks, key=lambda t: t.id)


def test_build_refuses_a_repo_with_failing_tests(tmp_path, cache):
    repo = make_repo(tmp_path / "broken")
    (repo / "tests" / "test_demo.py").write_text("def test_ko(): assert 1 == 2\n")  # aucun test vert
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-am", "casse"],
                   cwd=repo, check=True, capture_output=True)

    report = build_audit_dataset(str(repo), limit=1, out=tmp_path / "vide")

    assert report.status == "baseline_unusable"
    assert report.tasks == []
    assert "failed" in report.detail
