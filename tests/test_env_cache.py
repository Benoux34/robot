import subprocess

import pytest

from robot.env_cache import EnvCache, environment_key
from robot.env_cache import dependency_files
from robot.sandbox import Sandbox


def write(root, name, content=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def repo(tmp_path):
    write(tmp_path, "pyproject.toml", '[project]\nname = "demo"\nversion = "0.1.0"\n')
    write(tmp_path, "requirements.txt", "six==1.16.0\n")
    write(tmp_path, "src/app.py", "print('hi')\n")
    return tmp_path


def test_key_is_stable(repo):
    assert environment_key(repo, "python:3.12-slim") == environment_key(repo, "python:3.12-slim")


def test_key_ignores_source_code(repo):
    before = environment_key(repo, "python:3.12-slim")
    write(repo, "src/app.py", "print('completely different')\n")
    write(repo, "src/other.py", "x = 1\n")
    assert environment_key(repo, "python:3.12-slim") == before


@pytest.mark.parametrize(
    "change",
    [
        lambda r: write(r, "requirements.txt", "six==1.17.0\n"),
        lambda r: write(r, "pyproject.toml", '[project]\nname = "demo"\nversion = "0.2.0"\n'),
        lambda r: write(r, "requirements-dev.txt", "pytest\n"),
        lambda r: write(r, "uv.lock", "version = 1\n"),
    ],
)
def test_key_changes_with_dependencies(repo, change):
    before = environment_key(repo, "python:3.12-slim")
    change(repo)
    assert environment_key(repo, "python:3.12-slim") != before


def test_key_changes_with_image(repo):
    assert environment_key(repo, "python:3.12-slim") != environment_key(repo, "python:3.13-slim")


def test_dependency_files_ignores_symlinks(repo, tmp_path):
    (repo / "link.txt").symlink_to(repo / "requirements.txt")
    (repo / "requirements-link.txt").symlink_to(repo / "requirements.txt")
    names = [p.name for p in dependency_files(repo)]
    assert names == ["pyproject.toml", "requirements.txt"]


def test_save_find_and_reuse(cache):
    key = "test" + "0" * 12
    assert cache.find(key) is None

    with Sandbox(network=True) as sandbox:
        assert sandbox.run("pip install six==1.16.0", timeout=300).ok
        tag = cache.save(sandbox, key)

    assert tag == f"robot-env:{key}"
    assert cache.find(key) == tag

    with Sandbox(tag) as reused:
        assert reused.run("python -c 'import six; print(six.__version__)'").stdout.strip() == "1.16.0"
        assert reused.run("pip download six --no-cache-dir -d /tmp/x", timeout=60).exit_code != 0

    entry = next(e for e in cache.entries() if e.key == key)
    assert entry.size_mb > 0
    cache.remove(tag)
    assert cache.find(key) is None


def test_pipeline_uses_the_cache(tmp_path, cache):
    import uuid

    from robot.audit import audit_repo

    unique = uuid.uuid4().hex[:8]

    repo = tmp_path / "demo"
    (repo / "src" / "demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pyproject.toml").write_text(
        f'# clé de cache unique à ce test : {unique}\n'
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )
    (repo / "src" / "demo" / "__init__.py").write_text("def sign(n):\n    return 1 if n > 0 else -1\n")
    (repo / "tests" / "test_demo.py").write_text("from demo import sign\ndef test_s(): assert sign(1) == 1\n")
    for args in (["init", "--quiet"], ["add", "."], ["commit", "--quiet", "-m", "init"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=repo, check=True, capture_output=True)

    first = audit_repo(str(repo), since="10 years ago")
    assert first.cache == "miss"
    assert first.install.ok and len(first.install.steps) > 0
    assert cache.find(first.cache_key) is not None

    second = audit_repo(str(repo), since="10 years ago")
    assert second.cache == "hit"
    assert second.cache_key == first.cache_key
    assert second.install.steps == []
    assert second.tests.status == "passed"
    assert second.duration < first.duration
    assert "cache" in second.to_markdown()
    assert second.metrics()["cache"] == "hit"
