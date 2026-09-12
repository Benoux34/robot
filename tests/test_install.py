import pytest

from robot.install import TOOLS_CMD, install, plan_install
from robot.sandbox import Sandbox


def write(root, name, content=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_empty_repo_only_installs_pytest(tmp_path):
    assert plan_install(tmp_path) == [TOOLS_CMD]


def test_requirements_files(tmp_path):
    write(tmp_path, "requirements.txt")
    write(tmp_path, "requirements-dev.txt")
    write(tmp_path, "requirements-docs.txt")
    write(tmp_path, "requirements/test.txt")
    assert plan_install(tmp_path) == [
        "pip install -r requirements-dev.txt",
        "pip install -r requirements.txt",
        "pip install -r requirements/test.txt",
        TOOLS_CMD,
    ]


def test_project_with_test_extra(tmp_path):
    write(tmp_path, "pyproject.toml", """
[project]
name = "demo"
[project.optional-dependencies]
test = ["pytest"]
docs = ["sphinx"]
""")
    assert plan_install(tmp_path) == ["pip install -e '.[test]'", TOOLS_CMD]


def test_dependency_groups_upgrade_pip(tmp_path):
    write(tmp_path, "pyproject.toml", """
[project]
name = "demo"
[dependency-groups]
tests = ["pytest", "freezegun"]
docs = ["sphinx"]
""")
    assert plan_install(tmp_path) == [
        "pip install --upgrade 'pip>=25.1'",
        "pip install -e .",
        "pip install --group tests",
        TOOLS_CMD,
    ]


def test_falls_back_to_dev(tmp_path):
    write(tmp_path, "pyproject.toml", """
[project]
name = "demo"
[project.optional-dependencies]
dev = ["pytest"]
""")
    assert plan_install(tmp_path)[0] == "pip install -e '.[dev]'"


def test_tool_only_pyproject_is_not_installed(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    assert plan_install(tmp_path) == [TOOLS_CMD]


def test_invalid_pyproject_is_ignored(tmp_path):
    write(tmp_path, "pyproject.toml", "this is [not toml")
    assert plan_install(tmp_path) == [TOOLS_CMD]


def test_only_known_section_names_reach_the_shell(tmp_path):
    write(tmp_path, "pyproject.toml", """
[dependency-groups]
"tests; touch /pwned" = []
Tests = []
""")
    assert plan_install(tmp_path)[-2] == "pip install --group Tests"


def test_disable_network():
    with Sandbox(network=True) as sb:
        probe = "python -c \"import urllib.request as u; u.urlopen('https://pypi.org', timeout=5)\""
        assert sb.run(probe).ok
        sb.disable_network()
        assert not sb.run(probe).ok


def test_install_then_run_offline(tmp_path):
    write(tmp_path, "requirements.txt", "six==1.16.0\n")
    with Sandbox(network=True) as sb:
        sb.copy_in(tmp_path)
        report = install(sb, tmp_path)
        assert report.ok, [s.result.stderr for s in report.steps]
        sb.disable_network()
        assert sb.run("python -c 'import six; print(six.__version__)'").stdout.strip() == "1.16.0"
        assert sb.run("python -m pytest --version").ok
