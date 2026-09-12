from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from robot.audit.report import AuditReport
from robot.audit.risks import compute_risks
from robot.env_cache import EnvCache, environment_key, lock_for
from robot.fingerprint import host_environment, sandbox_environment
from robot.install import InstallReport, install
from robot.recon import detect_stack, find_hotspots, run_lint, run_tests
from robot.recon.hotspots import DEFAULT_SINCE
from robot.repo import clone_repo
from robot.sandbox import Sandbox

Progress = Callable[[str], None]


def audit_repo(
    source: str,
    ref: str | None = None,
    since: str = DEFAULT_SINCE,
    risks_limit: int = 10,
    use_cache: bool = True,
    prepare: Callable[[Path], None] | None = None,
    on_progress: Progress = lambda step: None,
) -> AuditReport:
    started = time.monotonic()
    marks: list[tuple[str, float]] = []

    def step(name: str) -> None:
        marks.append((name, time.monotonic()))
        on_progress(name)

    with tempfile.TemporaryDirectory(prefix="robot-") as tmp:
        step("clone du repo")
        repo = clone_repo(source, Path(tmp) / "repo", ref=ref)

        if prepare is not None:
            step("préparation du repo (injection)")
            prepare(repo.path)

        step("détection de la stack")
        stack = detect_stack(repo.path)

        step("analyse de l'historique git")
        hotspots = find_hotspots(repo.path, since=since)

        cache = EnvCache() if use_cache else None
        key = environment_key(repo.path, stack.image)
        install_report = InstallReport()
        cached_image, built_here = None, False

        if cache is not None:
            with lock_for(key):
                cached_image = cache.find(key)
                if cached_image is None:
                    step("construction de l'environnement")
                    cached_image, install_report = _build_environment(cache, key, stack.image, repo.path)
                    built_here = True

        with Sandbox(cached_image or stack.image, network=cached_image is None) as sandbox:
            step(f"copie dans le sandbox ({'cache ' + key if cached_image else stack.image})")
            sandbox.run("rm -rf /workspace/* /workspace/.[!.]*")
            sandbox.copy_in(repo.path)

            if cached_image is None:
                if not built_here:
                    step("installation des dépendances")
                    install_report = install(sandbox, repo.path)
                step("coupure du réseau")
                sandbox.disable_network()

            step("lint")
            lint = run_lint(sandbox)

            step("tests + couverture")
            tests = run_tests(sandbox, with_coverage=True)
            environment = host_environment() | sandbox_environment(sandbox)

        step("croisement des signaux")
        duration = time.monotonic() - started
        return AuditReport(
            source=source,
            commit=repo.commit,
            created_at=AuditReport.now(),
            duration=duration,
            stack=stack,
            install=install_report,
            tests=tests,
            lint=lint,
            hotspots=hotspots,
            risks=compute_risks(hotspots, tests.coverage, lint, limit=risks_limit),
            cache="off" if cache is None else ("miss" if built_here else "hit"),
            cache_key=key,
            timings=_durations(marks, started + duration),
            environment=environment,
        )


def _build_environment(cache: EnvCache, key: str, image: str, repo_path: Path) -> tuple[str | None, InstallReport]:
    with Sandbox(image, network=True) as builder:
        builder.copy_in(repo_path)
        report = install(builder, repo_path)
        if not report.ok:
            return None, report
        return cache.save(builder, key), report


def _durations(marks: list[tuple[str, float]], end: float) -> list[tuple[str, float]]:
    return [
        (name, round((marks[i + 1][1] if i + 1 < len(marks) else end) - start, 2))
        for i, (name, start) in enumerate(marks)
    ]
