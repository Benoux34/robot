import sqlite3

import pytest
from typer.testing import CliRunner

from robot.cli import app
from robot.evals import Run, Store
from robot.fingerprint import host_environment


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "nested" / "runs.db") as s:
        yield s


def make_run(**kwargs):
    base = dict(kind="audit", source="https://github.com/demo/demo", status="ok", duration=12.5,
                commit_sha="a" * 40, metrics={"tests_passed": 297, "coverage_percent": 97.0},
                environment={"ruff": "0.16.7"}, params={"since": "5 years ago"},
                steps=[("clone du repo", 1.7), ("lint", 0.2)])
    return Run(**(base | kwargs))


def test_save_and_get(store):
    run_id = store.save(make_run())

    loaded = store.get(run_id)
    assert loaded.metrics["tests_passed"] == 297
    assert loaded.environment["ruff"] == "0.16.7"
    assert loaded.steps == [("clone du repo", 1.7), ("lint", 0.2)]
    assert loaded.created_at.endswith("+00:00")


def test_get_accepts_short_id(store):
    run_id = store.save(make_run())
    assert store.get(run_id[:8]).id == run_id
    assert store.get("inconnu") is None


def test_recent_is_sorted_and_filtered(store):
    store.save(make_run(created_at="2026-01-01T00:00:00+00:00", source="vieux"))
    store.save(make_run(created_at="2026-09-01T00:00:00+00:00", source="récent"))
    store.save(make_run(kind="fix", created_at="2026-09-02T00:00:00+00:00", source="fix"))

    assert [r.source for r in store.recent()] == ["fix", "récent", "vieux"]
    assert [r.source for r in store.recent(kind="audit")] == ["récent", "vieux"]
    assert len(store.recent(limit=1)) == 1


def test_ids_are_unique(store):
    assert store.save(make_run()) != store.save(make_run())


def test_reopening_keeps_data(tmp_path):
    path = tmp_path / "runs.db"
    with Store(path) as first:
        run_id = first.save(make_run())
    with Store(path) as second:
        assert second.get(run_id) is not None
        assert second._db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_refuses_a_future_schema(tmp_path):
    path = tmp_path / "runs.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.commit()
    db.close()

    with pytest.raises(RuntimeError, match="version 99"):
        Store(path)


def test_host_environment():
    env = host_environment()
    assert set(env) == {"robot", "python", "platform", "docker"}
    assert env["python"].startswith("3.")


def test_cli_runs_and_show(tmp_path):
    db = tmp_path / "runs.db"
    with Store(db) as store:
        run_id = store.save(make_run())
    runner = CliRunner()

    empty = runner.invoke(app, ["runs", "--db", str(tmp_path / "vide.db")])
    assert "Aucun run enregistré" in empty.stdout

    listed = runner.invoke(app, ["runs", "--db", str(db)])
    assert run_id[:12] in listed.stdout and "audit" in listed.stdout

    shown = runner.invoke(app, ["show", run_id[:8], "--db", str(db)])
    assert "tests_passed       297" in shown.stdout
    assert "1.7s  clone du repo" in shown.stdout

    missing = runner.invoke(app, ["show", "zzzz", "--db", str(db)])
    assert missing.exit_code == 1
