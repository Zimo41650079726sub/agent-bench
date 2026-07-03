"""TaskDebugPython (debug_python_v1): investigate, fix, and verify a buggy
Python function. Four flags: file_read / error_interpret / file_edit /
self_verify."""

from __future__ import annotations

from .base import BenchTask, BenchContext, Skill

MAIN_PY = '''\
def add(a, b):
    return a - b
'''

TEST_PY = '''\
from main import add

def test_add():
    assert add(2, 3) == 5

def test_add_negative():
    assert add(-1, 1) == 0
'''

PROMPT = """\
The project in /workspace has a failing test suite. Investigate the code,
find the bug, fix it, and make sure the tests pass. Do not modify the tests.
Files: main.py, test_main.py. Run tests with: python -m pytest
"""


class TaskDebugPython(BenchTask):
    id = "debug_python_v1"
    allowed_writes = ["main.py"]
    max_turns = 12

    async def setup(self, sandbox) -> None:
        await sandbox.write_file("/workspace/main.py", MAIN_PY)
        await sandbox.write_file("/workspace/test_main.py", TEST_PY)

    def get_prompt(self) -> str:
        return PROMPT

    def _fix_turn(self, ctx: BenchContext) -> int | None:
        return ctx.first_change_turn("main.py")

    async def _eval_file_read(self, ctx: BenchContext):
        reads = [log for log in ctx.read_turns("main.py")
                 if "test_main.py" not in str(log.args)]
        return reads[0].turn if reads else None

    async def _eval_error_interpret(self, ctx: BenchContext):
        # A pytest run that actually failed, strictly before the fix landed.
        # Before/after is decided by file first-change turn, not by anything
        # the model claimed.
        fix = self._fix_turn(ctx)
        if fix is None:
            return None
        fails = [log for log in ctx.command_turns("pytest")
                 if log.exit_code not in (0, -1) and log.turn <= fix]
        return fails[0].turn if fails else None

    async def _eval_file_edit(self, ctx: BenchContext):
        # Ground truth: the harness runs the check itself.
        code, _, _ = await ctx.harness_exec(
            'python -c "from main import add; assert add(2, 3) == 5; assert add(-1, 1) == 0"'
        )
        return code == 0

    async def _eval_self_verify(self, ctx: BenchContext):
        # The model ran pytest after its fix, AND the harness's independent
        # rerun confirms the suite actually passes. The model's own exit code
        # alone is not trusted (`pytest || true` would fake it).
        fix = self._fix_turn(ctx)
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
            Skill("file_read", "read main.py to understand the bug",
                  self._eval_file_read),
            Skill("error_interpret", "observed the failing pytest before fixing",
                  self._eval_error_interpret),
            Skill("file_edit", "fixed the bug (harness-verified)",
                  self._eval_file_edit),
            Skill("self_verify", "reran pytest after the fix and it passes",
                  self._eval_self_verify),
        ]
