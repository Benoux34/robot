from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from robot.audit import audit_repo
from robot.evals.dataset import Task
from robot.evals.mutations import Mutation, apply_mutation
from robot.evals.strategies import STRATEGIES, candidate_files, rank_of

Progress = Callable[[str], None]
HITS_AT = (1, 3, 5)


@dataclass
class TaskResult:
    task_id: str
    bug_file: str
    status: str
    ranks: dict[str, int | None] = field(default_factory=dict)
    candidates: int = 0
    duration: float = 0.0
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "ok"


@dataclass
class EvalReport:
    results: list[TaskResult] = field(default_factory=list)
    duration: float = 0.0

    @property
    def usable(self) -> list[TaskResult]:
        return [r for r in self.results if r.usable]

    def hit_at(self, strategy: str, k: int) -> float:
        usable = self.usable
        if not usable:
            return 0.0
        hits = sum(1 for r in usable if (r.ranks.get(strategy) or 10**9) <= k)
        return hits / len(usable)

    def mrr(self, strategy: str) -> float:
        usable = self.usable
        if not usable:
            return 0.0
        return sum(1 / r.ranks[strategy] for r in usable if r.ranks.get(strategy)) / len(usable)

    def table(self) -> str:
        header = f"{'stratégie':12}" + "".join(f"{'hit@' + str(k):>8}" for k in HITS_AT) + f"{'MRR':>8}"
        lines = [header, "-" * len(header)]
        for name in STRATEGIES:
            cells = "".join(f"{self.hit_at(name, k):7.0%} " for k in HITS_AT)
            lines.append(f"{name:12}{cells}{self.mrr(name):7.2f}")
        return "\n".join(lines)

    def summary(self) -> str:
        skipped = len(self.results) - len(self.usable)
        return (
            f"{len(self.usable)} tâches évaluées"
            + (f" ({skipped} ignorées)" if skipped else "")
            + f" en {self.duration:.0f}s"
        )


def run_task(task: Task, seed: int = 0, on_progress: Progress = lambda step: None) -> TaskResult:
    started = time.monotonic()

    def inject(repo_path: Path) -> None:
        target = repo_path / task.bug["file"]
        mutation = Mutation(
            line=task.bug["line"], col=task.bug["col"], before=task.bug["before"],
            after=task.bug["after"], kind=task.bug["kind"],
        )
        mutated = apply_mutation(target.read_text(), mutation)
        if mutated is None:
            raise ValueError(f"mutation inapplicable sur {task.bug['file']}")
        target.write_text(mutated)

    try:
        report = audit_repo(task.source, ref=task.commit, prepare=inject, on_progress=on_progress)
    except Exception as e:  # noqa: BLE001 - une tâche cassée ne doit pas arrêter l'eval
        return TaskResult(task.id, task.bug["file"], "error", detail=str(e)[:200],
                          duration=round(time.monotonic() - started, 1))

    expected = set(task.validation.get("baseline_failures", []))
    failures = {case.id for case in report.tests.problems}
    if report.tests.status not in ("passed", "failed") or not failures <= expected:
        return TaskResult(task.id, task.bug["file"], "killed_now",
                          detail=f"les tests détectent le bug : {report.tests.summary()}",
                          duration=round(time.monotonic() - started, 1))

    signals = (report.hotspots, report.tests.coverage, report.lint)
    ranks = {name: rank_of(task.bug["file"], ranking(*signals, seed)) for name, ranking in STRATEGIES.items()}

    return TaskResult(
        task_id=task.id,
        bug_file=task.bug["file"],
        status="ok",
        ranks=ranks,
        candidates=len(candidate_files(*signals)),
        duration=round(time.monotonic() - started, 1),
    )


def run_dataset(tasks: list[Task], seed: int = 0, on_progress: Progress = lambda step: None) -> EvalReport:
    started = time.monotonic()
    results = []

    for index, task in enumerate(tasks, 1):
        on_progress(f"[{index}/{len(tasks)}] {task.id} — {task.bug['file']}:{task.bug['line']}")
        result = run_task(task, seed=seed)
        results.append(result)
        on_progress(f"    {result.status} : robot={result.ranks.get('robot')} "
                    f"random={result.ranks.get('random')} sur {result.candidates} fichiers")

    return EvalReport(results=results, duration=round(time.monotonic() - started, 1))
