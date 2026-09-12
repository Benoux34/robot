import threading
import time

import pytest

from robot.env_cache import lock_for
from robot.evals import Store, Task, run_dataset
from robot.evals import runner as runner_module
from robot.evals.runner import TaskResult


def task(index):
    return Task(id=f"t{index}", dataset="audit", source="/repo", commit="a" * 40,
                image="python:3.12-slim",
                bug={"file": f"src/{index}.py", "line": 1, "col": 0, "kind": "number",
                     "before": "1", "after": "2", "description": ""},
                patch="", validation={"baseline_failures": []})


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def install(fn):
        monkeypatch.setattr(runner_module, "run_task", fn)
        return calls

    return install, calls


def test_results_follow_task_order(fake_run):
    install, calls = fake_run

    def fake(task, seed=0):
        calls.append(task.id)
        time.sleep(0.05 if task.id == "t1" else 0.0)
        return TaskResult(task.id, "src/a.py", "ok", ranks={"robot": 1})

    install(fake)
    report = run_dataset([task(i) for i in range(4)], workers=3)

    assert [r.task_id for r in report.results] == ["t0", "t1", "t2", "t3"]
    assert sorted(calls) == ["t0", "t1", "t2", "t3"]


def test_workers_run_in_parallel(fake_run):
    install, _ = fake_run
    running, peak = 0, 0
    lock = threading.Lock()

    def fake(task, seed=0):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.2)
        with lock:
            running -= 1
        return TaskResult(task.id, "src/a.py", "ok")

    install(fake)
    started = time.monotonic()
    run_dataset([task(i) for i in range(4)], workers=4)
    duration = time.monotonic() - started

    assert peak == 4
    assert duration < 0.6


def test_retry_only_on_infrastructure_errors(fake_run):
    install, calls = fake_run
    attempts = {"t0": 0, "t1": 0}

    def fake(task, seed=0):
        attempts[task.id] += 1
        if task.id == "t0" and attempts["t0"] == 1:
            return TaskResult(task.id, "src/a.py", "error", detail="docker down")
        if task.id == "t1":
            return TaskResult(task.id, "src/a.py", "killed_now")
        return TaskResult(task.id, "src/a.py", "ok")

    install(fake)
    report = run_dataset([task(0), task(1)], retry=2)

    assert attempts == {"t0": 2, "t1": 1}
    assert [r.status for r in report.results] == ["ok", "killed_now"]


def test_skip_already_done(fake_run):
    install, calls = fake_run
    install(lambda task, seed=0: (calls.append(task.id), TaskResult(task.id, "a", "ok"))[1])

    report = run_dataset([task(0), task(1), task(2)], skip={"t1"})

    assert calls == ["t0", "t2"]
    assert len(report.results) == 2


def test_completed_tasks_from_store(tmp_path):
    from robot.evals import Run

    with Store(tmp_path / "runs.db") as store:
        store.save(Run(kind="eval", dataset="audit", task_id="t0", source="x", status="ok",
                       duration=1, params={"seed": 0}))
        store.save(Run(kind="eval", dataset="audit", task_id="t1", source="x", status="error",
                       duration=1, params={"seed": 0}))
        store.save(Run(kind="eval", dataset="audit", task_id="t2", source="x", status="ok",
                       duration=1, params={"seed": 7}))

        assert store.completed_tasks("audit", seed=0) == {"t0"}
        assert store.completed_tasks("audit", seed=7) == {"t2"}
        assert store.completed_tasks("fix", seed=0) == set()


def test_one_lock_per_cache_key():
    assert lock_for("abc") is lock_for("abc")
    assert lock_for("abc") is not lock_for("def")


def test_same_key_builds_once():
    builds = []
    key = "clé-de-test"

    def build() -> None:
        with lock_for(key):
            if key not in builds:
                time.sleep(0.05)
                builds.append(key)

    threads = [threading.Thread(target=build) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert builds == [key]
