# agent-bench

English | [日本語](README.ja.md)

**Find the smallest local LLM that can run your agent skill soundly, end to end.**
agent-bench measures multi-step agentic ability with *deterministic* scoring only —
exit codes, file first-appearance order, SHA256 hashes. No LLM judge, fully local,
reproducible. Each measured ability is a "flag" (orienteering-style): the bench
reports whether the model reached each flag, how fast, and how consistently (pass^k).
Point it at any OpenAI-compatible server (llama-server / Ollama / LM Studio), or
mount a real skill directory and get a verdict like *"this skill needs at least
Q6 of model X."*

## Requirements

- Python 3.12+ (stdlib only — no pip installs for the core)
- Docker (sandbox containers)

## Run

```bash
docker build -t agent-bench:latest .
python3 tests/verify_harness.py   # self-check without any LLM (mock model)
python3 cli.py --model YOUR_MODEL --task debug_python_v1 --k 3 \
    --base-url http://localhost:8080/v1
```

Run the whole bench in one invocation:

```bash
python3 cli.py --model YOUR_MODEL --tasks all --early-stop \
    --base-url http://localhost:8080/v1
```

- `--tasks all` (or a comma list of task ids) runs every builtin task plus
  the bundled skills, heavy artifact tasks last, with a batch summary table
  at the end.
- `--early-stop` skips the remaining trials of a task after the first
  failure — one failed trial already decides pass^k, so the repeats only
  cost time (a failing heavyweight task can burn hours otherwise). The
  summary records `early_stopped` and `trials_run`.
- Sequential trials reuse one sandbox container and reset it in between
  (processes killed, workspace wiped; tamper detection is anchored on the
  per-trial post-reset snapshot). `--fresh-container` restores the
  one-container-per-trial behavior.

To benchmark a real skill:

```bash
python3 cli.py --model YOUR_MODEL --task skill_run_v1 \
    --skill-dir ./examples/skills/report-skill --k 5 \
    --base-url http://localhost:8080/v1
```

## Dashboard

```bash
python3 webui.py          # -> http://127.0.0.1:8765/
```

Serves a local dashboard over `results/` — stdlib only, light and dark mode:

- **Run panel** — pick a model (fetched live from your OpenAI-compatible
  server), a task or an installed skill, and k; watch the run stream
  turn-by-turn in a live log. One run at a time.
- Model × task matrix (pass^k, score, turns, elapsed, tamper, hallucinated
  calls) and per-skill reach-rate charts, newest file per (task, model).
- **Click a row for the full transcript**: every tool call with exit code
  and unfoldable stdout/stderr, what the model said between calls, and its
  final answer.
- **Artifact previews** — files the model legitimately produced are exported
  from the container before cleanup; HTML artifacts render inline in a
  sandboxed iframe, images inline too. Compare what each model actually
  built, side by side (see `examples/skills/landing-page`).

## Measured results

Environment: Snapdragon X Elite, 64 GB RAM, LM Studio (llama.cpp backend),
sandbox = Docker on WSL2. Wall times are relative to this hardware; treat
them as ratios, not absolutes. Quantizations: qwen3.5-4b = Unsloth
UD-Q4_K_XL, qwen3.6-35b-a3b = Unsloth UD-Q6_K_XL, Gemma 4 = official
GGUF releases (e4b/12b QAT, 26b-a4b instruct). Generated with
`scripts/make_matrix.py`.

