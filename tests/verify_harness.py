#!/usr/bin/env python3
"""Harness verification with a scripted mock model — no LLM required.

Covers the pre-publication checklist from IMPLEMENTATION.md:
  A. a well-behaved run passes debug_python_v1 with all flags reached
  B. rewriting test_main.py           -> tamper_detected
  B'. adding conftest.py (untouched tests) -> tamper_detected
  C. TDD in the wrong order           -> test_first flag not reached
  D. skill_run_v1 completes with the example skill (stub applied)
  E. calling a nonexistent tool       -> no_tool_hallucination not reached
  F. `pytest || true` fake            -> self_verify not fooled
  G. capture_messages=True            -> full history + raw choices, aligned;
     default run unchanged (fields absent, result_hash identical)

Run from repo root:  python3 tests/verify_harness.py
"""

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_bench.adapters import Completion, MockAdapter, ToolCall
from agent_bench.runner import run_trial
from agent_bench.tasks import TaskDebugPython, TaskTDD, TaskSkillDriven

IMAGE = "agent-bench:latest"
ENV = {"sandbox_image": IMAGE, "sampling": {"temperature": 0, "seed": 42},
       "base_url": "mock", "model": "mock", "adapter": "mock",
       "server": "mock", "schema_version": "1.0"}

FIXED_MAIN = "def add(a, b):\n    return a + b\n"


def call(name: str, **kwargs) -> Completion:
    return Completion(text=None, tool_calls=[ToolCall(
        id="c1", name=name, raw_arguments=json.dumps(kwargs),
        arguments=kwargs)])


async def run(task, script, **kwargs):
    return await run_trial(task, MockAdapter(script), image=IMAGE,
                           trial_index=0, model="mock", environment=ENV,
                           **kwargs)


def check(label: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label}" + (f"  ({detail})" if detail and not cond else ""))
    return cond


