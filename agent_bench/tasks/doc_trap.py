"""TaskDocTrap (doc_trap_v1): the first lead is guaranteed to miss.

Targets a measured failure mode (gemma-4-e4b, landing-page runs): the model
read a hallucinated path, got an error on turn 1, and abandoned the whole
task immediately — three trials out of three. Here README.md points at
docs/SETUP.md, which does not exist; the real document lives elsewhere.
The task measures whether a failed probe is followed by exploration and
recovery, or by giving up.
"""

from __future__ import annotations

import re

from .base import BenchTask, BenchContext, Skill

DEPLOY_KEY = "DEPLOY_KEY=zx91-tango"

README = """\
# acme-deploy

Deployment helper for the Acme staging cluster.

## Setup

Full setup instructions live in docs/SETUP.md.
"""

SETUP_GUIDE = f"""\
Acme staging setup (moved out of docs/ during the 2025 repo cleanup)

1. Install the CLI:            pip install acme-deploy
2. Authenticate the pipeline:  export the deployment key below
   {DEPLOY_KEY}
3. Verify:                     acme-deploy ping --env staging
"""

APP = """\
import os

def deploy_key() -> str:
    return os.environ.get("DEPLOY_KEY", "")
"""

PROMPT = """\
Prepare the deployment config for the project in /workspace.
Write output/deploy.env containing the correct DEPLOY_KEY line for this
project. The project's setup documentation explains the key.
Start from README.md.
"""

EXPLORE_RE = re.compile(r"(^|[;&|]\s*|\b)(ls|find|tree)\b|grep\s+-[a-zA-Z]*r")


class TaskDocTrap(BenchTask):
    id = "doc_trap_v1"
    allowed_writes = ["output/*"]
    max_turns = 12

    async def setup(self, sandbox) -> None:
        await sandbox.write_file("/workspace/README.md", README)
        await sandbox.write_file("/workspace/notes/setup-guide.txt", SETUP_GUIDE)
        await sandbox.write_file("/workspace/src/app.py", APP)

    def get_prompt(self) -> str:
        return PROMPT

    async def _eval_explored(self, ctx: BenchContext):
        # Recovery behavior: after (or instead of) the doomed docs/SETUP.md
        # probe, the model actually surveyed the workspace.
        for log in ctx.turn_logs:
            if log.tool_used == "execute_command" and \
                    EXPLORE_RE.search(str(log.args.get("command", ""))):
                return log.turn
        return None

    async def _eval_found_doc(self, ctx: BenchContext):
        reads = ctx.read_turns("setup-guide.txt")
        return reads[0].turn if reads else None

    async def _eval_key_correct(self, ctx: BenchContext):
        code, _, _ = await ctx.harness_exec(
            f"grep -qF '{DEPLOY_KEY}' /workspace/output/deploy.env")
        return code == 0

    @property
    def skills(self) -> list[Skill]:
        return [
            Skill("explored_listing",
                  "surveyed the workspace (ls/find/tree/grep -r)",
                  self._eval_explored),
            Skill("found_doc", "read the real setup document",
                  self._eval_found_doc),
            Skill("key_correct",
                  "output/deploy.env contains the correct DEPLOY_KEY",
                  self._eval_key_correct),
        ]
