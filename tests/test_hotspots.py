import subprocess

import pytest

from robot.recon import complexity, find_hotspots, parse_git_log


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=dev", "-c", "user.email=dev@test", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", 1),
        ("x = 1\n", 1),
        ("if a:\n    pass\n", 2),
        ("for i in x:\n    if i:\n        pass\n", 3),
        ("if a and b and c:\n    pass\n", 4),
        ("try:\n    pass\nexcept ValueError:\n    pass\n", 2),
        ("y = [i for i in x if i]\n", 3),
        ("def f(:\n", 1),
    ],
)
def test_complexity(source, expected):
    assert complexity(source) == expected


def test_parse_git_log():
    log = (
        "\x01aaa\x02alice\x021700000000\x02fix: crash on empty input\n"
        "src/app.py\n"
        "tests/test_app.py\n"
        "\n"
        "\x01bbb\x02bob\x021600000000\x02add feature\n"
        "src/app.py\n"
        "docs/guide.md\n"
        "node_modules/lib/x.js\n"
    )
    commits, changes = parse_git_log(log)

    assert commits == 2
    assert set(changes) == {"src/app.py", "tests/test_app.py"}
    assert changes["src/app.py"]["commits"] == 2
    assert changes["src/app.py"]["fixes"] == 1
    assert changes["src/app.py"]["authors"] == {"alice", "bob"}
    assert changes["src/app.py"]["last"] == 1700000000


@pytest.fixture
def repo(tmp_path):
    git("init", "--quiet", cwd=tmp_path)
    (tmp_path / "stable.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "messy.py").write_text("def f(x):\n    if x:\n        return 1\n    return 2\n")
    git("add", ".", cwd=tmp_path)
    git("commit", "--quiet", "-m", "initial", cwd=tmp_path)

    for i in range(3):
        (tmp_path / "messy.py").write_text(
            "def f(x):\n" + "".join(f"    if x == {n}:\n        return {n}\n" for n in range(i + 2)) + "    return 0\n"
        )
        git("commit", "--quiet", "-am", f"fix: bug {i}", cwd=tmp_path)
    return tmp_path


def test_find_hotspots(repo):
    report = find_hotspots(repo)

    assert report.status == "ok"
    assert report.commits_analysed == 4
    top = report.top()
    assert [h.path for h in top] == ["messy.py", "stable.py"]

    messy = top[0]
    assert (messy.commits, messy.fix_commits, messy.authors) == (4, 3, 1)
    assert messy.complexity == 5
    assert messy.days_since_change == 0
    assert messy.score > top[1].score * 5


def test_find_hotspots_without_git(tmp_path):
    assert find_hotspots(tmp_path).status == "unavailable"


def test_deleted_files_are_skipped(repo):
    git("rm", "--quiet", "stable.py", cwd=repo)
    git("commit", "--quiet", "-m", "remove", cwd=repo)
    assert [h.path for h in find_hotspots(repo).files] == ["messy.py"]
