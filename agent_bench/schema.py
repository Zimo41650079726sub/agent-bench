"""Result dataclasses. All output is schema-versioned JSON."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict

from . import SCHEMA_VERSION


@dataclass
class TurnLog:
    turn: int
    tool_used: str
    args: dict
    stdout: str
    stderr: str
    exit_code: int
    elapsed_sec: float
    invalid_tool_call: bool = False


@dataclass
class SkillResult:
    skill_id: str
    reached: bool
    # Turn on which the flag condition was first met. None if never reached.
    reached_turn: int | None = None
    # Cumulative wall time up to the end of reached_turn. None if never reached.
    elapsed_sec: float | None = None


@dataclass
class BenchResult:
    schema_version: str
    task_id: str
    skill_ids: list[str]
    model: str
    trial_index: int
    passed: bool
    score: float
    turns_taken: int
    total_elapsed_sec: float
    invalid_tool_call_count: int
    skill_results: list[SkillResult]
    turn_logs: list[TurnLog]
    tamper_detected: bool
    failure_reason: str | None
    environment: dict
    # What the model *said* (not trusted for scoring; kept for post-hoc
    # analysis of failures like "answered in prose instead of using tools").
    assistant_notes: list[dict] = field(default_factory=list)
    final_text: str | None = None
    # Files the model legitimately produced (allowed_writes), exported from
    # the container before cleanup. artifacts_dir is relative to the results
    # dir; artifacts are workspace-relative paths inside it.
    artifacts_dir: str | None = None
    artifacts: list[str] = field(default_factory=list)
    # Opt-in (run_trial(capture_messages=True)): the full conversation as
    # sent to the LLM, plus the raw `choices[0]` dict per assistant turn
    # (index-aligned with assistant messages). For RL training consumers
    # (ART); None on normal bench runs and omitted from the JSON output.
    messages_history: list[dict] | None = None
    raw_choices: list[dict] | None = None
    result_hash: str = ""

    def compute_hash(self) -> str:
        """SHA256 (first 16 hex chars) over the primary result fields.

        This is an integrity/identity check for corrupted or duplicated
        submissions. It is NOT a signature and does not prevent forgery.
        messages_history/raw_choices are deliberately excluded: capturing
        them must not change the hash of an otherwise identical run.
        """
        core = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "model": self.model,
            "trial_index": self.trial_index,
            "passed": self.passed,
            "score": self.score,
            "turns_taken": self.turns_taken,
            "skill_results": [asdict(s) for s in self.skill_results],
            "tamper_detected": self.tamper_detected,
            "environment": self.environment,
        }
        blob = json.dumps(core, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def finalize(self) -> "BenchResult":
        self.result_hash = self.compute_hash()
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        # Keep result JSON byte-identical to pre-capture versions unless
        # capture was actually requested.
        if self.messages_history is None:
            d.pop("messages_history")
        if self.raw_choices is None:
            d.pop("raw_choices")
        return d


def make_environment(
    *,
    image: str,
    base_url: str,
    model: str,
    adapter: str,
    sampling: dict,
    server: str = "unknown",
) -> dict:
    """Everything needed to reproduce a run. Sampling params are mandatory:
    even at temperature=0 local servers are not guaranteed deterministic, so
    the bench's stance is 'fix and record the params, then treat pass^k
    variance itself as the stability metric'."""
    return {
        "schema_version": SCHEMA_VERSION,
        "sandbox_image": image,
        "base_url": base_url,
        "model": model,
        "adapter": adapter,
        "server": server,
        "sampling": sampling,
    }
