import subprocess

from robot.evals import build_fix_dataset, find_candidates


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True)


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def make_history(path):
    path.mkdir(parents=True)
    git("init", "--quiet", cwd=path)
    write(path, "pyproject.toml",
          '# fix-dataset\n[project]\nname = "demo"\nversion = "0.4.0"\nrequires-python = ">=3.10"\n'
          '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n')
    write(path, "src/demo/__init__.py", "def sign(n):\n    if n > 0:\n        return 1\n    return -1\n")
    write(path, "tests/test_demo.py", "from demo import sign\ndef test_positive(): assert sign(5) == 1\n")
    git("add", ".", cwd=path)
    git("commit", "--quiet", "-m", "initial", cwd=path)

    write(path, "src/demo/__init__.py",
          "def sign(n):\n    if n == 0:\n        return 0\n    if n > 0:\n        return 1\n    return -1\n")
    write(path, "tests/test_demo.py",
          "from demo import sign\ndef test_positive(): assert sign(5) == 1\ndef test_zero(): assert sign(0) == 0\n")
    git("commit", "--quiet", "-am", "fix: sign(0) renvoyait -1", cwd=path)

    write(path, "src/demo/__init__.py",
          "def sign(n):\n    if n == 0:\n        return 0\n    if n > 0:\n        return 1\n    return -1\n\n\ndef noop():\n    pass\n")
    git("commit", "--quiet", "-am", "refacto sans test", cwd=path)

    write(path, "src/demo/extra.py", "def double(n):\n    return n * 2\n")
    write(path, "tests/test_extra.py", "from demo.extra import double\ndef test_d(): assert double(2) == 4\n")
    git("add", ".", cwd=path)
    git("commit", "--quiet", "-m", "feat: double", cwd=path)
    return path


def test_find_candidates_keeps_only_code_plus_tests(tmp_path):
    repo = make_history(tmp_path / "demo")

    candidates = find_candidates(repo, "10 years ago")

    assert [c.subject for c in candidates] == ["fix: sign(0) renvoyait -1"]
    assert candidates[0].code_files == ["src/demo/__init__.py"]
    assert candidates[0].test_files == ["tests/test_demo.py"]


def test_build_fix_dataset(tmp_path, cache):
    repo = make_history(tmp_path / "demo")
    out = tmp_path / "dataset"

    report = build_fix_dataset(str(repo), limit=2, since="10 years ago", out=out)

    assert report.status == "ok"
    assert len(report.tasks) == 1

    task = report.tasks[0]
    assert task.dataset == "fix"
    assert task.bug["files"] == ["src/demo/__init__.py"]
    assert task.bug["description"] == "fix: sign(0) renvoyait -1"
    assert [t.split("::")[-1] for t in task.validation["fail_to_pass"]] == ["test_zero"]
    assert task.validation["pass_to_pass"] == 2
    assert "+    if n == 0:" in task.patch
    assert (out / f"{task.id}.json").is_file()
