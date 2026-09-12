from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT = 300


class CloneError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalRepo:
    path: Path
    commit: str


def clone_repo(source: str, dest: Path, ref: str | None = None) -> LocalRepo:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise CloneError(f"{dest} n'est pas vide")

    _git("clone", "--filter=blob:none", "--no-recurse-submodules", source, str(dest))
    if ref:
        _git("checkout", "--quiet", ref, cwd=dest)

    commit = _git("rev-parse", "HEAD", cwd=dest).strip()
    return LocalRepo(path=dest, commit=commit)


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise CloneError(f"git {args[0]} : timeout après {GIT_TIMEOUT}s") from e

    if result.returncode != 0:
        raise CloneError(f"git {args[0]} a échoué : {result.stderr.strip()}")
    return result.stdout
