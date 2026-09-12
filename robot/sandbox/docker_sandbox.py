from __future__ import annotations

import io
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.models.containers import Container

DEFAULT_IMAGE = "python:3.12-slim"
WORKDIR = "/workspace"

MAX_OUTPUT_CHARS = 20_000
MAX_READ_BYTES = 10_000_000

@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    skipped = len(text) - limit
    return f"{text[:half]}\n\n[... {skipped} caractères tronqués ...]\n\n{text[-half:]}"


class Sandbox:
    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        *,
        network: bool = False,
        mem_limit: str = "2g",
        cpus: float = 2.0,
    ) -> None:
        self.image = image
        self.network = network
        self.mem_limit = mem_limit
        self.cpus = cpus
        self._client = docker.from_env()
        self._container: Container | None = None

    def start(self) -> Sandbox:
        self._container = self._client.containers.run(
            self.image,

            command=["sleep", "infinity"],
            detach=True,
            working_dir=WORKDIR,
            network_mode="bridge" if self.network else "none",
            mem_limit=self.mem_limit,
            nano_cpus=int(self.cpus * 1e9),
            pids_limit=512,
            security_opt=["no-new-privileges"],
            labels={"robot.sandbox": "1"},
        )
        return self

    def close(self) -> None:
        if self._container is not None:
            self._container.remove(force=True)
            self._container = None

    def __enter__(self) -> Sandbox:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def container_id(self) -> str:
        return self._require().id

    def disable_network(self) -> None:
        if not self.network:
            return
        container = self._require()
        container.reload()
        for name in list(container.attrs["NetworkSettings"]["Networks"]):
            self._client.networks.get(name).disconnect(container)
        self.network = False

    def run(self, cmd: str, timeout: int = 60, env: dict[str, str] | None = None) -> ExecResult:
        container = self._require()
        wrapped = ["timeout", "--kill-after=5", str(timeout), "bash", "-c", cmd]

        start = time.monotonic()
        exit_code, (stdout, stderr) = container.exec_run(
            wrapped, demux=True, workdir=WORKDIR, environment=env
        )
        duration = time.monotonic() - start

        return ExecResult(
            exit_code=exit_code,
            stdout=truncate((stdout or b"").decode(errors="replace")),
            stderr=truncate((stderr or b"").decode(errors="replace")),
            duration=round(duration, 2),
            timed_out=exit_code == 124 or duration >= timeout,
        )

    def copy_in(self, src: Path, dest: str = WORKDIR, exclude: tuple[str, ...] = (".git",)) -> None:
        def skip_excluded(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            return None if any(part in exclude for part in Path(info.name).parts) else info

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.add(src, arcname=".", filter=skip_excluded)
        self._require().put_archive(dest, buffer.getvalue())

    def write_file(self, path: str, content: str) -> None:
        target = Path(path if path.startswith("/") else f"{WORKDIR}/{path}")
        data = content.encode()
        info = tarfile.TarInfo(name=target.name)
        info.size = len(data)
        info.mtime = int(time.time())

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.addfile(info, io.BytesIO(data))
        self._require().put_archive(str(target.parent), buffer.getvalue())

    def commit(self, repository: str, tag: str, labels: dict[str, str] | None = None) -> str:
        changes = "\n".join(f"LABEL {key}={value}" for key, value in (labels or {}).items())
        self._require().commit(repository=repository, tag=tag, changes=changes or None)
        return f"{repository}:{tag}"

    def read_file(self, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
        try:
            stream, stat = self._require().get_archive(path)
        except docker.errors.NotFound as e:
            raise FileNotFoundError(path) from e
        if stat["size"] > max_bytes:
            raise ValueError(f"{path} fait {stat['size']} octets (max {max_bytes})")

        with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
            member = tar.next()
            file = tar.extractfile(member) if member and member.isfile() else None
            if file is None:
                raise FileNotFoundError(f"{path} n'est pas un fichier normal")
            return file.read().decode(errors="replace")

    def _require(self) -> Container:
        if self._container is None:
            raise RuntimeError("Sandbox non démarrée : appelle start() ou utilise `with Sandbox()`.")
        return self._container
