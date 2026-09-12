from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import APIError, ImageNotFound

from robot.install import plan_install
from robot.sandbox import Sandbox

CACHE_VERSION = 1
REPOSITORY = "robot-env"
CACHE_LABEL = "robot.cache"

DEPENDENCY_GLOBS = (
    "pyproject.toml", "setup.py", "setup.cfg", "uv.lock", "poetry.lock", "Pipfile.lock",
    "requirements*.txt", "constraints*.txt", "requirements/*.txt",
)


@dataclass
class CachedEnv:
    tag: str
    key: str
    size_mb: int
    created: str


def environment_key(repo_path: Path, image: str, plan: list[str] | None = None) -> str:
    plan = plan_install(repo_path) if plan is None else plan
    digest = hashlib.sha256()
    digest.update(f"cache-v{CACHE_VERSION}\n{image}\n{chr(10).join(plan)}\n".encode())

    for path in dependency_files(repo_path):
        digest.update(f"\n--- {path.relative_to(repo_path).as_posix()}\n".encode())
        digest.update(path.read_bytes())

    return digest.hexdigest()[:16]


def dependency_files(repo_path: Path) -> list[Path]:
    found = {
        path
        for pattern in DEPENDENCY_GLOBS
        for path in repo_path.glob(pattern)
        if path.is_file() and not path.is_symlink()
    }
    return sorted(found)


class EnvCache:
    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client or docker.from_env()

    def find(self, key: str) -> str | None:
        tag = f"{REPOSITORY}:{key}"
        try:
            self._client.images.get(tag)
        except (ImageNotFound, APIError):
            return None
        return tag

    def save(self, sandbox: Sandbox, key: str) -> str:
        return sandbox.commit(REPOSITORY, key, labels={CACHE_LABEL: "1"})

    def entries(self) -> list[CachedEnv]:
        entries = []
        for image in self._client.images.list(name=REPOSITORY):
            for tag in image.tags:
                entries.append(
                    CachedEnv(
                        tag=tag,
                        key=tag.split(":", 1)[-1],
                        size_mb=round(image.attrs.get("Size", 0) / 1e6),
                        created=image.attrs.get("Created", "")[:19],
                    )
                )
        return sorted(entries, key=lambda e: e.created, reverse=True)

    def remove(self, tag: str) -> None:
        self._client.images.remove(tag, force=True)

    def clear(self) -> int:
        entries = self.entries()
        for entry in entries:
            self.remove(entry.tag)
        return len(entries)
