import pytest

from robot.evals.mutations import Mutation, apply_mutation, find_mutations

CODE = '''def check(n, flag):
    # ignore ce commentaire : n > 0
    label = "a > b"
    if n > 0 and flag is True:
        return 1
    return 0
'''


def test_find_mutations_ignores_strings_and_comments():
    found = find_mutations(CODE)
    assert all(m.line in (4, 5, 6) for m in found)
    assert [(m.line, m.before, m.after, m.kind) for m in found] == [
        (4, ">", ">=", "comparison"),
        (4, "0", "1", "number"),
        (4, "and", "or", "boolean"),
        (4, "True", "False", "constant"),
        (5, "1", "2", "number"),
        (6, "0", "1", "number"),
    ]


def test_apply_mutation_changes_only_the_target():
    mutation = next(m for m in find_mutations(CODE) if m.before == "and")
    mutated = apply_mutation(CODE, mutation)
    assert "if n > 0 or flag is True:" in mutated
    assert '"a > b"' in mutated
    assert mutated.count("\n") == CODE.count("\n")


@pytest.mark.parametrize(
    "mutation",
    [
        Mutation(line=999, col=0, before=">", after=">=", kind="comparison"),
        Mutation(line=4, col=0, before=">", after=">=", kind="comparison"),
    ],
)
def test_apply_mutation_refuses_bad_coordinates(mutation):
    assert apply_mutation(CODE, mutation) is None


def test_broken_source_yields_nothing():
    assert find_mutations("def f(:\n") == []


def test_mutations_keep_valid_python():
    for mutation in find_mutations(CODE):
        assert apply_mutation(CODE, mutation) is not None
