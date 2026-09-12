import pytest

from robot.env_cache import EnvCache
from robot.install import PIP_ENV, TOOLS_CMD
from robot.sandbox import Sandbox


@pytest.fixture(scope="session")
def tool_sandbox():
    with Sandbox(network=True) as sb:
        assert sb.run(TOOLS_CMD, timeout=300, env=PIP_ENV).ok
        sb.disable_network()
        yield sb


@pytest.fixture
def workspace(tool_sandbox):
    def load(files: dict[str, str]) -> Sandbox:
        tool_sandbox.run("rm -rf /workspace/* /workspace/.[!.]*")
        for name, content in files.items():
            tool_sandbox.run(f"mkdir -p $(dirname {name}) && cat > {name} <<'EOF'\n{content}\nEOF")
        return tool_sandbox

    return load


@pytest.fixture
def cache():
    store = EnvCache()
    before = {entry.tag for entry in store.entries()}
    yield store
    for entry in store.entries():
        if entry.tag not in before:
            store.remove(entry.tag)
