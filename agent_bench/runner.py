"""run_trial / run_pass_k / detect_tampering.

Trials run sequentially by default: firing parallel requests at a local
inference server contaminates elapsed_sec through slot contention and cache
state. --parallel exists only for runs whose timings will not be compared.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .adapters import LLMAdapter, tool_call_to_openai
from .sandbox import StatefulSandbox
from .schema import BenchResult, SkillResult, TurnLog
from .tasks.base import BenchContext, BenchTask

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Run a shell command in the sandbox. State (files, cwd) persists between calls.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file and return its contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
]

VALID_TOOLS = {t["function"]["name"] for t in TOOLS_SPEC}


def detect_tampering(task: BenchTask, setup: dict[str, str],
                     final: dict[str, str]) -> list[str]:
    """Whole-workspace diff against the task's allowed_writes. Catches
    cheats that never touch the test file itself (adding conftest.py,
    deleting fixtures, editing helper modules)."""
    violations = []
    for path, digest in final.items():
        if task.is_ignored(path):
            continue
        if path not in setup:
            if not task.is_write_allowed(path):
                violations.append(f"new file: {path}")
        elif setup[path] != digest and not task.is_write_allowed(path):
            violations.append(f"modified: {path}")
    for path in setup:
        if path not in final and not task.is_ignored(path) \
                and not task.is_write_allowed(path):
            violations.append(f"deleted: {path}")
    return violations


async def _dispatch_tool(sandbox: StatefulSandbox, name: str, args: dict) -> tuple[int, str, str]:
    # Crisp required-argument errors: models (especially mid-size ones
    # generating long content) drop small args like 'path' — a raw shell
    # error gives them nothing to recover with.
    if name == "execute_command":
        cmd = str(args.get("command", "")).strip()
        if not cmd:
            return 1, "", "Error: execute_command requires a non-empty 'command' argument."
        return await sandbox.execute(cmd)
    if name == "read_file":
        path = str(args.get("path", "")).strip()
        if not path:
            return 1, "", "Error: read_file requires a 'path' argument."
        return await sandbox.read_file(path)
    if name == "write_file":
        path = str(args.get("path", "")).strip()
        if not path:
            return 1, "", ("Error: write_file requires a 'path' argument "
                           "(e.g. {\"path\": \"output/index.html\", \"content\": ...}). "
                           "Your content was NOT saved — call write_file again with both arguments.")
        return await sandbox.write_file(path, str(args.get("content", "")))
    raise ValueError(name)


async def run_trial(task: BenchTask, adapter: LLMAdapter, *,
                    image: str, trial_index: int, model: str,
                    environment: dict, command_timeout: float = 60.0,
                    on_event=None, export_dir: Path | None = None,
                    export_rel: str | None = None,
                    capture_messages: bool = False) -> BenchResult:
    def emit(**ev):
        if on_event:
            on_event({"trial": trial_index, **ev})

    sandbox = StatefulSandbox(image=image, command_timeout=command_timeout)
    turn_logs: list[TurnLog] = []
    assistant_notes: list[dict] = []
    final_text: str | None = None
    file_first_turn: dict[str, int] = {}
    setup_snapshot: dict[str, str] = {}
    final_snapshot: dict[str, str] = {}
    failure_reason: str | None = None
    turn_elapsed_cumulative: list[float] = []
    messages: list[dict] = []
    # One raw choice per assistant turn, in order (capture_messages only).
    raw_choices: list[dict] | None = [] if capture_messages else None
    t_start = time.monotonic()

    try:
        emit(type="trial_start")
        await sandbox.start()
        await task.setup(sandbox)
        setup_snapshot = await sandbox.snapshot_workspace()
        prev_snapshot = dict(setup_snapshot)

        messages.append({"role": "user", "content": task.get_prompt()})

        for turn in range(1, task.max_turns + 1):
            try:
                completion = await adapter.complete(messages, TOOLS_SPEC)
            except Exception as exc:  # noqa: BLE001
                # An LLM-side failure (timeout, 5xx) ends the trial but is
                # scored from the flags reached so far — zeroing everything
                # would erase what the model verifiably did do.
                failure_reason = f"llm_error: {type(exc).__name__}: {exc}"
                emit(type="llm_error", turn=turn, error=str(exc))
                break
            if capture_messages:
                raw_choices.append(completion.raw_choice or {})
            if completion.text and completion.text.strip():
                assistant_notes.append({"turn": turn,
                                        "text": completion.text[:4000]})
                emit(type="note", turn=turn, text=completion.text[:200])
            if not completion.tool_calls:
                final_text = completion.text
                if capture_messages:
                    # The tool-call loop never appends the final answer;
                    # the captured history must still end with it so
                    # raw_choices stays index-aligned with assistant turns.
                    messages.append({"role": "assistant",
                                     "content": completion.text})
                break

            assistant_msg = {
                "role": "assistant",
                "content": completion.text,
                "tool_calls": [tool_call_to_openai(tc) for tc in completion.tool_calls],
            }
            messages.append(assistant_msg)

            for tc in completion.tool_calls:
                t0 = time.monotonic()
                if tc.name not in VALID_TOOLS or tc.arguments is None:
                    # Tool-name hallucination / unparseable arguments: never
                    # executed, logged as invalid, error surfaced to the model.
                    # This is the measurement point for small-model failures.
                    err = (f"Error: unknown tool '{tc.name}'"
                           if tc.name not in VALID_TOOLS
                           else "Error: arguments were not valid JSON")
                    turn_logs.append(TurnLog(
                        turn=turn, tool_used=tc.name,
                        args={"raw": tc.raw_arguments}, stdout="", stderr=err,
                        exit_code=-1, elapsed_sec=0.0, invalid_tool_call=True,
                    ))
                    emit(type="tool", turn=turn, tool=tc.name, arg="",
                         exit=-1, invalid=True)
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": err})
                    continue
                code, out, errout = await _dispatch_tool(sandbox, tc.name, tc.arguments)
                elapsed = time.monotonic() - t0
                turn_logs.append(TurnLog(
                    turn=turn, tool_used=tc.name, args=tc.arguments,
                    stdout=out[-8000:], stderr=errout[-4000:],
                    exit_code=code, elapsed_sec=round(elapsed, 3),
                ))
                arg_summary = str(tc.arguments.get("command")
                                  or tc.arguments.get("path") or "")[:160]
                emit(type="tool", turn=turn, tool=tc.name, arg=arg_summary,
                     exit=code, invalid=False)
                content = f"exit_code: {code}\nstdout:\n{out[-4000:]}"
                if errout.strip():
                    content += f"\nstderr:\n{errout[-2000:]}"
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": content})

            # Per-turn snapshot: file first-appearance is judged from actual
            # workspace state, so shell-side writes can't slip past
            # write_file-based ordering checks.
            snap = await sandbox.snapshot_workspace()
            for path, digest in snap.items():
                if path not in file_first_turn and \
                        (path not in setup_snapshot or setup_snapshot[path] != digest):
                    file_first_turn[path] = turn
            prev_snapshot = snap
            turn_elapsed_cumulative.append(time.monotonic() - t_start)
        else:
            failure_reason = "max_turns_exhausted"

        final_snapshot = prev_snapshot if turn_elapsed_cumulative else \
            await sandbox.snapshot_workspace()

        ctx = BenchContext(
            sandbox=sandbox, turn_logs=turn_logs,
            file_first_turn=file_first_turn,
            setup_snapshot=setup_snapshot, final_snapshot=final_snapshot,
        )

        # Evaluators return: False/None = not reached; an int = reached on
        # that turn; True = reached but only observable post-hoc (harness
        # ground-truth checks), so no turn is attributed.
        skill_results = []
        for skill in task.skills:
            verdict = await skill.evaluator(ctx)
            reached = bool(verdict)
            reached_turn = verdict if isinstance(verdict, int) and not isinstance(verdict, bool) else None
            elapsed = None
            if reached_turn and reached_turn <= len(turn_elapsed_cumulative):
                elapsed = round(turn_elapsed_cumulative[reached_turn - 1], 3)
            skill_results.append(SkillResult(
                skill_id=skill.id, reached=reached,
                reached_turn=reached_turn, elapsed_sec=elapsed,
            ))

        violations = detect_tampering(task, setup_snapshot, final_snapshot)
        tamper = bool(violations)
        if tamper:
            failure_reason = f"tampering: {'; '.join(violations[:5])}"

        # Export what the model legitimately produced, so outputs (pages,
        # images, reports) survive container cleanup and can be previewed.
        artifacts: list[str] = []
        if export_dir is not None:
            for path in sorted(final_snapshot):
                changed = path not in setup_snapshot or \
                    setup_snapshot[path] != final_snapshot[path]
                if changed and task.is_write_allowed(path) \
                        and not task.is_ignored(path):
                    dest = export_dir / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if await sandbox.copy_out(f"/workspace/{path}", str(dest)):
                        artifacts.append(path)

        reached_count = sum(1 for s in skill_results if s.reached)
        passed = reached_count == len(skill_results) and not tamper
        emit(type="trial_end", passed=passed,
             score=round(reached_count / max(len(task.skills), 1), 2),
             turns=max((log.turn for log in turn_logs), default=0))

    except Exception as exc:  # noqa: BLE001 - a broken trial is a result, not a crash
        skill_results = [SkillResult(skill_id=s.id, reached=False) for s in task.skills]
        tamper = False
        passed = False
        reached_count = 0
        artifacts = []
        failure_reason = f"harness_error: {type(exc).__name__}: {exc}"
    finally:
        await sandbox.cleanup()

    turns_taken = max((log.turn for log in turn_logs), default=0)
    return BenchResult(
        schema_version=SCHEMA_VERSION,
        task_id=task.id,
        skill_ids=[s.id for s in task.skills],
        model=model,
        trial_index=trial_index,
        passed=passed,
        score=round(reached_count / max(len(task.skills), 1), 4),
        turns_taken=turns_taken,
        total_elapsed_sec=round(time.monotonic() - t_start, 3),
        invalid_tool_call_count=sum(1 for log in turn_logs if log.invalid_tool_call),
        skill_results=skill_results,
        turn_logs=turn_logs,
        tamper_detected=tamper,
        failure_reason=failure_reason,
        environment=environment,
        assistant_notes=assistant_notes,
        final_text=final_text,
        artifacts_dir=export_rel if artifacts else None,
        artifacts=artifacts,
        messages_history=[dict(m) for m in messages] if capture_messages else None,
        raw_choices=raw_choices,
    ).finalize()


async def run_pass_k(task: BenchTask, adapter_factory, *, k: int,
                     image: str, model: str, environment: dict,
                     parallel: bool = False,
                     command_timeout: float = 60.0,
                     results_dir: str = "results",
                     on_event=None) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = "".join(c if c.isalnum() or c in "._-" else "_" for c in model)
    run_name = f"{task.id}_{safe_model}_{ts}"

    async def one(i: int) -> BenchResult:
        export_rel = f"artifacts/{run_name}/trial{i}"
        return await run_trial(task, adapter_factory(), image=image,
                               trial_index=i, model=model,
                               environment=environment,
                               command_timeout=command_timeout,
                               on_event=on_event,
                               export_dir=Path(results_dir) / export_rel,
                               export_rel=export_rel)

    if parallel:
        results = list(await asyncio.gather(*(one(i) for i in range(k))))
    else:
        results = [await one(i) for i in range(k)]

    per_skill: dict[str, dict] = {}
    for sid in results[0].skill_ids:
        hits = [s for r in results for s in r.skill_results if s.skill_id == sid]
        reached = [s for s in hits if s.reached]
        turns = [s.reached_turn for s in reached if s.reached_turn is not None]
        per_skill[sid] = {
            "reach_rate": round(len(reached) / len(hits), 4),
            "avg_turns": round(sum(turns) / len(turns), 2) if turns else None,
        }

    summary = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task.id,
        "model": model,
        "k": k,
        "parallel": parallel,
        "pass_hat_k": round(sum(1 for r in results if r.passed) / k, 4),
        "pass_all_k": all(r.passed for r in results),
        "avg_score": round(sum(r.score for r in results) / k, 4),
        "avg_turns": round(sum(r.turns_taken for r in results) / k, 2),
        "avg_elapsed_sec": round(sum(r.total_elapsed_sec for r in results) / k, 2),
        "tamper_detected_count": sum(1 for r in results if r.tamper_detected),
        "invalid_tool_call_count": sum(r.invalid_tool_call_count for r in results),
        "skills": per_skill,
        "environment": environment,
        "trials": [r.to_dict() for r in results],
    }

    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"result_{task.id}_{ts}.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    summary["_output_file"] = str(out_path)
    return summary


def print_summary(summary: dict) -> None:
    print(f"\n=== {summary['task_id']} | {summary['model']} | k={summary['k']} ===")
    print(f"pass^k (all pass): {summary['pass_all_k']}   "
          f"pass rate: {summary['pass_hat_k']:.0%}")
    print(f"avg_score: {summary['avg_score']:.2f}   "
          f"avg_turns: {summary['avg_turns']}   "
          f"avg_elapsed: {summary['avg_elapsed_sec']}s")
    print(f"tamper: {summary['tamper_detected_count']}   "
          f"invalid tool calls: {summary['invalid_tool_call_count']}")
    print("skills:")
    for sid, st in summary["skills"].items():
        avg_t = st["avg_turns"] if st["avg_turns"] is not None else "-"
        print(f"  {sid:<24} reach {st['reach_rate']:>6.0%}   avg_turns {avg_t}")
    print(f"-> {summary['_output_file']}")