| task | gemma-4-26b-a4b-it | gemma-4-e2b-it-qat | gemma-4-e4b-it-qat | gemma-4-12b-qat | qwen3.5-4b | qwen3.6-35b-a3b |
|---|---|---|---|---|---|---|
| context_manage_v1 | ✅ pass^3 · 10.0t · 93s | ✅ pass^3 · 11.0t · 51s | ✅ pass^3 · 8.0t · 24s | ✅ pass^3 · 12.0t · 5.5m | ✅ pass^3 · 11.0t · 52s | ✅ pass^3 · 5.0t · 2.0m |
| debug_python_v1 | ✅ pass^3 · 5.0t · 58s | ✅ pass^3 · 4.0t · 15s | ✅ pass^3 · 5.0t · 20s | ✅ pass^3 · 5.0t · 87s | ✗ 0.75 (k=10) · 17s | ✅ pass^3 · 4.0t · 53s |
| skill_run_v1_imitate_dashboard | ✅ pass^1 · 3.0t · 7.8m | ✅ pass^1 · 2.0t · 2.8m | ✅ pass^1 · 3.0t · 7.8m | ✗ 0.20 (k=1) · 181.3m | ✅ pass^1 · 6.0t · 6.0m | ✅ pass^1 · 12.0t · 32.6m |
| skill_run_v1_landing_page | ✅ pass^1 · 4.0t · 2.1m | ✅ pass^1 · 5.0t · 39s | ✗ 0.25 (k=3) · 4s | ✅ pass^1 · 5.0t · 2.7m | ✅ pass^1 · 6.0t · 94s | ✅ pass^1 · 4.0t · 4.5m |
| skill_run_v1_report | ✅ pass^3 · 4.0t · 53s | ✅ pass^3 · 5.0t · 14s | ✅ pass^3 · 5.0t · 21s | ✅ pass^3 · 4.0t · 68s | ✅ pass^3 · 6.0t · 20s | ✅ pass^3 · 5.67t · 62s |
| tdd_order_v1 | ✅ pass^3 · 4.0t · 74s | ✗ 0.44 (k=3) · 25s | ✗ 0.67 (k=3) · 28s | ✅ pass^3 · 6.0t · 4.0m | ✅ pass^3 · 4.0t · 49s | ✅ pass^3 · 4.0t · 96s |

`✅ pass^k · Nt · T` = all k trials passed, avg N turns, avg wall time per
trial. `✗ S` = avg score S with at least one failed trial. Newest result
per (task, model).

### Model tiers (derived from flags)

| tier | definition | models |
|---|---|---|
| 1 | **Full discipline** — every task passes, process flags included | gemma-4-26b-a4b-it (23.9m), qwen3.6-35b-a3b (53.7m) |
| 2 | **Outcome-capable** — completes tasks but drops process flags (e.g. never observes the failure before fixing) | gemma-4-e2b-it-qat (8.7m), gemma-4-e4b-it-qat (12.6m), qwen3.5-4b (16.4m), gemma-4-12b-qat (220.4m) |
| 3 | **Cannot drive tools** — the agentic loop itself does not run | — |

Within each tier, models are ordered by total clear time (sum over benched
tasks, all trials).

The interesting part is how *specific* and *reproducible* each failure is —
capability is not monotonic in parameter count:

- **gemma-4-e2b-it-qat (2B)** passes 5/6 tasks — including both artifact
  skills that trip its larger siblings (the landing page e4b quits, the
  dashboard 12b collapses on) — and is the fastest model on the board
  (8.7 min total). Its single gap is pure TDD discipline: it writes the
  test, then implements without ever running the test to observe it fail
  (`red_observed` 0/3).
- **gemma-4-12b-qat** passes 5/6 tasks but collapses on the dashboard task:
  when the HTML payload gets large, it stops emitting the `path` argument of
  `write_file` — 9 consecutive times, even right after correctly diagnosing
  its own mistake ("the error was due to a missing `path` argument").
  Meanwhile the much smaller e4b clears the same task in 3 turns.
- **gemma-4-e4b-it-qat** clears the heavyweight dashboard task but quits the
  *easier* landing-page skill at turn 1 (hallucinates a `.documentation`
  path, reads it, gives up — identically in 3/3 trials), and fails TDD by
  writing a fizzbuzz stub *inside its own test file*, then can't escape the
  red state it created.
- **qwen3.5-4b** drops exactly one flag across the whole bench: it edits the
  buggy file without ever running the code to observe the failure first
  (`error_interpret`), a pure process-discipline miss.

## The failure-luring set — six tasks built from those failures

Each failure above is deterministic enough to reproduce — so each one became
a task designed to *provoke* it. The original six tasks saturate near the
top (mid-size models pass everything); this second set restores a gradient,
because every trap below is something a real model measurably fell into:

