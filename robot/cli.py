from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from robot.audit import audit_repo
from robot.evals import DEFAULT_DB, Run, Store
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
) -> None:
    """Analyse un repo sans issue et produit un rapport."""
    started = time.monotonic()

    def progress(step: str) -> None:
        typer.secho(f"[{time.monotonic() - started:5.1f}s] {step}", fg=typer.colors.BRIGHT_BLACK, err=True)

    try:
        report = audit_repo(source, ref=ref, since=since, risks_limit=top, on_progress=progress)
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
                params={"ref": ref, "since": since, "top": top},
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
