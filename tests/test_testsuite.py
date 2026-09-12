import pytest

from robot.recon import parse_junit, run_tests

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="tests.test_math" name="test_ok" time="0.01"/>
  <testcase classname="tests.test_math" name="test_ko" time="0.02">
    <failure message="assert 2 == 3">def test_ko():&gt;  assert 1 + 1 == 3</failure>
  </testcase>
  <testcase classname="tests.test_math" name="test_crash" time="0">
    <error message="failed on setup">fixture 'db' not found</error>
  </testcase>
  <testcase classname="tests.test_math" name="test_later" time="0">
    <skipped message="pas encore"/>
  </testcase>
</testsuite></testsuites>"""


def test_parse_junit():
    cases = parse_junit(JUNIT)
    assert [(c.id, c.outcome) for c in cases] == [
        ("tests.test_math::test_ok", "passed"),
        ("tests.test_math::test_ko", "failed"),
        ("tests.test_math::test_crash", "error"),
        ("tests.test_math::test_later", "skipped"),
    ]
    assert cases[1].message == "assert 2 == 3"
    assert "1 + 1 == 3" in cases[1].details
    assert cases[1].duration == 0.02


def test_parse_junit_rejects_entity_bombs():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>'
    with pytest.raises(ValueError):
        parse_junit(bomb)


def test_run_tests_mixed_results(workspace):
    sandbox = workspace({
        "tests/test_demo.py": (
            "import pytest\n"
            "def test_ok(): assert True\n"
            "def test_ko(): assert 1 + 1 == 3\n"
            "def test_crash(db): pass\n"
            "@pytest.mark.skip(reason='plus tard')\n"
            "def test_later(): pass\n"
        ),
    })

    suite = run_tests(sandbox)

    assert suite.status == "failed"
    assert (suite.count("passed"), suite.count("failed"), suite.count("error"), suite.count("skipped")) == (1, 1, 1, 1)
    assert [c.id for c in suite.problems] == ["tests.test_demo::test_ko", "tests.test_demo::test_crash"]
    assert "assert (1 + 1) == 3" in suite.problems[0].message
    assert sandbox.run("test -e .pytest_cache || test -e tests/__pycache__").exit_code == 1


def test_run_tests_all_green(workspace):
    sandbox = workspace({"test_a.py": "def test_a(): pass"})
    suite = run_tests(sandbox)
    assert suite.status == "passed"
    assert suite.summary().startswith("passed (1 passed)")


def test_run_tests_no_tests(workspace):
    sandbox = workspace({"app.py": "print('hi')"})
    assert run_tests(sandbox).status == "no_tests"


def test_collection_error_does_not_hide_other_tests(workspace):
    sandbox = workspace({
        "test_broken.py": "import module_qui_n_existe_pas\ndef test_a(): pass",
        "test_fine.py": "def test_b(): pass",
    })
    suite = run_tests(sandbox)
    assert suite.status == "failed"
    assert suite.count("error") == 1
    assert suite.count("passed") == 1
    assert "module_qui_n_existe_pas" in suite.problems[0].details


def test_run_tests_with_coverage(workspace):
    sandbox = workspace({
        "src/mod.py": (
            "def sign(n):\n"
            "    if n > 0:\n"
            "        return 1\n"
            "    return -1\n"
            "\n"
            "def never_called():\n"
            "    return 42\n"
        ),
        "tests/test_mod.py": (
            "import sys; sys.path.insert(0, 'src')\n"
            "from mod import sign\n"
            "def test_sign(): assert sign(5) == 1\n"
        ),
    })

    suite = run_tests(sandbox, with_coverage=True)

    assert suite.status == "passed"
    assert [f.path for f in suite.coverage.files] == ["src/mod.py"]
    mod = suite.coverage.files[0]
    assert mod.missing_lines == [4, 7]
    assert mod.missing_branches == [(2, 4)]
    assert sandbox.run("test -e .coverage").exit_code == 1


def test_run_tests_without_coverage(workspace):
    sandbox = workspace({"test_a.py": "def test_a(): pass"})
    assert run_tests(sandbox).coverage is None


def test_run_tests_timeout(workspace):
    sandbox = workspace({"test_slow.py": "import time\ndef test_slow(): time.sleep(30)"})
    suite = run_tests(sandbox, timeout=2)
    assert suite.status == "timeout"
