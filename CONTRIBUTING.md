# Contributing

## Flag design rules

1. **Deterministic only.** A flag must be decidable from exit codes, file
   first-appearance order, hashes, or logs — by Python, with no judgment
   calls. Reasoning quality, style, language mixing and the like cannot be
   machine-judged and therefore are never flags. (Raw `turn_logs` are kept in
   the results JSON for human post-hoc analysis.)
2. **Never trust the model's self-report.** "All tests pass!" is not
   evidence. Ground truth comes from the harness re-running the check
   (`BenchContext.harness_exec`). A model turn's exit code can be faked with
   `cmd || true`.
3. **Thresholds are part of the spec.** If a flag needs a cutoff (e.g. "same
   file read at most N times"), the value lives in the task definition and is
   thereby recorded and reproducible — not decided at scoring time.
4. **Observation caveats are stated, not hidden.** e.g. `file_read` observes
   *tool usage* (read_file or cat/head/grep/sed on the file), not
   comprehension. Say so in the skill description.

## Schema

- Never break backward compatibility of `schema_version` "1.0" fields; add
  fields, bump the version, keep old readers working.

## Tasks

- New tasks subclass `BenchTask` and must not touch the run loop, sandbox, or
  scoring infrastructure. If you need a new capability from the core, open an
  issue first.
- Declare `allowed_writes` tightly: everything else is tamper territory.
- Model-specific tool-call formats belong in an `LLMAdapter`, never in the
  run loop.

## Before submitting results

Run `python3 tests/verify_harness.py` — all checks must pass on your machine.
