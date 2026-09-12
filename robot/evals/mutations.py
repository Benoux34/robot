from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

OPERATORS = {
    ">": ">=", ">=": ">", "<": "<=", "<=": "<",
    "==": "!=", "!=": "==", "+": "-", "-": "+",
}
KEYWORDS = {"and": "or", "or": "and", "True": "False", "False": "True"}
KINDS = {"and": "boolean", "or": "boolean", "True": "constant", "False": "constant"}


@dataclass(frozen=True)
class Mutation:
    line: int
    col: int
    before: str
    after: str
    kind: str

    def describe(self) -> str:
        return f"ligne {self.line} : `{self.before}` → `{self.after}` ({self.kind})"


def find_mutations(source: str) -> list[Mutation]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []

    found = []
    for token in tokens:
        line, col = token.start
        if token.type == tokenize.OP and token.string in OPERATORS:
            found.append(Mutation(line, col, token.string, OPERATORS[token.string], "comparison"
                                  if token.string not in "+-" else "arithmetic"))
        elif token.type == tokenize.NAME and token.string in KEYWORDS:
            found.append(Mutation(line, col, token.string, KEYWORDS[token.string], KINDS[token.string]))
        elif token.type == tokenize.NUMBER and token.string.isdigit():
            found.append(Mutation(line, col, token.string, str(int(token.string) + 1), "number"))

    return [m for m in found if apply_mutation(source, m) is not None]


def apply_mutation(source: str, mutation: Mutation) -> str | None:
    lines = source.splitlines(keepends=True)
    if not 0 < mutation.line <= len(lines):
        return None

    line = lines[mutation.line - 1]
    end = mutation.col + len(mutation.before)
    if line[mutation.col:end] != mutation.before:
        return None

    lines[mutation.line - 1] = line[:mutation.col] + mutation.after + line[end:]
    mutated = "".join(lines)

    try:
        ast.parse(mutated)
    except (SyntaxError, ValueError):
        return None
    return mutated
