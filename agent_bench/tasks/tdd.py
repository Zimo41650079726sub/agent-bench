"""TaskTDD (tdd_order_v1): can the model keep a test-first discipline?

Ordering is judged from workspace-snapshot file first-appearance turns, not
from write_file logs — a shell-side `echo > main.py` or heredoc counts on
exactly the same footing.
"""

from __future__ import annotations

from .base import BenchTask, BenchContext, Skill

PROMPT = """\
Implement a function `fizzbuzz(n)` in /workspace/main.py using strict TDD:

1. FIRST write the tests in /workspace/test_main.py:
   - fizzbuzz(3) == "Fizz", fizzbuzz(5) == "Buzz",
   - fizzbuzz(15) == "FizzBuzz", fizzbuzz(7) == "7"
2. Run the tests and confirm they fail (main.py must not exist yet).
3. THEN write main.py and make the tests pass.

Run tests with: python -m pytest
"""


class TaskTDD(BenchTask):
    id = "tdd_order_v1"
    allowed_writes = ["main.py", "test_main.py", "test_*.py"]
    max_turns = 12

    async def setup(self, sandbox) -> None:
        pass  # empty workspace

    def get_prompt(self) -> str:
        return PROMPT

    async def _eval_test_first(self, ctx: BenchContext):
        test_turn = ctx.first_change_turn("test_main.py")
        impl_turn = ctx.first_change_turn("main.py")
        if test_turn is None or impl_turn is None:
            return None
        return test_turn if test_turn < impl_turn else None

    async def _eval_red_observed(self, ctx: BenchContext):
        # A failing pytest run strictly after tests existed and before the
        # implementation appeared: the "red" phase of red-green.
        test_turn = ctx.first_change_turn("test_main.py")
        impl_turn = ctx.first_change_turn("main.py")
        if test_turn is None or impl_turn is None:
            return None
        fails = [log for log in ctx.command_turns("pytest")
                 if log.exit_code not in (0, -1)
                 and test_turn <= log.turn <= impl_turn]
        return fails[0].turn if fails else None

    async def _eval_tests_pass(self, ctx: BenchContext):
        code, _, _ = await ctx.harness_exec("python -m pytest -q")
        if code != 0:
            return None
        # Guard against gaming by trivial tests: harness checks the actual
        # contract, not just that some suite passes.
        check = ('python -c "from main import fizzbuzz; '
                 "assert fizzbuzz(3) == 'Fizz'; assert fizzbuzz(5) == 'Buzz'; "
                 "assert fizzbuzz(15) == 'FizzBuzz'; assert fizzbuzz(7) == '7'\"")
        code, _, _ = await ctx.harness_exec(check)
        return code == 0

    @property
    def skills(self) -> list[Skill]:
        return [
            Skill("test_first", "test file appeared before the implementation",
                  self._eval_test_first),
            Skill("red_observed", "saw the tests fail before implementing",
                  self._eval_red_observed),
            Skill("tests_pass", "implementation satisfies the contract (harness-verified)",
                  self._eval_tests_pass),
        ]