| task | measured failure it targets | the trap |
|---|---|---|
| `big_file_edit_v1` | 12b drops `write_file`'s `path` argument and truncates when regenerating a large file | 4 surgical edits inside a ~29 KB page; anchor sentences detect truncating rewrites, `args_intact` detects the dropped argument |
| `doc_trap_v1` | e4b reads one wrong path, then abandons the task on turn 1 (3/3 trials) | README points at a doc that does not exist; the real one is elsewhere — the first probe is guaranteed to miss |
| `wrong_fix_trap_v1` | Tier-2 models fix without ever observing the failing test | a correct-but-suspicious-looking decoy (with a tempting TODO) sits next to the real bug; skipping pytest steers straight into it — and breaks a passing suite |
| `tdd_strict_v1` | e2b never observes red (0/3); e4b stubs the function inside its own test file | the red phase must leave `red.log` *before* `main.py` exists (file-order check); an AST check rejects test-file stubs |
| `tool_mirage_v1` | LFM2.5-class models copy documentation step names verbatim as tool calls | a bundled skill whose step names (`csv_summarize`, `publish_report`) look like tools; calling one trips `no_tool_hallucination` |
| `long_procedure_v1` | small models abandon multi-step procedures, typically at a long-generation step | a 7-step pipeline with a multi-KB HTML write parked mid-way; per-step flags turn the abandonment point into a score gradient |

Scoring stays fully deterministic (grep / AST / recomputed expectations —
no LLM judge). Scores well below 1.0 are expected here; that is the point.

Compare models across all 12 axes with the radar generator:

```bash
python3 scripts/make_radar.py --models modelA,modelB,modelC   # max 4
```

It writes a dark-mode HTML/SVG radar (plus the value table) from `results/`
and prints the headless-Edge command for a PNG.

## Reading the results JSON

Written to `results/result_{task_id}_{timestamp}.json`:

- `pass_all_k` — every one of the k trials passed (all flags + no tampering)
- `skills.{flag}.reach_rate` / `avg_turns` — per-flag diagnosis of the model's habits
- `invalid_tool_call_count` — tool-name hallucinations (a dominant small-model failure)
- `tamper_detected_count` — trials invalidated by out-of-bounds file changes
- `trials[]` — full per-trial `turn_logs` for post-hoc analysis
- `environment` — sandbox image + **sampling params** (temperature/seed/top_p),
  server, model. Recorded because even temperature=0 is not guaranteed
  deterministic on local servers: the bench's stance is *fix and record the
  params, then treat pass^k variance itself as the stability metric*.
- `result_hash` — integrity check for corrupted/duplicated result files.
  It is not a signature and does not prevent forgery.

## Anti-cheat measures

- Whole-workspace hash diff: any change outside the task's `allowed_writes`
  (including *new* files like a sneaky `conftest.py`) invalidates the trial.
- Verification flags never trust the model's exit codes (`pytest || true`
  fools nothing): the harness independently re-runs the checks.
- File ordering (e.g. test-first) is judged from per-turn workspace snapshots,
  so `echo > file` from the shell counts the same as the write_file tool.

## Adding a task

Subclass `BenchTask` in `agent_bench/tasks/`, define `setup` / `get_prompt` /
`skills` / `allowed_writes`, and register it in `tasks/__init__.py`. The run
loop and evaluation infrastructure are never touched.

## License & sponsorship

agent-bench is free for everyone under the [Apache License 2.0](LICENSE).

If your organization gets real value out of it at commercial scale (rough
guide: annual revenue above ¥1B / ~$10M), please consider sponsoring the
project's development. This is a request, not a license condition — nothing
changes about your rights either way.

## Mounting your own skill

Put a `bench_manifest.json` next to your skill's `SKILL.md` declaring
`required_reads`, `required_commands` (regexes), `expected_artifacts`,
`allowed_writes`, and optional `stubs` that replace heavy scripts (image
generation etc.) with argv recorders — the bench measures whether the model
drives the procedure correctly, not the quality of the outputs. See
`examples/skills/report-skill/`. Two flags are always measured for free:
`no_tool_hallucination` and `step_completion`.
