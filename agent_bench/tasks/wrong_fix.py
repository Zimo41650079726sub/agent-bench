"""TaskWrongFixTrap (wrong_fix_trap_v1): a decoy that punishes skipping
the error message.

Targets a measured failure mode (qwen3.5-4b / Tier-2 models, debug runs):
fixing without ever observing the failing test first. Here that habit has
consequences: report.py contains a line that *looks* like an off-by-one bug
(complete with a tempting TODO comment) but is correct — while the real bug
sits in stats.py, and the pytest output names it unambiguously. Models that
pattern-match on code smell instead of reading the error edit the decoy and
break a passing suite.
"""

from __future__ import annotations

from .base import BenchTask, BenchContext, Skill

STATS = """\
def mean(values):
    return sum(values) / len(values)


def median(values):
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid] + vals[mid + 1]) / 2
"""

REPORT = """\
def daily_deltas(readings):
    # TODO: double-check this range boundary
    deltas = []
    for i in range(len(readings) - 1):
        deltas.append(readings[i + 1] - readings[i])
    return deltas


def summary(readings):
    d = daily_deltas(readings)
    return {"days": len(readings), "moves": len(d)}
"""

TEST_STATS = """\
from stats import mean, median

def test_mean():
    assert mean([2, 4, 6]) == 4

def test_median_odd():
    assert median([7, 1, 3]) == 3

def test_median_even():
    assert median([1, 2, 3, 4]) == 2.5
"""

TEST_REPORT = """\
from report import daily_deltas, summary

def test_deltas():
    assert daily_deltas([10, 13, 11]) == [3, -2]

def test_summary():
    assert summary([10, 13, 11]) == {"days": 3, "moves": 2}
"""

PROMPT = """\
The test suite in /workspace has a failure. Investigate, find the actual
bug, and fix it without breaking anything that currently works.
Do not modify the tests. Files: stats.py, report.py, test_stats.py,
test_report.py. Run tests with: python -m pytest
"""

CONTRACT = ('python -c "from stats import median; '
            "assert median([1, 2, 3, 4]) == 2.5; "
            "assert median([1, 2]) == 1.5; "
            'assert median([7, 1, 3]) == 3"')


class TaskWrongFixTrap(BenchTask):
    id = "wrong_fix_trap_v1"
    allowed_writes = ["stats.py", "report.py"]
    max_turns = 12

    async def setup(self, sandbox) -> None:
        await sandbox.write_file("/workspace/stats.py", STATS)
        await sandbox.write_file("/workspace/report.py", REPORT)
        await sandbox.write_file("/workspace/test_stats.py", TEST_STATS)
        await sandbox.write_file("/workspace/test_report.py", TEST_REPORT)

    def get_prompt(self) -> str:
        return PROMPT

    def _first_edit_turn(self, ctx: BenchContext) -> int | None:
        turns = [t for t in (ctx.first_change_turn("stats.py"),
                             ctx.first_change_turn("report.py"))
                 if t is not None]
        return min(turns) if turns else None

    async def _eval_error_interpret(self, ctx: BenchContext):
        # Observed the failing suite before touching any source file.
        fix = self._first_edit_turn(ctx)
        if fix is None:
            return None
        fails = [log for log in ctx.command_turns("pytest")
                 if log.exit_code not in (0, -1) and log.turn <= fix]
        return fails[0].turn if fails else None

    async def _eval_decoy_untouched(self, ctx: BenchContext):
        # The correct-but-suspicious-looking file was left alone.
        return ctx.final_snapshot.get("report.py") == \
            ctx.setup_snapshot.get("report.py")

    async def _eval_real_fixed(self, ctx: BenchContext):
        code, _, _ = await ctx.harness_exec(CONTRACT)
        return code == 0

    async def _eval_self_verify(self, ctx: BenchContext):
        fix = self._first_edit_turn(ctx)
        if fix is None:
            return None
        later = [log for log in ctx.command_turns("pytest") if log.turn >= fix]
        if not later:
            return None
        code, _, _ = await ctx.harness_exec("python -m pytest -q")
        return later[0].turn if code == 0 else None

    @property
    def skills(self) -> list[Skill]:
        return [
            Skill("error_interpret",
                  "observed the failing pytest before editing",
                  self._eval_error_interpret),
            Skill("decoy_untouched",
                  "did not 'fix' the correct code in report.py",
                  self._eval_decoy_untouched),
            Skill("real_fixed", "median() satisfies the contract "
                  "(harness-verified)", self._eval_real_fixed),
            Skill("self_verify",
                  "reran pytest after the fix and the whole suite passes",
                  self._eval_self_verify),
        ]
