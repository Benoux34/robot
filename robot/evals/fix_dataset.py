from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from robot.env_cache import EnvCache, environment_key
from robot.evals.dataset import BuildReport, Task, elapsed
from robot.install import install
from robot.recon import detect_stack, run_tests
from robot.recon.hotspots import FIX_PATTERN
from robot.recon.stack import LANGUAGES, is_test_file
from robot.repo import clone_repo
from robot.sandbox import Sandbox

DEFAULT_FIX_DIR = Path("evals/datasets/fix")
Progress = Callable[[str], None]


@dataclass
class Candidate:
    commit: str
    parent: str
    subject: str
    code_files: list[str]
    test_files: list[str]


def build_fix_dataset(
    source: str,
    limit: int = 3,
    since: str = "5 years ago",
    max_candidates: int = 15,
    out: Path = DEFAULT_FIX_DIR,
    on_progress: Progress = lambda step: None,
) -> BuildReport:
    started = time.monotonic()
    tasks: list[Task] = []
    checked = 0

    with tempfile.TemporaryDirectory(prefix="robot-fix-") as tmp:
        on_progress("clone du repo")
        repo = clone_repo(source, Path(tmp) / "repo")
        candidates = find_candidates(repo.path, since)[:max_candidates]
        on_progress(f"{len(candidates)} commits candidats (code + tests modifiés)")

        for candidate in candidates:
            if len(tasks) >= limit:
                break
            checked += 1
            on_progress(f"essai {checked} : {candidate.commit[:8]} {candidate.subject[:60]}")

            try:
                task = _validate(repo.path, candidate, source, on_progress)
            except subprocess.CalledProcessError as e:
                on_progress(f"    git a échoué : {e}")
                continue

            if task is not None:
                tasks.append(task)

    for task in tasks:
        task.save(out)

    return BuildReport("ok", tasks=tasks, killed=checked - len(tasks), checked=checked,
                       duration=elapsed(started))


def find_candidates(repo_path: Path, since: str) -> list[Candidate]:
    log = _git(repo_path, "log", f"--since={since}", "--no-merges", "--name-status",
               "--pretty=format:\x01%H\x02%P\x02%s")
    candidates, commit, parent, subject, code, tests = [], "", "", "", [], []

    def flush() -> None:
        if commit and parent and code and tests:
            candidates.append(Candidate(commit, parent, subject, sorted(code), sorted(tests)))

    for line in log.splitlines():
        if line.startswith("\x01"):
            flush()
            commit, parents, subject = line[1:].split("\x02", 2)
            parent = parents.split()[0] if parents.split() else ""
            code, tests = [], []
        elif line.strip():
            status, _, path = line.partition("\t")
            if not path.endswith(".py") or Path(path).suffix not in LANGUAGES:
                continue
            if is_test_file(Path(path)):
                tests.append(path)
            elif status == "M":
                code.append(path)
            else:
                code.append("__unsupported__")

    flush()
    usable = [c for c in candidates if "__unsupported__" not in c.code_files]
    return sorted(usable, key=lambda c: not FIX_PATTERN.search(c.subject))


def _validate(repo_path: Path, candidate: Candidate, source: str, on_progress: Progress) -> Task | None:
    _git(repo_path, "checkout", "--quiet", "--force", candidate.commit)
    stack = detect_stack(repo_path)

    cache = EnvCache()
    key = environment_key(repo_path, stack.image)
    cached_image = cache.find(key)

    with Sandbox(cached_image or stack.image, network=cached_image is None) as sandbox:
        _git(repo_path, "checkout", "--quiet", candidate.parent, "--", *candidate.code_files)
        _load(sandbox, repo_path)

        if cached_image is None:
            report = install(sandbox, repo_path)
            if not report.ok:
                on_progress("    installation impossible")
                return None
            cache.save(sandbox, key)
            sandbox.disable_network()

        broken = run_tests(sandbox)
        _git(repo_path, "checkout", "--quiet", candidate.commit, "--", *candidate.code_files)
        _load(sandbox, repo_path)
        fixed = run_tests(sandbox)

    broken_failures = {case.id for case in broken.problems}
    fixed_failures = {case.id for case in fixed.problems}
    fail_to_pass = sorted(broken_failures - fixed_failures)

    if not fail_to_pass:
        on_progress("    rejeté : les tests ne détectent pas l'absence du correctif")
        return None
    if not fixed_failures <= broken_failures:
        on_progress("    rejeté : le correctif casse d'autres tests")
        return None

    patch = _git(repo_path, "diff", candidate.parent, candidate.commit, "--", *candidate.code_files)
    on_progress(f"    gardé : {len(fail_to_pass)} test(s) passent de rouge à vert")

    return Task(
        id=f"{source.rstrip('/').split('/')[-1]}-fix-{candidate.commit[:6]}",
        dataset="fix",
        source=source,
        commit=candidate.commit,
        image=stack.image,
        bug={
            "files": candidate.code_files,
            "parent": candidate.parent,
            "description": candidate.subject,
            "tests": candidate.test_files,
        },
        patch=patch,
        validation={
            "fail_to_pass": fail_to_pass,
            "pass_to_pass": fixed.count("passed"),
            "tests_broken": broken.summary(),
            "tests_fixed": fixed.summary(),
        },
    )


def _load(sandbox: Sandbox, repo_path: Path) -> None:
    sandbox.run("rm -rf /workspace/* /workspace/.[!.]*")
    sandbox.copy_in(repo_path)


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo_path, capture_output=True,
                            text=True, timeout=300, check=True)
    return result.stdout
