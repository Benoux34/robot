import docker
import pytest

from robot.sandbox import Sandbox
from robot.sandbox.docker_sandbox import truncate

@pytest.fixture(scope="module")
def sandbox():
    with Sandbox() as sb:
        yield sb


def test_run_returns_stdout(sandbox):
    result = sandbox.run("echo hello")
    assert result.ok
    assert result.stdout == "hello\n"


def test_exit_code_and_stderr(sandbox):
    result = sandbox.run("echo oops >&2; exit 3")
    assert result.exit_code == 3
    assert result.stderr == "oops\n"
    assert not result.ok


def test_runs_in_workdir(sandbox):
    assert sandbox.run("pwd").stdout.strip() == "/workspace"


def test_network_is_disabled(sandbox):
    result = sandbox.run(
        "python -c \"import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=3)\""
    )
    assert result.exit_code != 0


def test_timeout_kills_long_command(sandbox):
    result = sandbox.run("sleep 30", timeout=1)
    assert result.timed_out
    assert result.duration < 10


def test_container_is_removed_on_exit():
    with Sandbox() as sb:
        container_id = sb.container_id
    with pytest.raises(docker.errors.NotFound):
        docker.from_env().containers.get(container_id)


def test_copy_in(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "leak").symlink_to("/etc/hosts")

    with Sandbox() as sb:
        sb.copy_in(tmp_path)
        assert sb.run("python src/app.py").stdout == "hi\n"
        assert sb.run("test -e .git").exit_code == 1
        assert sb.run("readlink leak").stdout.strip() == "/etc/hosts"


def test_read_file_is_not_truncated(sandbox):
    sandbox.run("python -c \"print('x' * 50_000, end='')\" > /tmp/big.txt")
    assert len(sandbox.read_file("/tmp/big.txt")) == 50_000


def test_read_file_errors(sandbox):
    with pytest.raises(FileNotFoundError):
        sandbox.read_file("/tmp/nope.txt")
    with pytest.raises(FileNotFoundError):
        sandbox.read_file("/tmp")
    sandbox.run("ln -sf /etc/hosts /tmp/link")
    with pytest.raises(FileNotFoundError):
        sandbox.read_file("/tmp/link")
    with pytest.raises(ValueError):
        sandbox.read_file("/etc/os-release", max_bytes=10)


def test_truncate_keeps_head_and_tail():
    text = "A" * 100 + "B" * 100
    out = truncate(text, limit=20)
    assert out.startswith("A" * 10)
    assert out.endswith("B" * 10)
    assert "180 caractères tronqués" in out
