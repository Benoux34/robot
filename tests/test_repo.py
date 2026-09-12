import subprocess

import pytest

from robot.repo import CloneError, clone_repo


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    repo.mkdir()
    git("init", "--quiet", cwd=repo)
    (repo / "app.py").write_text("print('v1')\n")
    git("add", ".", cwd=repo)
    git("commit", "--quiet", "-m", "v1", cwd=repo)
    git("tag", "v1", cwd=repo)
    (repo / "app.py").write_text("print('v2')\n")
    git("commit", "--quiet", "-am", "v2", cwd=repo)
    return repo


def test_clone_returns_path_and_commit(origin, tmp_path):
    repo = clone_repo(str(origin), tmp_path / "clone")
    assert (repo.path / "app.py").read_text() == "print('v2')\n"
    assert len(repo.commit) == 40


def test_clone_at_ref(origin, tmp_path):
    repo = clone_repo(str(origin), tmp_path / "clone", ref="v1")
    assert (repo.path / "app.py").read_text() == "print('v1')\n"


def test_clone_bad_source_raises(tmp_path):
    with pytest.raises(CloneError):
        clone_repo(str(tmp_path / "nope"), tmp_path / "clone")


def test_clone_refuses_non_empty_dest(origin, tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    (dest / "existing.txt").write_text("x")
    with pytest.raises(CloneError):
        clone_repo(str(origin), dest)
