import pytest

from robot.recon import detect_stack, pick_image
from robot.recon.stack import is_test_file


def write(root, name, content=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.parametrize(
    ("requires", "image"),
    [
        (None, "python:3.12-slim"),
        (">=3.9", "python:3.12-slim"),
        (">=3.13", "python:3.13-slim"),
        ("<3.12", "python:3.11-slim"),
        ("~=3.10.0", "python:3.10-slim"),
        ("pas une version", "python:3.12-slim"),
    ],
)
def test_pick_image(requires, image):
    assert pick_image(requires) == image


def test_detect_stack(tmp_path):
    write(tmp_path, "pyproject.toml", '[project]\nname = "demo"\nrequires-python = ">=3.13"\n')
    write(tmp_path, "src/app.py", "a = 1\nb = 2\nc = 3\n")
    write(tmp_path, "tests/test_app.py", "def test_a():\n    pass\n")
    write(tmp_path, "web/index.ts", "export {}\n")
    write(tmp_path, "node_modules/lib/huge.js", "x\n" * 1000)
    write(tmp_path, ".github/workflows/ci.yml")

    stack = detect_stack(tmp_path)

    assert stack.main_language == "Python"
    assert stack.lines_by_language == {"Python": 5, "TypeScript": 1}
    assert stack.image == "python:3.13-slim"
    assert stack.dependency_files == ["pyproject.toml"]
    assert stack.source_files == 3
    assert stack.test_files == 1
    assert stack.has_ci


def test_symlinks_are_not_followed(tmp_path):
    secret = tmp_path.parent / "secret.py"
    secret.write_text("x\n" * 50)
    (tmp_path / "leak.py").symlink_to(secret)

    stack = detect_stack(tmp_path)

    assert stack.lines_by_language == {}
    assert stack.main_language is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_app.py", True),
        ("src/app_test.py", True),
        ("src/app_test.go", True),
        ("src/index.test.ts", True),
        ("src/index.spec.js", True),
        ("test.js", True),
        ("__tests__/helpers.js", True),
        ("conftest.py", True),
        ("src/app.py", False),
        ("src/contest.py", False),
        ("src/testing_utils.py", False),
    ],
)
def test_is_test_file(path, expected):
    from pathlib import Path as P

    assert is_test_file(P(path)) is expected
