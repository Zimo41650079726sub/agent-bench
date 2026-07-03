"""TaskContextManage (context_manage_v1): a multi-file consistency fix.

Measures whether the model can hold state across a longer task without
looping. "No wasteful repetition" is made mechanical: reads of the same file
must stay <= READ_REPEAT_LIMIT (recorded in the task definition, so the
threshold is part of the reproducible spec, not a judgment call).
"""

from __future__ import annotations

from .base import BenchTask, BenchContext, Skill

READ_REPEAT_LIMIT = 3

# services/*.py each import TAX_RATE from config.py; three of them have a
# stale hardcoded copy. The model must find and fix all inconsistencies.
CONFIG = "TAX_RATE = 0.10\n"

MODULES = {
    "services/billing.py": """\
from config import TAX_RATE

def total(amount):
    return amount * (1 + TAX_RATE)
""",
    "services/invoice.py": """\
TAX_RATE = 0.08  # stale copy

def invoice_total(amount):
    return amount * (1 + TAX_RATE)
""",
    "services/receipt.py": """\
TAX_RATE = 0.05  # stale copy

def receipt_total(amount):
    return amount * (1 + TAX_RATE)
""",
    "services/estimate.py": """\
TAX_RATE = 0.08  # stale copy

def estimate_total(amount):
    return amount * (1 + TAX_RATE)
""",
    "services/refund.py": """\
from config import TAX_RATE

def refund_total(amount):
    return amount * (1 + TAX_RATE)
""",
    "services/__init__.py": "",
}

TEST = """\
import sys
sys.path.insert(0, '.')
from services.billing import total
from services.invoice import invoice_total
from services.receipt import receipt_total
from services.estimate import estimate_total
from services.refund import refund_total

def test_consistent():
    values = [total(100), invoice_total(100), receipt_total(100),
              estimate_total(100), refund_total(100)]
    assert all(v == values[0] for v in values)
    # float-tolerant anchor: 100 * (1 + 0.10) is not exactly 110.0
    assert abs(values[0] - 110.0) < 1e-6
"""

PROMPT = """\
The project in /workspace computes prices in several service modules under
services/. The single source of truth for the tax rate is config.py
(TAX_RATE = 0.10), but some modules drifted and hardcode stale values.

Find every module that is inconsistent with config.py and fix it so all
modules use the same rate as config.py. Do not modify config.py or the tests.
Run tests with: python -m pytest
"""


class TaskContextManage(BenchTask):
    id = "context_manage_v1"
    allowed_writes = ["services/*.py"]
    max_turns = 25

    async def setup(self, sandbox) -> None:
        await sandbox.write_file("/workspace/config.py", CONFIG)
        for path, body in MODULES.items():
            await sandbox.write_file(f"/workspace/{path}", body)
        await sandbox.write_file("/workspace/test_consistency.py", TEST)

    def get_prompt(self) -> str:
        return PROMPT

    async def _eval_all_files_surveyed(self, ctx: BenchContext):
        # Every stale module was read (or listed then read) before completion.
        stale = ["invoice.py", "receipt.py", "estimate.py"]
        turns = []
        for name in stale:
            reads = ctx.read_turns(name)
            if not reads:
                return None
            turns.append(reads[0].turn)
        return max(turns)

    async def _eval_consistency(self, ctx: BenchContext):
        code, _, _ = await ctx.harness_exec("python -m pytest -q")
        return code == 0

    async def _eval_no_read_loops(self, ctx: BenchContext):
        counts: dict[str, int] = {}
        for log in ctx.turn_logs:
            if log.tool_used == "read_file":
                p = str(log.args.get("path", ""))
                counts[p] = counts.get(p, 0) + 1
        return all(c <= READ_REPEAT_LIMIT for c in counts.values())

    @property
    def skills(self) -> list[Skill]:
        return [
            Skill("files_surveyed", "read every inconsistent module",
                  self._eval_all_files_surveyed),
            Skill("consistency_fixed", "all modules agree with config.py (harness-verified)",
                  self._eval_consistency),
            Skill("no_read_loops",
                  f"no file read more than {READ_REPEAT_LIMIT} times",
                  self._eval_no_read_loops),
        ]
