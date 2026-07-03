"""TaskSkillDriven (skill_run_v1): mount a real skill directory and measure
whether a model can drive it to completion.

This is the core purpose of the bench: "what is the smallest local model
that runs this skill soundly, end to end?" Flags are declared by the skill
itself in bench_manifest.json, so new skills never touch core code.

bench_manifest.json:
{
  "id": "skill_run_v1_myskill",        // optional, defaults from dir name
  "goal": "one-line goal given to the model",
  "prompt_file": "SKILL.md",           // optional; contents appended to prompt
  "required_reads": ["skill/SKILL.md"],
  "required_commands": ["python3? skill/scripts/gen\\\\.py .*--seed"],
  "expected_artifacts": ["output/result.txt"],
  "allowed_writes": ["output/*"],
  "max_turns": 20,
  "stubs": {
    "scripts/gen.py": {"outputs": ["output/result.txt"]}
  }
}

Stubs replace heavy real scripts (image generation etc.) with a recorder
that logs its argv and touches dummy outputs. The bench measures whether the
model drives the skill's steps correctly — not the quality of the outputs.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from .base import BenchTask, BenchContext, Skill, DEFAULT_IGNORE

STUB_LOG = "/workspace/.stub_calls.log"

STUB_TEMPLATE = """\
#!/bin/sh
# agent-bench stub: records argv, produces dummy outputs.
printf '%s %s\\n' "{name}" "$*" >> {log}
{touches}
exit 0
"""


class TaskSkillDriven(BenchTask):
    id = "skill_run_v1"
    max_turns = 20

    def __init__(self, skill_dir: str):
        self.skill_dir = Path(skill_dir)
        manifest_path = self.skill_dir / "bench_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"bench_manifest.json not found in {skill_dir}")
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.manifest = m
        self.id = m.get("id", f"skill_run_v1_{self.skill_dir.name}")
        self.goal = m.get("goal", "Execute the skill as documented.")
        self.prompt_file = m.get("prompt_file")
        self.required_reads = m.get("required_reads", [])
        self.required_commands = m.get("required_commands", [])
        self.expected_artifacts = m.get("expected_artifacts", [])
        # Deterministic structural assertions on artifact contents:
        # [{"file": "output/index.html", "contains": ["<table", ...]}]
        # "Does it look similar" is left to the human via the dashboard
        # preview; these flags only pin down the machine-checkable contract.
        self.artifact_checks = m.get("artifact_checks", [])
        self.allowed_writes = m.get("allowed_writes", []) + [".stub_calls.log"]
        self.max_turns = m.get("max_turns", 20)
        self.stubs = m.get("stubs", {})
        self.ignore_patterns = DEFAULT_IGNORE + [".stub_calls.log"]

    async def setup(self, sandbox) -> None:
        await sandbox.copy_in(str(self.skill_dir), "/workspace/skill")
        # Manifest must not leak into the sandbox: it declares the flags.
        await sandbox.execute("rm -f /workspace/skill/bench_manifest.json")
        for rel_path, spec in self.stubs.items():
            touches = []
            for out in spec.get("outputs", []):
                touches.append(f'mkdir -p "$(dirname /workspace/{out})"')
                touches.append(f'echo "stub output" > /workspace/{out}')
            stub = STUB_TEMPLATE.format(
                name=rel_path, log=STUB_LOG, touches="\n".join(touches))
            await sandbox.write_file(f"/workspace/skill/{rel_path}", stub)
            await sandbox.execute(f"chmod +x /workspace/skill/{rel_path}")

    def get_prompt(self) -> str:
        prompt = (
            f"A skill is installed at /workspace/skill/. Your goal: {self.goal}\n"
            "Read the skill's documentation and follow its steps exactly to "
            "accomplish the goal. Work inside /workspace."
        )
        if self.prompt_file:
            body = (self.skill_dir / self.prompt_file).read_text(encoding="utf-8")
            prompt += f"\n\n--- {self.prompt_file} ---\n{body}"
        return prompt

    async def _stub_log(self, ctx: BenchContext) -> str:
        code, out, _ = await ctx.harness_exec(f"cat {STUB_LOG}")
        return out if code == 0 else ""

    def _make_read_eval(self, path: str):
        async def _eval(ctx: BenchContext):
            reads = ctx.read_turns(path.split("/")[-1])
            return reads[0].turn if reads else None
        return _eval

    def _make_command_eval(self, pattern: str):
        regex = re.compile(pattern)
        async def _eval(ctx: BenchContext):
            for log in ctx.turn_logs:
                if log.tool_used == "execute_command" and \
                        regex.search(str(log.args.get("command", ""))):
                    return log.turn
            # Also match against stub-recorded argv (arg fidelity even when
            # the command line went through wrappers).
            stub_log = await self._stub_log(ctx)
            return True if regex.search(stub_log) else None
        return _eval

    def _make_artifact_eval(self, path: str):
        async def _eval(ctx: BenchContext):
            return path in ctx.final_snapshot or \
                ctx.first_change_turn(path) is not None
        return _eval

    def _make_contains_eval(self, path: str, needle: str):
        async def _eval(ctx: BenchContext):
            # Fixed-string grep against the artifact: deterministic and
            # shell-injection-safe via quoting.
            code, _, _ = await ctx.harness_exec(
                f"grep -qF {shlex.quote(needle)} {shlex.quote('/workspace/' + path)}")
            return code == 0
        return _eval

    async def _eval_no_hallucination(self, ctx: BenchContext):
        return not any(log.invalid_tool_call for log in ctx.turn_logs)

    async def _eval_step_completion(self, ctx: BenchContext):
        # All expected artifacts exist: the multi-step procedure was not
        # abandoned partway (a dominant small-model failure mode).
        return all(p in ctx.final_snapshot for p in self.expected_artifacts)

    @property
    def skills(self) -> list[Skill]:
        skills: list[Skill] = []
        for p in self.required_reads:
            skills.append(Skill(f"read:{p}", f"read {p}", self._make_read_eval(p)))
        for i, pat in enumerate(self.required_commands):
            skills.append(Skill(f"cmd:{i}", f"ran command matching /{pat}/",
                                self._make_command_eval(pat)))
        for p in self.expected_artifacts:
            skills.append(Skill(f"artifact:{p}", f"produced {p}",
                                self._make_artifact_eval(p)))
        for chk in self.artifact_checks:
            for needle in chk.get("contains", []):
                label = needle if len(needle) <= 24 else needle[:23] + "…"
                skills.append(Skill(
                    f"check:{label}",
                    f"{chk['file']} contains {needle!r}",
                    self._make_contains_eval(chk["file"], needle)))
        skills.append(Skill("no_tool_hallucination",
                            "never called a nonexistent tool",
                            self._eval_no_hallucination))
        skills.append(Skill("step_completion",
                            "all expected artifacts exist (procedure completed)",
                            self._eval_step_completion))
        return skills
