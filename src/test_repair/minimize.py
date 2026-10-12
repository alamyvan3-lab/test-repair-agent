"""Shrink a passing patch to the changes the tests actually need.

Each contiguous change region ("unit") of the final patch is tentatively reverted; if the
suite stays green the revert is kept. Passes repeat until one makes no change, so every
remaining unit is necessary given the others (1-minimal, the guarantee of Zeller and
Hildebrandt's delta debugging, IEEE TSE 2002). Patches are small, so testing one unit at
a time is cheaper here than ddmin's partition search.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass

Opcode = tuple[str, int, int, int, int]


@dataclass(frozen=True)
class Units:
    """Change regions of a patch, per file, numbered globally."""

    before: dict[str, list[str]]
    after: dict[str, list[str]]
    opcodes: dict[str, list[Opcode]]
    count: int

    @classmethod
    def of(cls, before: dict[str, str], after: dict[str, str]) -> Units:
        b = {p: t.splitlines(keepends=True) for p, t in before.items()}
        a = {p: after[p].splitlines(keepends=True) for p in before}
        ops = {p: difflib.SequenceMatcher(None, b[p], a[p], autojunk=False).get_opcodes()
               for p in before}
        count = sum(op[0] != "equal" for p in ops for op in ops[p])
        return cls(b, a, ops, count)

    def build(self, removed: set[int]) -> dict[str, str]:
        """File texts with the units in `removed` reverted to the original."""
        out: dict[str, str] = {}
        unit = 0
        for path, ops in self.opcodes.items():
            lines: list[str] = []
            for tag, i1, i2, j1, j2 in ops:
                if tag == "equal":
                    lines += self.before[path][i1:i2]
                    continue
                lines += self.before[path][i1:i2] if unit in removed else self.after[path][j1:j2]
                unit += 1
            out[path] = "".join(lines)
        return out


def minimize(units: Units, passes: Callable[[dict[str, str]], bool],
             max_runs: int) -> tuple[set[int], int]:
    """Greedy repeated elimination. Returns (removed unit ids, test runs used)."""
    removed: set[int] = set()
    runs = 0
    changed = True
    while changed and units.count - len(removed) > 1:
        changed = False
        for unit in range(units.count):
            if unit in removed:
                continue
            if runs >= max_runs or units.count - len(removed) == 1:
                return removed, runs
            runs += 1
            if passes(units.build(removed | {unit})):
                removed.add(unit)
                changed = True
    return removed, runs
