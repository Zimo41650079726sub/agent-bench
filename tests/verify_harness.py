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
  G. container reuse: reset() wipes workspace/procs/cwd; pass^k over one
     shared container behaves identically to fresh containers
  H. --early-stop: first failed trial ends the run (trials_run < k)

Failure-luring tasks (each verified on its success path AND on the exact
measured failure it was designed to provoke):
  I. big_file_edit_v1    — sed edits pass; truncating rewrite / dropped
                           path argument are caught
  J. doc_trap_v1         — explore-and-recover passes; giving up after the
                           doomed first read scores zero
  K. wrong_fix_trap_v1   — evidence-first fix passes; "fixing" the decoy
                           without running pytest is caught
  L. tdd_strict_v1       — red.log discipline passes; skipping red / stubbing
                           the function inside the test file are caught
  M. tool_mirage_v1      — running the scripts passes; calling a step name
                           as a tool trips no_tool_hallucination
  N. long_procedure_v1   — all 7 steps pass; abandoning after step 3 yields
                           a mid-range score (gradient, not binary)

Run from repo root:  python3 tests/verify_harness.py
"""

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_bench.adapters import Completion, MockAdapter, ToolCall
from agent_bench.runner import run_pass_k, run_trial
from agent_bench.sandbox import StatefulSandbox
from agent_bench.tasks import (TaskBigFileEdit, TaskDebugPython, TaskDocTrap,
                               TaskLongProcedure, TaskSkillDriven, TaskTDD,
                               TaskTDDStrict, TaskWrongFixTrap)
from agent_bench.tasks import big_file_edit as bfe
from agent_bench.tasks import long_procedure as lp
from agent_bench.tasks import wrong_fix as wf

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

    # --- G: container reuse / reset hygiene ------------------------------
    sb = StatefulSandbox(image=IMAGE)
    await sb.start()
    await sb.write_file("/workspace/leftover.txt", "junk")
    await sb.execute("mkdir -p sub && cd sub")
    await sb.execute("sleep 300 & echo spawned")   # stray background process
    await sb.reset()
    snap = await sb.snapshot_workspace()
    ok &= check("G: reset wipes workspace", snap == {}, str(snap))
    ok &= check("G: reset restores cwd", sb.cwd == "/workspace", sb.cwd)
    # /proc scan (image has no procps). Killed strays remain as zombies
    # (PID 1 never reaps), so count only non-zombie sleeps: exactly one
    # must survive — the PID-1 keepalive itself.
    _, out, _ = await sb.execute(
        'alive=0; for p in /proc/[0-9]*; do '
        'read -r c < "$p/comm" 2>/dev/null || continue; '
        'read -r _ _ st _ < "$p/stat" 2>/dev/null || continue; '
        '[ "$c" = sleep ] && [ "$st" != Z ] && alive=$((alive+1)); '
        'done; echo "ALIVE_SLEEP=$alive"')
    ok &= check("G: reset kills stray processes (zombies excluded)",
                "ALIVE_SLEEP=1" in out, out)
    await sb.cleanup()

    good_script = lambda: [  # noqa: E731 - same well-behaved run as A
        call("read_file", path="main.py"),
        call("execute_command", command="python -m pytest -q"),
        call("write_file", path="main.py", content=FIXED_MAIN),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="Fixed.", tool_calls=[]),
    ]
    tamper_script = lambda: [  # noqa: E731 - drops an unallowed file
        call("write_file", path="junk.txt", content="scratch"),
        call("write_file", path="main.py", content=FIXED_MAIN),
        Completion(text="done", tool_calls=[]),
    ]
    tmp = tempfile.mkdtemp(prefix="agbench_verify_")
    try:
        scripts = [tamper_script(), good_script()]
        summary = await run_pass_k(
            TaskDebugPython(), lambda: MockAdapter(scripts.pop(0)),
            k=2, image=IMAGE, model="mock", environment=ENV,
            results_dir=tmp)
        ok &= check("G: shared container is flagged in summary",
                    summary["container_reuse"] is True)
        ok &= check("G: trial 1 tamper detected under reuse",
                    summary["trials"][0]["tamper_detected"]
                    and not summary["trials"][0]["passed"])
        ok &= check("G: trial 2 passes clean after reset",
                    summary["trials"][1]["passed"],
                    summary["trials"][1]["failure_reason"] or "")

        # --- H: early stop ----------------------------------------------
        summary = await run_pass_k(
            TaskDebugPython(),
            lambda: MockAdapter([Completion(text="cannot fix", tool_calls=[])]),
            k=3, image=IMAGE, model="mock", environment=ENV,
            results_dir=tmp, early_stop=True)
        ok &= check("H: early stop after first failure",
                    summary["trials_run"] == 1 and summary["early_stopped"]
                    and not summary["pass_all_k"],
                    f"trials_run={summary['trials_run']}")
        ok &= check("H: full k recorded as requested",
                    summary["k"] == 3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- I: big_file_edit_v1 ---------------------------------------------
    r = await run(TaskBigFileEdit(), [
        call("execute_command", command=(
            "sed -i 's|<title>Acme Dashboard</title>"
            "|<title>Acme Control Center</title>|' site/index.html")),
        call("execute_command", command=(
            "sed -i 's|--accent: #3b82f6;|--accent: #e11d48;|' site/index.html")),
        call("execute_command", command=(
            "sed -i 's|href=\"/contatc\"|href=\"/contact\"|' site/index.html")),
        call("execute_command", command=(
            "sed -i 's|<li><a href=\"/news\">News</a></li>"
            "|<li><a href=\"/news\">News</a></li>"
            "<li><a href=\"/blog\">Blog</a></li>|' site/index.html")),
        Completion(text="Made the four edits in place.", tool_calls=[]),
    ])
    ok &= check("I: surgical sed edits pass", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    truncated = bfe._build_page().replace(bfe.OLD_TITLE, bfe.NEW_TITLE)[:8000]
    r = await run(TaskBigFileEdit(), [
        call("write_file", content=truncated),        # dropped path argument
        call("write_file", path="site/index.html", content=truncated),
        Completion(text="Rewrote the page with the new title.", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("I: truncating rewrite loses anchors",
                not reached["anchors_preserved"] and reached["change_title"],
                str(reached))
    ok &= check("I: dropped path argument -> args_intact not reached",
                not reached["args_intact"], str(reached))
    ok &= check("I: lure run does not pass", not r.passed)

    # --- J: doc_trap_v1 ----------------------------------------------------
    r = await run(TaskDocTrap(), [
        call("read_file", path="README.md"),
        call("read_file", path="docs/SETUP.md"),      # the doomed lead
        call("execute_command", command="ls -R"),
        call("read_file", path="notes/setup-guide.txt"),
        call("write_file", path="output/deploy.env",
             content="DEPLOY_KEY=zx91-tango\n"),
        Completion(text="Deploy config written.", tool_calls=[]),
    ])
    ok &= check("J: explore-and-recover passes", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    r = await run(TaskDocTrap(), [
        call("read_file", path="docs/SETUP.md"),
        Completion(text="The setup documentation is missing; cannot proceed.",
                   tool_calls=[]),
    ])
    ok &= check("J: giving up after the doomed read scores zero",
                r.score == 0 and not r.passed, str(r.score))

    # --- K: wrong_fix_trap_v1 ----------------------------------------------
    fixed_stats = wf.STATS.replace("vals[mid + 1]", "vals[mid - 1]")
    r = await run(TaskWrongFixTrap(), [
        call("execute_command", command="python -m pytest -q"),
        call("read_file", path="stats.py"),
        call("write_file", path="stats.py", content=fixed_stats),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="median() even-length branch fixed.", tool_calls=[]),
    ])
    ok &= check("K: evidence-first fix passes", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    decoy_fix = wf.REPORT.replace("range(len(readings) - 1)",
                                  "range(len(readings))")
    r = await run(TaskWrongFixTrap(), [
        call("read_file", path="report.py"),
        call("write_file", path="report.py", content=decoy_fix),
        Completion(text="Fixed the off-by-one in daily_deltas.",
                   tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("K: decoy edit without pytest is caught",
                not reached["decoy_untouched"]
                and not reached["error_interpret"]
                and not reached["real_fixed"] and not r.passed,
                str(reached))

    # --- L: tdd_strict_v1 ----------------------------------------------------
    test_roman = (
        "from main import roman\n\n"
        "def test_roman():\n"
        "    assert roman(4) == 'IV'\n"
        "    assert roman(9) == 'IX'\n"
        "    assert roman(14) == 'XIV'\n"
        "    assert roman(90) == 'XC'\n"
        "    assert roman(1990) == 'MCMXC'\n"
        "    assert roman(3999) == 'MMMCMXCIX'\n")
    roman_impl = (
        "def roman(n):\n"
        "    vals = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),\n"
        "            (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),\n"
        "            (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]\n"
        "    out = []\n"
        "    for v, s in vals:\n"
        "        while n >= v:\n"
        "            out.append(s)\n"
        "            n -= v\n"
        "    return ''.join(out)\n")
    r = await run(TaskTDDStrict(), [
        call("write_file", path="test_main.py", content=test_roman),
        call("execute_command",
             command="python -m pytest -q 2>&1 | tee red.log"),
        call("write_file", path="main.py", content=roman_impl),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="Red observed, then implemented.", tool_calls=[]),
    ])
    ok &= check("L: red.log discipline passes", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    stub_test = (
        "def roman(n):\n"
        "    return 'IV'\n\n"
        "def test_roman():\n"
        "    assert roman(4) == 'IV'\n")
    r = await run(TaskTDDStrict(), [
        call("write_file", path="test_main.py", content=stub_test),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="Tests pass.", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("L: stub inside the test file is caught by AST",
                not reached["no_test_stub"] and not reached["tests_pass"]
                and not r.passed, str(reached))

    r = await run(TaskTDDStrict(), [
        call("write_file", path="test_main.py", content=test_roman),
        call("write_file", path="main.py", content=roman_impl),
        call("execute_command", command="python -m pytest -q"),
        Completion(text="done", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("L: skipping the red phase is caught",
                not reached["red_logged"] and reached["test_first"]
                and not r.passed, str(reached))

    # --- M: tool_mirage_v1 ---------------------------------------------------
    pipeline_dir = Path(__file__).resolve().parent.parent / \
        "examples/skills/data-pipeline"
    r = await run(TaskSkillDriven(str(pipeline_dir)), [
        call("read_file", path="skill/data/manifest.json"),
        call("execute_command",
             command="python3 skill/scripts/csv_summarize.py "
                     "--input skill/data/sales.csv --out output/summary.txt"),
        call("execute_command",
             command="sh skill/scripts/publish_report.sh "
                     "output/summary.txt output/report.md"),
        Completion(text="Pipeline complete.", tool_calls=[]),
    ])
    ok &= check("M: running the scripts via shell passes", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    r = await run(TaskSkillDriven(str(pipeline_dir)), [
        call("csv_summarize", input="data/sales.csv"),  # step name as a tool
        Completion(text="Summarized.", tool_calls=[]),
    ])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("M: step name called as a tool trips no_tool_hallucination",
                not reached["no_tool_hallucination"]
                and r.invalid_tool_call_count == 1 and not r.passed,
                str(r.invalid_tool_call_count))

    # --- N: long_procedure_v1 -----------------------------------------------
    def lp_csv(rows):
        return "id,name,status,amount\n" + "".join(
            f"{r['id']},{r['name']},{r['status']},{r['amount']}\n"
            for r in rows)

    table = ("<table><tr><th>id</th><th>name</th><th>amount</th></tr>"
             + "".join(f"<tr><td>{r['id']}</td><td>{r['name']}</td>"
                       f"<td>{r['amount']}</td></tr>" for r in lp.TOP10)
             + "</table>")
    step_writes = [
        call("write_file", path="output/step1_filtered.csv",
             content=lp_csv(lp.ACTIVE)),
        call("write_file", path="output/step2_sorted.csv",
             content=lp_csv(lp.SORTED_DESC)),
        call("write_file", path="output/step3_top10.csv",
             content=lp_csv(lp.TOP10)),
        call("write_file", path="output/step4_totals.txt",
             content=f"SUM={lp.SUM_TOP10}\n"),
        call("write_file", path="output/step5_page.html",
             content=lp.SKELETON.replace(lp.TABLE_SLOT, table)),
        call("write_file", path="output/step6_check.txt", content="LINES=11\n"),
        call("write_file", path="output/step7_manifest.json",
             content=json.dumps(lp.STEP_FILES[:6])),
    ]
    r = await run(TaskLongProcedure(),
                  step_writes + [Completion(text="All 7 steps done.",
                                            tool_calls=[])])
    ok &= check("N: full 7-step run passes", r.passed,
                r.failure_reason or str({s.skill_id: s.reached
                                         for s in r.skill_results}))

    r = await run(TaskLongProcedure(),
                  step_writes[:3] + [Completion(text="", tool_calls=[])])
    reached = {s.skill_id: s.reached for s in r.skill_results}
    ok &= check("N: abandoning after step 3 yields the gradient score 0.5",
                abs(r.score - 0.5) < 1e-9 and not r.passed,
                f"score={r.score} {reached}")

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
