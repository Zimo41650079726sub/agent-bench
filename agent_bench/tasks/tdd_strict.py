"""TaskTDDStrict (tdd_strict_v1): TDD with an auditable red phase.

Targets two measured failure modes from tdd_order_v1:
- gemma-4-e2b passed everything except red_observed (0/3): it wrote tests
  and immediately implemented, never looking at the failure. Here the red
  phase leaves a file: red.log must exist — with failing output — before
  main.py does. File first-appearance ordering makes the skip mechanical
  to detect.
- gemma-4-e4b got stuck writing a stub implementation of the target
  function *inside the test file* (3/3 trials). no_test_stub catches that
  with an AST check, not pattern matching.
"""

from __future__ import annotations

from .base import BenchTask, BenchContext, Skill

PROMPT = """\
Implement a function `roman(n)` (integer 1..3999 -> Roman numeral string)
in /workspace/main.py using strict TDD:

1. FIRST write /workspace/test_main.py with exactly these checks:
   roman(4) == "IV", roman(9) == "IX", roman(14) == "XIV",
   roman(90) == "XC", roman(1990) == "MCMXC", roman(3999) == "MMMCMXCIX".
   The test file must import roman from main. It must NOT define roman
   itself — not even a placeholder.
2. Run the tests and save the failing output to /workspace/red.log
   (for example: python -m pytest -q 2>&1 | tee red.log).
   main.py must not exist yet at this point.
3. THEN write main.py and make the tests pass.

Run tests with: python -m pytest
"""

# AST ground truth: the test file imports from main and defines no roman()
# of its own. Exit 0 = clean, 1 = stub or missing import, 2 = unreadable.
AST_CHECK = (
    'python -c "'
    "import ast, sys\n"
    "try:\n"
    "    tree = ast.parse(open('/workspace/test_main.py').read())\n"
    "except (OSError, SyntaxError):\n"
    "    sys.exit(2)\n"
    "stub = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))\n"
    "           and n.name == 'roman' for n in ast.walk(tree))\n"
    "imports_main = any(isinstance(n, ast.ImportFrom) and n.module == 'main'\n"
    "                   for n in ast.walk(tree)) or \\\n"
    "    any(isinstance(n, ast.Import) and\n"
    "        any(a.name == 'main' for a in n.names) for n in ast.walk(tree))\n"
    'sys.exit(0 if (not stub and imports_main) else 1)"'
)

CONTRACT = ('python -c "from main import roman; '
            "assert roman(4) == 'IV'; assert roman(9) == 'IX'; "
            "assert roman(14) == 'XIV'; assert roman(90) == 'XC'; "
            "assert roman(1990) == 'MCMXC'; "
            'assert roman(3999) == \'MMMCMXCIX\'"')


class TaskTDDStrict(BenchTask):
    id = "tdd_strict_v1"
    allowed_writes = ["main.py", "test_main.py", "test_*.py", "red.log"]
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

    async def _eval_red_logged(self, ctx: BenchContext):
        # The red phase left evidence: red.log appeared strictly before the
        # implementation and actually contains failing output.
        red_turn = ctx.first_change_turn("red.log")
        impl_turn = ctx.first_change_turn("main.py")
        if red_turn is None or impl_turn is None or red_turn >= impl_turn:
            return None
        code, _, _ = await ctx.harness_exec(
            "grep -Eqi '(fail|error|no tests ran)' /workspace/red.log")
        return red_turn if code == 0 else None

    async def _eval_no_test_stub(self, ctx: BenchContext):
        code, _, _ = await ctx.harness_exec(AST_CHECK)
        return code == 0

    async def _eval_tests_pass(self, ctx: BenchContext):
        code, _, _ = await ctx.harness_exec("python -m pytest -q")
        if code != 0:
            return None
        code, _, _ = await ctx.harness_exec(CONTRACT)
        return code == 0

    @property
    def skills(self) -> list[Skill]:
        return [
            Skill("test_first", "test file appeared before the implementation",
                  self._eval_test_first),
            Skill("red_logged",
                  "failing output saved to red.log before implementing",
                  self._eval_red_logged),
            Skill("no_test_stub",
                  "test file imports from main and defines no roman() stub",
                  self._eval_no_test_stub),
            Skill("tests_pass",
                  "implementation satisfies the contract (harness-verified)",
                  self._eval_tests_pass),
        ]
