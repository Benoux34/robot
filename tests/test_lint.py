import json

import pytest

from robot.recon import parse_ruff, run_lint

RUFF_OUTPUT = json.dumps([
    {
        "code": "F821",
        "message": "Undefined name `y`",
        "filename": "/workspace/src/mod.py",
        "location": {"row": 3, "column": 20},
    },
    {
        "code": "B006",
        "message": "Do not use mutable data structures for argument defaults",
        "filename": "/workspace/src/mod.py",
        "location": {"row": 2, "column": 9},
    },
    {
        "code": None,
        "message": "SyntaxError: Expected ')'",
        "filename": "/workspace/broken.py",
        "location": {"row": 1, "column": 5},
    },
    "pas un dict",
])


def test_parse_ruff():
    issues = parse_ruff(RUFF_OUTPUT)
    assert [(i.code, i.category, i.path, i.line) for i in issues] == [
        ("F821", "error", "src/mod.py", 3),
        ("B006", "bug-risk", "src/mod.py", 2),
        ("syntax-error", "error", "broken.py", 1),
    ]


def test_parse_ruff_rejects_non_list():
    with pytest.raises(ValueError):
        parse_ruff('{"code": "F401"}')


def test_run_lint(workspace):
    sandbox = workspace({
        "src/mod.py": (
            "import os\n"
            "def f(x=[]):\n"
            "    return eval(x) + y\n"
        ),
        "tests/test_mod.py": "def test_a():\n    assert True\n",
    })

    report = run_lint(sandbox)

    assert report.status == "ok"
    assert sorted(i.code for i in report.issues) == ["B006", "F401", "F821", "S307"]
    assert report.count("security") == 1
    assert report.summary() == "4 problèmes (2 error, 1 bug-risk, 1 security)"
    assert sandbox.run("test -e .ruff_cache").exit_code == 1


def test_security_rules_skip_test_files(workspace):
    code = 'connect(password="hunter2")\n'
    sandbox = workspace({
        "src/db.py": code,
        "tests/test_db.py": code,
        "test_root.py": code,
        "conftest.py": code,
    })
    assert [(i.path, i.code) for i in run_lint(sandbox).issues if i.category == "security"] == [
        ("src/db.py", "S106")
    ]


def test_run_lint_ignores_repo_config(workspace):
    sandbox = workspace({
        "pyproject.toml": '[tool.ruff.lint]\nignore = ["F"]\n',
        "mod.py": "print(undefined_name)\n",
    })
    assert [i.code for i in run_lint(sandbox).issues] == ["F821"]


def test_lint_on_repo_without_python(workspace):
    sandbox = workspace({"index.js": "const x = eval('1')\n", "package.json": "{}"})
    report = run_lint(sandbox)
    assert report.status == "no_python_files"
    assert report.summary() == "aucun fichier Python à analyser"
    assert report.issues == []
