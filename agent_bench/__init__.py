"""agent-bench: deterministic agent-skill benchmark for local LLMs."""

# 1.1: added assistant_notes[] and final_text to BenchResult (additive only).
# Note: run_trial(capture_messages=True) adds messages_history/raw_choices to
# BenchResult. Opt-in, omitted from JSON and result_hash otherwise — no bump.
SCHEMA_VERSION = "1.1"

from .runner import TOOLS_SPEC  # noqa: E402,F401 — re-export for RL consumers (ART)
