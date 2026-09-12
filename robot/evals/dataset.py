from __future__ import annotations

import difflib
import hashlib
import json
import random
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from robot.env_cache import EnvCache, environment_key
from robot.evals.mutations import Mutation, apply_mutation, find_mutations
from robot.install import install
from robot.recon import detect_stack, run_tests
from robot.recon.stack import is_test_file
from robot.repo import clone_repo
from robot.sandbox import Sandbox

DEFAULT_DATASET_DIR = Path("evals/datasets/audit")
MAX_CANDIDATES = 400
Progress = Callable[[str], None]


@dataclass
class Task:
    id: str
    dataset: str
    source: str
    commit: str
    image: str
    bug: dict
    patch: str
    validation: dict = field(default_factory=dict)
    review: dict = field(default_factory=lambda: {"status": "unreviewed", "note": ""})

    def path_in(self, directory: Path) -> Path:
        return Path(directory) / f"{self.id}.json"

    def save(self, directory: Path) -> Path:
        path = self.path_in(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        return path


@dataclass
class BuildReport:
    status: str
    tasks: list[Task] = field(default_factory=list)
    killed: int = 0
    checked: int = 0
    duration: float = 0.0
    detail: str = ""

    def summary(self) -> str:
        if self.status != "ok":
            return f"{self.status} — {self.detail}"
        return (
            f"{len(self.tasks)} tâches gardées sur {self.checked} mutations testées "
            f"({self.killed} détectées par les tests) en {self.duration:.0f}s"
        )


def load_tasks(directory: Path = DEFAULT_DATASET_DIR) -> list[Task]:
    files = sorted(Path(directory).glob("*.json"))
    return [Task(**json.loads(path.read_text())) for path in files]


def build_audit_dataset(
    source: str,
    ref: str | None = None,
    limit: int = 5,
    seed: int = 0,
    max_per_file: int = 2,
    out: Path = DEFAULT_DATASET_DIR,
    on_progress: Progress = lambda step: None,
) -> BuildReport:
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="robot-dataset-") as tmp:
        on_progress("clone du repo")
        repo = clone_repo(source, Path(tmp) / "repo", ref=ref)
        stack = detect_stack(repo.path)

        cache = EnvCache()
        key = environment_key(repo.path, stack.image)
        cached_image = cache.find(key)

        with Sandbox(cached_image or stack.image, network=cached_image is None) as sandbox:
            on_progress("préparation de l'environnement")
            sandbox.run("rm -rf /workspace/* /workspace/.[!.]*")
            sandbox.copy_in(repo.path)

            if cached_image is None:
                report = install(sandbox, repo.path)
                if not report.ok:
                    return BuildReport("install_failed", detail="pip a échoué", duration=elapsed(started))
                cache.save(sandbox, key)
                sandbox.disable_network()

            on_progress("tests de référence")
            baseline = run_tests(sandbox, with_coverage=True)
            if baseline.status not in ("passed", "failed") or baseline.count("passed") == 0:
                return BuildReport("baseline_unusable", detail=baseline.summary(), duration=elapsed(started))
            baseline_failures = {case.id for case in baseline.problems}
            if baseline.coverage is None:
                return BuildReport("no_coverage", detail="couverture indisponible", duration=elapsed(started))

            covered = {f.path: set(f.executed_lines) for f in baseline.coverage.files}
            candidates = _candidates(repo.path, covered, seed)
            on_progress(f"{len(candidates)} mutations candidates sur des lignes couvertes")

            tasks, killed, checked = [], 0, 0
            used_lines: set[tuple[str, int]] = set()
            per_file: dict[str, int] = {}

            for relative_path, mutation in candidates:
                if len(tasks) >= limit:
                    break
                if (relative_path, mutation.line) in used_lines:
                    continue
                if per_file.get(relative_path, 0) >= max_per_file:
                    continue

                original = (repo.path / relative_path).read_text()
                mutated = apply_mutation(original, mutation)
                if mutated is None:
                    continue

                checked += 1
                on_progress(f"test {checked} : {relative_path} {mutation.describe()}")
                sandbox.write_file(relative_path, mutated)
                suite = run_tests(sandbox)
                sandbox.write_file(relative_path, original)

                if _survived(suite, baseline_failures):
                    used_lines.add((relative_path, mutation.line))
                    per_file[relative_path] = per_file.get(relative_path, 0) + 1
                    tasks.append(_task(source, repo.commit, stack.image, relative_path, mutation,
                                       original, mutated, baseline, suite, baseline_failures))
                else:
                    killed += 1

    for task in tasks:
        task.save(out)

    return BuildReport("ok", tasks=tasks, killed=killed, checked=checked, duration=elapsed(started))


def _candidates(repo_path: Path, covered: dict[str, set[int]], seed: int) -> list[tuple[str, Mutation]]:
    candidates = []
    for relative_path, lines in sorted(covered.items()):
        path = repo_path / relative_path
        if not path.is_file() or path.is_symlink() or is_test_file(Path(relative_path)):
            continue
        source = path.read_text(errors="replace")
        candidates += [(relative_path, m) for m in find_mutations(source) if m.line in lines]

    random.Random(seed).shuffle(candidates)
    return candidates[:MAX_CANDIDATES]


def _survived(suite, baseline_failures: set[str]) -> bool:
    if suite.status not in ("passed", "failed"):
        return False
    return {case.id for case in suite.problems} <= baseline_failures


def _task(source, commit, image, relative_path, mutation, original, mutated,
          baseline, suite, baseline_failures) -> Task:
    digest = hashlib.sha256(
        f"{commit}{relative_path}{mutation.line}{mutation.col}{mutation.after}".encode()
    ).hexdigest()[:6]
    patch = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True), mutated.splitlines(keepends=True),
            fromfile=f"a/{relative_path}", tofile=f"b/{relative_path}", n=3,
        )
    )
    return Task(
        id=f"{source.rstrip('/').split('/')[-1]}-{digest}",
        dataset="audit",
        source=source,
        commit=commit,
        image=image,
        bug={
            "file": relative_path, "line": mutation.line, "col": mutation.col,
            "kind": mutation.kind, "before": mutation.before, "after": mutation.after,
            "description": mutation.describe(),
        },
        patch=patch,
        validation={
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tests_before": f"{baseline.status} ({baseline.count('passed')} passed)",
            "tests_after": f"{suite.status} ({suite.count('passed')} passed)",
            "baseline_failures": sorted(baseline_failures),
            "survived": True,
        },
        review={"status": "unreviewed", "note": ""},
    )


def elapsed(started: float) -> float:
    return round(time.monotonic() - started, 1)
