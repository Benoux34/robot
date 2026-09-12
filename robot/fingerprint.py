from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

import docker

from robot.sandbox import Sandbox

VERSION_CMDS = {
    "python": "python --version",
    "pytest": "python -m pytest --version",
    "ruff": "ruff --version",
    "coverage": "python -m coverage --version",
}


def host_environment() -> dict[str, str]:
    return {
        "robot": _package_version("robot"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "docker": _docker_version(),
    }


def sandbox_environment(sandbox: Sandbox) -> dict[str, str]:
    versions = {"image": sandbox.image}
    for name, cmd in VERSION_CMDS.items():
        result = sandbox.run(cmd, timeout=30)
        versions[name] = _first_version(result.stdout + result.stderr) if result.ok else "absent"
    return versions


def _first_version(output: str) -> str:
    for word in output.split():
        if word[:1].isdigit() and "." in word:
            return word.strip(",()")
    return output.strip().splitlines()[0] if output.strip() else "inconnu"


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "dev"


def _docker_version() -> str:
    try:
        return docker.from_env().version().get("Version", "inconnu")
    except Exception:
        return "indisponible"
