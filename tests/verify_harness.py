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

Run from repo root:  python3 tests/verify_harness.py
"""

import asyncio
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


async def run(task, script):
    return await run_trial(task, MockAdapter(script), image=IMAGE,
                           trial_index=0, model="mock", environment=ENV)


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

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
