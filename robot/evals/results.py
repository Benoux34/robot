from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from robot.evals.runner import HITS_AT, EvalReport, TaskResult, result_from_metrics
from robot.evals.store import Run
from robot.evals.strategies import STRATEGIES

DEFAULT_RESULTS = Path("RESULTS.md")
DEFAULT_CHART = Path("evals/score.svg")
HEADLINE = ("robot", 5)


@dataclass
class Group:
    group_id: str
    label: str
    created_at: str
    seed: int
    report: EvalReport

    @property
    def name(self) -> str:
        return self.label or self.group_id[:8]

    @property
    def headline(self) -> float:
        return self.report.hit_at(*HEADLINE)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def group_from_runs(runs: list[Run]) -> Group:
    results = [
        result_from_metrics(run.task_id, run.source, run.status, run.duration, run.metrics)
        for run in runs
    ]
    first = runs[0]
    return Group(
        group_id=first.group_id,
        label=first.label,
        created_at=first.created_at,
        seed=int(first.params.get("seed", 0)),
        report=EvalReport(results=results, duration=sum(r.duration for r in results)),
    )


def results_markdown(groups: list[Group], chart: Path | None = None) -> str:
    lines = [
        "# RESULTS — historique des évaluations",
        "",
        "Généré par `robot results`. **Ne pas éditer à la main.**",
        f"Mis à jour le {datetime.now(timezone.utc).isoformat(timespec='minutes')}.",
        "",
        f"Métrique de référence : **{HEADLINE[0]} hit@{HEADLINE[1]}** — "
        "le fichier contenant le bug est-il dans les 5 premiers du classement ?",
        "",
    ]

    if chart is not None:
        lines += [f"![Score par version]({chart.as_posix()})", ""]

    lines += ["## Historique", "", "| version | date | tâches | hit@5 | intervalle 95 % | écart au hasard |", "|---|---|---|---|---|---|"]
    for group in groups:
        usable = len(group.report.usable)
        hits = round(group.headline * usable)
        low, high = wilson(hits, usable)
        gap = group.headline - group.report.hit_at("random", 5)
        lines.append(
            f"| `{group.name}` | {group.created_at[:16]} | {usable} | **{group.headline:.0%}** | "
            f"{low:.0%} – {high:.0%} | {gap:+.0%} |"
        )

    if groups:
        last = groups[-1]
        lines += ["", f"## Détail du dernier run — `{last.name}`", "",
                  "| stratégie | " + " | ".join(f"hit@{k}" for k in HITS_AT) + " | MRR | intervalle 95 % (hit@5) |",
                  "|---|" + "---|" * (len(HITS_AT) + 2)]
        usable = len(last.report.usable)
        for name in STRATEGIES:
            score = last.report.hit_at(name, 5)
            low, high = wilson(round(score * usable), usable)
            cells = " | ".join(f"{last.report.hit_at(name, k):.0%}" for k in HITS_AT)
            lines.append(f"| {name} | {cells} | {last.report.mrr(name):.2f} | {low:.0%} – {high:.0%} |")

        lines += ["", f"*{usable} tâches — un écart de moins de "
                  f"{100 / max(usable, 1):.0f} points vaut une seule tâche.*", ""]

    return "\n".join(lines) + "\n"


def compare(before: Group, after: Group, strategy: str = "robot") -> dict:
    ranks_before = {r.task_id: r.ranks.get(strategy) for r in before.report.usable}
    ranks_after = {r.task_id: r.ranks.get(strategy) for r in after.report.usable}
    shared = sorted(set(ranks_before) & set(ranks_after))

    changes = {"improved": [], "regressed": [], "unchanged": []}
    for task_id in shared:
        old, new = ranks_before[task_id], ranks_after[task_id]
        bucket = "unchanged" if old == new else ("improved" if _better(new, old) else "regressed")
        changes[bucket].append((task_id, old, new))

    return {
        "strategy": strategy,
        "shared": len(shared),
        "only_before": sorted(set(ranks_before) - set(ranks_after)),
        "only_after": sorted(set(ranks_after) - set(ranks_before)),
        "delta": after.report.hit_at(strategy, 5) - before.report.hit_at(strategy, 5),
        **changes,
    }


def _better(new: int | None, old: int | None) -> bool:
    return (new or 10**9) < (old or 10**9)


def score_svg(groups: list[Group], width: int = 640, height: int = 220) -> str:
    if not groups:
        return "<svg xmlns='http://www.w3.org/2000/svg'></svg>"

    left, bottom, top = 50, height - 40, 20
    span = width - left - 20
    step = span / max(len(groups) - 1, 1)

    def y_of(value: float) -> float:
        return bottom - value * (bottom - top)

    points = [(left + index * step, y_of(group.headline)) for index, group in enumerate(groups)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
    random_line = y_of(sum(g.report.hit_at("random", 5) for g in groups) / len(groups))

    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='{width}' height='{height}'>",
        "<style>text{font:11px sans-serif;fill:#555}.t{font-weight:600;fill:#222}</style>",
        f"<text class='t' x='{left}' y='14'>hit@5 par version (stratégie {HEADLINE[0]})</text>",
    ]
    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y_of(value)
        parts.append(f"<line x1='{left}' y1='{y:.1f}' x2='{width - 20}' y2='{y:.1f}' stroke='#eee'/>")
        parts.append(f"<text x='{left - 8}' y='{y + 4:.1f}' text-anchor='end'>{value:.0%}</text>")

    parts.append(
        f"<line x1='{left}' y1='{random_line:.1f}' x2='{width - 20}' y2='{random_line:.1f}'"
        " stroke='#c00' stroke-dasharray='4 3'/>"
    )
    parts.append(f"<text x='{width - 22}' y='{random_line - 5:.1f}' text-anchor='end' fill='#c00'>hasard</text>")
    parts.append(f"<path d='{path}' fill='none' stroke='#2563eb' stroke-width='2'/>")

    for (x, y), group in zip(points, groups):
        parts.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='#2563eb'/>")
        parts.append(f"<text x='{x:.1f}' y='{y - 10:.1f}' text-anchor='middle' class='t'>{group.headline:.0%}</text>")
        parts.append(f"<text x='{x:.1f}' y='{bottom + 18:.1f}' text-anchor='middle'>{group.name[:14]}</text>")

    parts.append("</svg>")
    return "\n".join(parts)