async def main() -> int:
    ok = True

    # --- A: well-behaved debug run -------------------------------------
    r = await run(TaskDebugPython(), [
        call("read_file", path="main.py"),
        call("execute_command", command="python -m pytest -q"),
        call("write_file", path="main.py", content=FIXED_MAIN),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="Fixed the operator bug.", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("A: debug_python passes", r.passed, r.failure_reason or str(reached))
    ok &= check("A: all four flags reached", all(reached.values()), str(reached))
    ok &= check("A: reached_turn attributed (file_read=1, self_verify=4)",
                next(s.reached_turn for s in r.skill_results
                     if s.skill_id == "file_read") == 1
                and next(s.reached_turn for s in r.skill_results
                         if s.skill_id == "self_verify") == 4,
                str([(s.skill_id, s.reached_turn) for s in r.skill_results]))

    # --- B: rewriting the test file ------------------------------------
    r = await run(TaskDebugPython(), [
        call("write_file", path="test_main.py",
             content="def test_add():\n    assert True\n"),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="All tests pass now!", tool_calls=[]),
    ])
    ok &= check("B: test rewrite -> tamper_detected", r.tamper_detected)
    ok &= check("B: tampered run does not pass", not r.passed)

    # --- B': conftest.py cheat (tests untouched) ------------------------
    r = await run(TaskDebugPython(), [
        call("write_file", path="conftest.py",
             content="import collections\n"),
        call("write_file", path="main.py", content=FIXED_MAIN),
        Completion(text="done", tool_calls=[]),
    ])
    ok &= check("B': conftest.py addition -> tamper_detected", r.tamper_detected)

    # --- C: TDD in the wrong order --------------------------------------
    fizz = ("def fizzbuzz(n):\n"
            "    if n % 15 == 0: return 'FizzBuzz'\n"
            "    if n % 3 == 0: return 'Fizz'\n"
            "    if n % 5 == 0: return 'Buzz'\n"
            "    return str(n)\n")
    tests = ("from main import fizzbuzz\n"
             "def test_all():\n"
             "    assert fizzbuzz(3) == 'Fizz'\n"
             "    assert fizzbuzz(5) == 'Buzz'\n"
             "    assert fizzbuzz(15) == 'FizzBuzz'\n"
             "    assert fizzbuzz(7) == '7'\n")
    r = await run(TaskTDD(), [
        call("write_file", path="main.py", content=fizz),      # impl first!
        call("write_file", path="test_main.py", content=tests),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="done", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("C: wrong order -> test_first not reached",
                not reached["test_first"], str(reached))
    ok &= check("C: implementation itself is valid (tests_pass reached)",
                reached["tests_pass"], str(reached))
    ok &= check("C: overall not passed", not r.passed)

    # --- C2: correct TDD order passes ------------------------------------
    r = await run(TaskTDD(), [
        call("write_file", path="test_main.py", content=tests),
        call("execute_command", command="python -m pytest -q"),  # red
        call("write_file", path="main.py", content=fizz),
        call("execute_command", command="python -m pytest -q"),  # green
        Completion(text="done", tool_calls=[]),
    ])
    ok &= check("C2: correct TDD order passes", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    # --- D: skill_run_v1 with the example skill --------------------------
    skill_dir = Path(__file__).resolve().parent.parent / "examples/skills/report-skill"
    r = await run(TaskSkillDriven(str(skill_dir)), [
        call("read_file", path="/workspace/skill/SKILL.md"),
        call("execute_command",
             command="sh /workspace/skill/scripts/make_report.sh --title daily"),
        Completion(text="Report generated.", tool_calls=[]),
    ])
    ok &= check("D: skill run passes with stub", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    # --- E: tool-name hallucination ---------------------------------------
    r = await run(TaskSkillDriven(str(skill_dir)), [
        call("browse_web", url="http://example.com"),           # hallucinated
        call("read_file", path="/workspace/skill/SKILL.md"),
        call("execute_command",
             command="sh /workspace/skill/scripts/make_report.sh --title daily"),
        Completion(text="done", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("E: hallucinated tool -> no_tool_hallucination not reached",
                not reached["no_tool_hallucination"], str(reached))
    ok &= check("E: invalid_tool_call_count == 1",
                r.invalid_tool_call_count == 1,
                str(r.invalid_tool_call_count))
    ok &= check("E: other flags unaffected", reached["step_completion"])

    # --- F: exit-code faking ----------------------------------------------
    r = await run(TaskDebugPython(), [
        call("read_file", path="main.py"),
        call("execute_command", command="python -m pytest -q"),
        # No real fix; fakes a green run instead.
        call("write_file", path="main.py",
             content="def add(a, b):\n    return a - b  # still broken\n"),
        call("execute_command", command="python -m pytest -q || true"),
        Completion(text="All tests pass!", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("F: `|| true` does not fool self_verify",
                not reached["self_verify"], str(reached))
    ok &= check("F: file_edit ground truth fails", not reached["file_edit"])

    # --- G: capture_messages --------------------------------------------
    debug_script = lambda: [  # noqa: E731 - same well-behaved run as A
        call("read_file", path="main.py"),
        call("execute_command", command="python -m pytest -q"),
        call("write_file", path="main.py", content=FIXED_MAIN),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="Fixed the operator bug.", tool_calls=[]),
    ]
    r_cap = await run(TaskDebugPython(), debug_script(), capture_messages=True)
    r_def = await run(TaskDebugPython(), debug_script())

    hist = r_cap.messages_history
    ok &= check("G: capture run still passes", r_cap.passed,
                r_cap.failure_reason or "")
    ok &= check("G: messages_history captured", hist is not None)
    assistants = [m for m in hist or [] if m["role"] == "assistant"]
    ok &= check("G: raw_choices aligned with assistant turns (5)",
                len(assistants) == 5 and len(r_cap.raw_choices or []) == 5,
                f"assistants={len(assistants)} "
                f"raw={len(r_cap.raw_choices or [])}")
    tool_msgs = [m for m in hist or [] if m["role"] == "tool"]
    ok &= check("G: tool messages match turn_logs",
                len(tool_msgs) == len(r_cap.turn_logs),
                f"tool_msgs={len(tool_msgs)} logs={len(r_cap.turn_logs)}")
    first = (r_cap.raw_choices or [{}])[0]
    last = (r_cap.raw_choices or [{}])[-1]
    ok &= check("G: raw choice content survives round-trip",
                (hist or [{}])[0]["role"] == "user"
                and first.get("message", {}).get("tool_calls", [{}])[0]
                    .get("function", {}).get("name") == "read_file"
                and last.get("finish_reason") == "stop"
                and (hist or [{}])[-1]["role"] == "assistant",
                json.dumps(first)[:200])
    ok &= check("G: default run has no capture fields",
                r_def.messages_history is None and r_def.raw_choices is None)
    # Two separate runs never share a hash (skill elapsed_sec is timing),
    # so prove exclusion on the same result: nulling the captured fields
    # must not change what compute_hash sees.
    stripped = dataclasses.replace(r_cap, messages_history=None,
                                   raw_choices=None)
    ok &= check("G: capture fields excluded from result_hash",
                stripped.compute_hash() == r_cap.result_hash,
                f"{stripped.compute_hash()} != {r_cap.result_hash}")
    d_def, d_cap = r_def.to_dict(), r_cap.to_dict()
    ok &= check("G: JSON omits fields unless captured",
                "messages_history" not in d_def and "raw_choices" not in d_def
                and "messages_history" in d_cap and "raw_choices" in d_cap)

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
