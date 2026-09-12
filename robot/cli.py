from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Annotated

import typer

from robot.audit import audit_repo
from robot.env_cache import EnvCache
from robot.evals import (DEFAULT_CHART, DEFAULT_DB, DEFAULT_FIX_DIR, DEFAULT_RESULTS, Run, Store,
                         build_audit_dataset, build_fix_dataset, compare, group_from_runs,
                         load_tasks, result_from_metrics, results_markdown, run_dataset, score_svg)
from robot.evals.dataset import DEFAULT_DATASET_DIR
from robot.recon.hotspots import DEFAULT_SINCE
from robot.repo import CloneError

app = typer.Typer(add_completion=False, help="Audit et correction de repos dans un sandbox Docker.")


@app.callback()
def main() -> None:
    """Robot : audit et correction de repos dans un sandbox Docker."""


@app.command()
def audit(
    source: Annotated[str, typer.Argument(help="URL GitHub ou chemin local du repo")],
    ref: Annotated[str | None, typer.Option(help="Branche, tag ou commit à auditer")] = None,
    since: Annotated[str, typer.Option(help="Fenêtre d'historique git")] = DEFAULT_SINCE,
    top: Annotated[int, typer.Option(help="Nombre de fichiers prioritaires")] = 10,
    json_out: Annotated[Path | None, typer.Option("--json", help="Écrit le rapport JSON")] = None,
    md_out: Annotated[Path | None, typer.Option("--md", help="Écrit le rapport markdown")] = None,
    db: Annotated[Path, typer.Option(help="Base des runs")] = DEFAULT_DB,
    record: Annotated[bool, typer.Option(help="Enregistre le run dans la base")] = True,
    cache: Annotated[bool, typer.Option(help="Réutilise l'environnement installé")] = True,
) -> None:
    """Analyse un repo sans issue et produit un rapport."""
    started = time.monotonic()

    def progress(step: str) -> None:
        typer.secho(f"[{time.monotonic() - started:5.1f}s] {step}", fg=typer.colors.BRIGHT_BLACK, err=True)

    try:
        report = audit_repo(source, ref=ref, since=since, risks_limit=top,
                            use_cache=cache, on_progress=progress)
    except CloneError as e:
        typer.secho(f"Clone impossible : {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from e

    markdown = report.to_markdown()
    if md_out:
        md_out.write_text(markdown)
        typer.secho(f"Rapport markdown : {md_out}", fg=typer.colors.GREEN, err=True)
    else:
        sys.stdout.write(markdown + "\n")

    if json_out:
        json_out.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        typer.secho(f"Rapport JSON : {json_out}", fg=typer.colors.GREEN, err=True)

    if record:
        with Store(db) as store:
            run_id = store.save(Run(
                kind="audit",
                source=source,
                commit_sha=report.commit,
                status="degraded" if report.warnings() else "ok",
                duration=report.duration,
                environment=report.environment,
                params={"ref": ref, "since": since, "top": top, "cache": report.cache},
                metrics=report.metrics(),
                steps=report.timings,
            ))
        typer.secho(f"Run enregistré : {run_id[:12]} (robot show {run_id[:12]})", fg=typer.colors.GREEN, err=True)


if __name__ == "__main__":
    app()


@app.command()
def runs(
    limit: Annotated[int, typer.Option(help="Nombre de runs affichés")] = 20,
    kind: Annotated[str | None, typer.Option(help="Filtre par type de run")] = None,
    db: Annotated[Path, typer.Option(help="Base des runs")] = DEFAULT_DB,
) -> None:
    """Liste les runs enregistrés."""
    with Store(db) as store:
        found = store.recent(limit=limit, kind=kind)

    if not found:
        typer.echo("Aucun run enregistré.")
        return

    typer.echo(f"{'id':12}  {'date':17}  {'type':6}  {'statut':9}  {'durée':>6}  source")
    for run in found:
        typer.echo(
            f"{run.id[:12]}  {run.created_at[:16]}  {run.kind:6}  {run.status:9}  "
            f"{run.duration:5.0f}s  {run.source}"
        )


@app.command()
def show(
    run_id: Annotated[str, typer.Argument(help="Identifiant du run (préfixe accepté)")],
    db: Annotated[Path, typer.Option(help="Base des runs")] = DEFAULT_DB,
) -> None:
    """Affiche le détail d'un run."""
    with Store(db) as store:
        run = store.get(run_id)

    if run is None:
        typer.secho(f"Run introuvable : {run_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo(f"{run.kind} {run.id}  {run.created_at}  [{run.status}]")
    typer.echo(f"  source   : {run.source} @ {run.commit_sha[:12]}")
    typer.echo(f"  durée    : {run.duration:.1f}s   coût : ${run.cost_usd:.4f}")
    typer.echo(f"  params   : {json.dumps(run.params, ensure_ascii=False)}")
    typer.echo("  env      : " + "  ".join(f"{k}={v}" for k, v in sorted(run.environment.items())))
    typer.echo("  métriques :")
    for key, value in run.metrics.items():
        typer.echo(f"    {key:18} {value}")
    if run.steps:
        typer.echo("  étapes   :")
        for name, duration in run.steps:
            typer.echo(f"    {duration:6.1f}s  {name}")


@app.command()
def cache(
    clear: Annotated[bool, typer.Option("--clear", help="Supprime tous les environnements en cache")] = False,
) -> None:
    """Liste ou vide les environnements Docker mis en cache."""
    store = EnvCache()
    if clear:
        removed = store.clear()
        typer.secho(f"{removed} environnement(s) supprimé(s).", fg=typer.colors.GREEN)
        return

    entries = store.entries()
    if not entries:
        typer.echo("Aucun environnement en cache.")
        return

    typer.echo(f"{'clé':18}  {'taille':>7}  créé")
    for entry in entries:
        typer.echo(f"{entry.key:18}  {entry.size_mb:5} Mo  {entry.created}")
    typer.echo(f"\nTotal : {sum(e.size_mb for e in entries)} Mo sur {len(entries)} image(s)")


dataset_app = typer.Typer(help="Construire et inspecter les datasets d'évaluation.")
app.add_typer(dataset_app, name="dataset")


@dataset_app.command("build")
def dataset_build(
    source: Annotated[str, typer.Argument(help="URL GitHub ou chemin local du repo")],
    tasks: Annotated[int, typer.Option(help="Nombre de tâches à produire")] = 5,
    ref: Annotated[str | None, typer.Option(help="Branche, tag ou commit")] = None,
    seed: Annotated[int, typer.Option(help="Graine aléatoire (reproductibilité)")] = 0,
    out: Annotated[Path, typer.Option(help="Dossier de sortie")] = DEFAULT_DATASET_DIR,
) -> None:
    """Injecte des bugs dans un repo sain et garde ceux que ses tests ne détectent pas."""
    started = time.monotonic()

    def progress(step: str) -> None:
        typer.secho(f"[{time.monotonic() - started:5.1f}s] {step}", fg=typer.colors.BRIGHT_BLACK, err=True)

    try:
        report = build_audit_dataset(source, ref=ref, limit=tasks, seed=seed, out=out, on_progress=progress)
    except CloneError as e:
        typer.secho(f"Clone impossible : {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from e

    color = typer.colors.GREEN if report.status == "ok" else typer.colors.RED
    typer.secho(report.summary(), fg=color)
    for task in report.tasks:
        typer.echo(f"  {task.id}  {task.bug['file']}:{task.bug['line']}  {task.bug['description']}")
    if report.status != "ok":
        raise typer.Exit(code=1)


@dataset_app.command("list")
def dataset_list(
    directory: Annotated[Path, typer.Option("--dir", help="Dossier du dataset")] = DEFAULT_DATASET_DIR,
) -> None:
    """Liste les tâches d'un dataset."""
    found = load_tasks(directory)
    if not found:
        typer.echo(f"Aucune tâche dans {directory}.")
        return

    typer.echo(f"{'id':24}  {'fichier':40}  bug")
    for task in found:
        location = f"{task.bug['file']}:{task.bug['line']}"
        typer.echo(f"{task.id:24}  {location:40}  {task.bug['before']} → {task.bug['after']}")
    typer.echo(f"\n{len(found)} tâches dans {directory}")


@dataset_app.command("review")
def dataset_review(
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche")],
    status: Annotated[str, typer.Argument(help="keep, drop ou unreviewed")],
    note: Annotated[str, typer.Option(help="Pourquoi")] = "",
    directory: Annotated[Path, typer.Option("--dir", help="Dossier du dataset")] = DEFAULT_DATASET_DIR,
) -> None:
    """Valide ou rejette une tâche après inspection humaine (drop supprime le fichier)."""
    task = next((t for t in load_tasks(directory) if t.id == task_id or t.id.endswith(task_id)), None)
    if task is None:
        typer.secho(f"Tâche introuvable : {task_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if status == "drop":
        task.path_in(directory).unlink()
        typer.secho(f"{task.id} supprimée ({note or 'sans note'})", fg=typer.colors.YELLOW)
        return

    task.review = {"status": status, "note": note}
    task.save(directory)
    typer.secho(f"{task.id} → {status}", fg=typer.colors.GREEN)


@dataset_app.command("show")
def dataset_show(
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche")],
    directory: Annotated[Path, typer.Option("--dir", help="Dossier du dataset")] = DEFAULT_DATASET_DIR,
) -> None:
    """Affiche une tâche et le patch du bug injecté."""
    task = next((t for t in load_tasks(directory) if t.id == task_id or t.id.endswith(task_id)), None)
    if task is None:
        typer.secho(f"Tâche introuvable : {task_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo(f"{task.id}  [{task.review['status']}]")
    typer.echo(f"  repo  : {task.source} @ {task.commit[:12]}")
    typer.echo(f"  bug   : {task.bug['file']}:{task.bug['line']}  {task.bug['description']}")
    typer.echo(f"  tests : avant {task.validation['tests_before']} / après {task.validation['tests_after']}")
    typer.echo(task.patch)


@app.command("eval")
def evaluate(
    directory: Annotated[Path, typer.Option("--dir", help="Dossier du dataset")] = DEFAULT_DATASET_DIR,
    limit: Annotated[int, typer.Option(help="Nombre maximum de tâches")] = 0,
    seed: Annotated[int, typer.Option(help="Graine (stratégie aléatoire)")] = 0,
    db: Annotated[Path, typer.Option(help="Base des runs")] = DEFAULT_DB,
    record: Annotated[bool, typer.Option(help="Enregistre les résultats")] = True,
    workers: Annotated[int, typer.Option(help="Tâches en parallèle")] = 1,
    retry: Annotated[int, typer.Option(help="Nouvelles tentatives sur erreur d'infrastructure")] = 0,
    resume: Annotated[bool, typer.Option("--resume", help="Ignore les tâches déjà réussies")] = False,
    label: Annotated[str, typer.Option(help="Nom de cette version (ex: v1-baseline)")] = "",
) -> None:
    """Mesure les stratégies de classement sur un dataset de bugs injectés."""
    tasks = [t for t in load_tasks(directory) if t.review["status"] != "drop"]
    if limit:
        tasks = tasks[:limit]
    if not tasks:
        typer.secho(f"Aucune tâche dans {directory}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    started = time.monotonic()

    def progress(step: str) -> None:
        typer.secho(f"[{time.monotonic() - started:6.1f}s] {step}", fg=typer.colors.BRIGHT_BLACK, err=True)

    skip = set()
    if resume:
        with Store(db) as store:
            skip = store.completed_tasks("audit", seed)

    report = run_dataset(tasks, seed=seed, workers=workers, retry=retry, skip=skip, on_progress=progress)
    fresh = list(report.results)

    if resume and skip:
        with Store(db) as store:
            wanted = {t.id for t in tasks}
            report.results = [
                result_from_metrics(run.task_id, run.source, run.status, run.duration, run.metrics)
                for run in store.results_for("audit", seed)
                if run.task_id in wanted
            ] + report.results

    typer.echo("")
    typer.secho(report.summary(), fg=typer.colors.GREEN)
    typer.echo(report.table())
    for result in report.results:
        if not result.usable:
            typer.secho(f"  ignorée : {result.task_id} — {result.status} {result.detail}",
                        fg=typer.colors.YELLOW)

    if record:
        group_id = uuid.uuid4().hex
        with Store(db) as store:
            for result in fresh:
                store.save(Run(
                    kind="eval", dataset="audit", task_id=result.task_id,
                    source=result.bug_file, status=result.status, duration=result.duration,
                    metrics={"ranks": result.ranks, "candidates": result.candidates},
                    params={"seed": seed}, label=label, group_id=group_id,
                ))
        typer.secho(f"Groupe {label or group_id[:8]} — `robot results` pour mettre à jour RESULTS.md",
                    fg=typer.colors.GREEN, err=True)
        typer.secho(f"{len(fresh)} résultats enregistrés dans {db}", fg=typer.colors.GREEN, err=True)


@dataset_app.command("build-fix")
def dataset_build_fix(
    source: Annotated[str, typer.Argument(help="URL GitHub ou chemin local du repo")],
    tasks: Annotated[int, typer.Option(help="Nombre de tâches à produire")] = 3,
    since: Annotated[str, typer.Option(help="Fenêtre d'historique git")] = "5 years ago",
    candidates: Annotated[int, typer.Option(help="Commits candidats testés au maximum")] = 15,
    out: Annotated[Path, typer.Option(help="Dossier de sortie")] = DEFAULT_FIX_DIR,
) -> None:
    """Fabrique des tâches en annulant de vrais correctifs (le test reste, le fix saute)."""
    started = time.monotonic()

    def progress(step: str) -> None:
        typer.secho(f"[{time.monotonic() - started:6.1f}s] {step}", fg=typer.colors.BRIGHT_BLACK, err=True)

    try:
        report = build_fix_dataset(source, limit=tasks, since=since, max_candidates=candidates,
                                   out=out, on_progress=progress)
    except CloneError as e:
        typer.secho(f"Clone impossible : {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from e

    typer.secho(f"{len(report.tasks)} tâches gardées sur {report.checked} commits testés "
                f"en {report.duration:.0f}s", fg=typer.colors.GREEN)
    for task in report.tasks:
        typer.echo(f"  {task.id}  {task.bug['description'][:50]}")
        typer.echo(f"      fichiers : {', '.join(task.bug['files'])}")
        typer.echo(f"      tests rouge→vert : {len(task.validation['fail_to_pass'])} "
                   f"(dont {task.validation['fail_to_pass'][0]})")


@app.command()
def results(
    out: Annotated[Path, typer.Option(help="Fichier markdown à écrire")] = DEFAULT_RESULTS,
    chart: Annotated[Path, typer.Option(help="Graphe SVG à écrire")] = DEFAULT_CHART,
    db: Annotated[Path, typer.Option(help="Base des runs")] = DEFAULT_DB,
) -> None:
    """Regénère RESULTS.md et le graphe depuis la base des runs."""
    with Store(db) as store:
        groups = [group_from_runs(store.runs_in_group(g["group_id"])) for g in store.groups(dataset="audit")]

    if not groups:
        typer.secho("Aucun groupe d'eval enregistré (lance `robot eval --label ...`).",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    chart.parent.mkdir(parents=True, exist_ok=True)
    chart.write_text(score_svg(groups))
    out.write_text(results_markdown(groups, chart=chart))

    typer.secho(f"{out} et {chart} mis à jour ({len(groups)} version(s))", fg=typer.colors.GREEN)
    for group in groups:
        typer.echo(f"  {group.name:20} {group.headline:.0%}  ({len(group.report.usable)} tâches)")


@app.command("compare")
def compare_groups(
    before: Annotated[str, typer.Argument(help="Version de référence (label ou id)")],
    after: Annotated[str, typer.Argument(help="Version à comparer")],
    strategy: Annotated[str, typer.Option(help="Stratégie comparée")] = "robot",
    db: Annotated[Path, typer.Option(help="Base des runs")] = DEFAULT_DB,
) -> None:
    """Compare deux runs d'eval, tâche par tâche."""
    with Store(db) as store:
        ids = [store.find_group(name) for name in (before, after)]
        for name, found in zip((before, after), ids):
            if found is None:
                typer.secho(f"Version introuvable : {name}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
        groups = [group_from_runs(store.runs_in_group(group_id)) for group_id in ids]

    diff = compare(groups[0], groups[1], strategy=strategy)
    typer.echo(f"{groups[0].name} → {groups[1].name}  (stratégie {strategy}, {diff['shared']} tâches communes)")
    typer.secho(f"  hit@5 : {groups[0].report.hit_at(strategy, 5):.0%} → "
                f"{groups[1].report.hit_at(strategy, 5):.0%}  ({diff['delta']:+.0%})",
                fg=typer.colors.GREEN if diff["delta"] >= 0 else typer.colors.RED)

    for bucket, symbol, color in (("improved", "✅", typer.colors.GREEN),
                                  ("regressed", "❌", typer.colors.RED),
                                  ("unchanged", "➖", typer.colors.BRIGHT_BLACK)):
        rows = diff[bucket]
        typer.secho(f"  {symbol} {bucket} : {len(rows)}", fg=color)
        if bucket != "unchanged":
            for task_id, old, new in rows:
                typer.echo(f"      {task_id:24} rang {old} → {new}")

    for key, message in (("only_before", "absentes du second run"), ("only_after", "nouvelles tâches")):
        if diff[key]:
            typer.secho(f"  ⚠️  {len(diff[key])} {message} : {', '.join(diff[key][:5])}",
                        fg=typer.colors.YELLOW)
