#!/usr/bin/env python3
"""agent-bench CLI.

  python cli.py --model MODEL --task debug_python_v1 --k 5 \\
      --base-url http://localhost:8080/v1 --api-key none

  python cli.py --model MODEL --task skill_run_v1 \\
      --skill-dir ./examples/skills/report-skill --k 5 \\
      --base-url http://localhost:8080/v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agent_bench.adapters import HermesAdapter, OpenAIAdapter
from agent_bench.runner import print_summary, run_pass_k
from agent_bench.sandbox import sweep_orphans
from agent_bench.schema import make_environment
from agent_bench.tasks import BUILTIN_TASKS, TaskSkillDriven

SKILLS_ROOT = Path(__file__).resolve().parent / "examples" / "skills"


def bundled_skills() -> dict[str, Path]:
    """task_id -> skill dir for every bundled skill with a manifest."""
    found: dict[str, Path] = {}
    if not SKILLS_ROOT.is_dir():
        return found
    for manifest in sorted(SKILLS_ROOT.glob("*/bench_manifest.json")):
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        tid = m.get("id", f"skill_run_v1_{manifest.parent.name}")
        found[tid] = manifest.parent
    return found


def resolve_tasks(spec: str, skill_dir: str | None):
    """'all' or a comma list of task ids -> list of instantiated tasks.
    Cheap/pure-python tasks run first so a broken setup fails fast; heavy
    artifact generators (imitate) run last."""
    skills = bundled_skills()
    if spec.strip() == "all":
        builtin_order = [t for t in BUILTIN_TASKS if t != "skill_run_v1"]
        heavy_last = sorted(skills, key=lambda t: "imitate" in t)
        names = builtin_order + heavy_last
    else:
        names = [s.strip() for s in spec.split(",") if s.strip()]
    tasks = []
    for name in names:
        if name in BUILTIN_TASKS and name != "skill_run_v1":
            tasks.append(BUILTIN_TASKS[name]())
        elif name == "skill_run_v1":
            if not skill_dir:
                raise SystemExit("skill_run_v1 requires --skill-dir")
            tasks.append(TaskSkillDriven(skill_dir))
        elif name in skills:
            tasks.append(TaskSkillDriven(str(skills[name])))
        else:
            known = ", ".join([*BUILTIN_TASKS, *skills])
            raise SystemExit(f"unknown task {name!r}; available: {known}")
    return tasks


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="agent-bench",
        description="Deterministic agent-skill benchmark for local LLMs.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--task",
                    help=f"one of: {', '.join(BUILTIN_TASKS)}")
    ap.add_argument("--tasks",
                    help="comma-separated task ids, or 'all' to run every "
                         "builtin task plus bundled skills in one go")
    ap.add_argument("--k", type=int, default=3, help="trials (pass^k)")
    ap.add_argument("--base-url", default="http://localhost:8080/v1")
    ap.add_argument("--api-key", default="none")
    ap.add_argument("--adapter", choices=["openai", "hermes"], default="openai")
    ap.add_argument("--skill-dir", help="skill directory for skill_run_v1")
    ap.add_argument("--image", default="agent-bench:latest")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--command-timeout", type=float, default=60.0)
    ap.add_argument("--request-timeout", type=float, default=600.0,
                    help="LLM HTTP timeout (sec); long artifact generations "
                         "on small hardware can take minutes per turn")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument("--server", default="unknown",
                    help="inference server name/version, recorded in environment")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--parallel", action="store_true",
                    help="run trials concurrently. Timing metrics from "
                         "parallel runs are NOT comparable; default is sequential.")
    ap.add_argument("--early-stop", action="store_true",
                    help="stop a task after the first failed trial: one "
                         "failure already decides pass^k, so the remaining "
                         "trials only cost time (sequential mode only)")
    ap.add_argument("--fresh-container", action="store_true",
                    help="start a new container per trial instead of "
                         "resetting one shared container between trials")
    ap.add_argument("--verbose", action="store_true",
                    help="stream per-turn progress lines while running")
    args = ap.parse_args()

    if not args.model.strip():
        ap.error("--model must not be empty (an empty model name makes "
                 "OpenAI-compatible servers silently answer with whatever "
                 "model happens to be loaded, corrupting attribution)")
    if bool(args.task) == bool(args.tasks):
        ap.error("exactly one of --task / --tasks is required")

    tasks = resolve_tasks(args.tasks or args.task, args.skill_dir)
    if args.max_turns:
        for task in tasks:
            task.max_turns = args.max_turns

    sampling = {"temperature": args.temperature, "seed": args.seed,
                "top_p": args.top_p}
    environment = make_environment(
        image=args.image, base_url=args.base_url, model=args.model,
        adapter=args.adapter, sampling=sampling, server=args.server)

    adapter_cls = HermesAdapter if args.adapter == "hermes" else OpenAIAdapter

    def adapter_factory():
        return adapter_cls(args.base_url, args.model, args.api_key,
                           sampling=sampling,
                           request_timeout=args.request_timeout)

    def on_event(ev):
        t = ev.get("type")
        if t == "trial_start":
            line = f"[trial {ev['trial']}] start"
        elif t == "tool":
            mark = "INVALID" if ev["invalid"] else f"exit {ev['exit']}"
            line = (f"[trial {ev['trial']} | turn {ev['turn']}] "
                    f"{ev['tool']} {ev['arg']}  -> {mark}")
        elif t == "note":
            line = f"[trial {ev['trial']} | turn {ev['turn']}] (says) {ev['text']}"
        elif t == "trial_end":
            line = (f"[trial {ev['trial']}] {'PASS' if ev['passed'] else 'FAIL'} "
                    f"score {ev['score']} turns {ev['turns']}")
        else:
            return
        print(line, flush=True)

    swept = asyncio.run(sweep_orphans())
    if swept:
        print(f"[sweep] removed {swept} orphaned sandbox container(s) "
              f"older than 24h", flush=True)

    summaries = []
    for task in tasks:
        summary = asyncio.run(run_pass_k(
            task, adapter_factory, k=args.k, image=args.image,
            model=args.model, environment=environment,
            parallel=args.parallel,
            command_timeout=args.command_timeout,
            results_dir=args.results_dir,
            on_event=on_event if args.verbose else None,
            early_stop=args.early_stop,
            reuse_container=not args.fresh_container))
        print_summary(summary)
        summaries.append(summary)

    if len(summaries) > 1:
        print(f"\n=== batch: {args.model} | {len(summaries)} tasks ===")
        for s in summaries:
            mark = "PASS" if s["pass_all_k"] else "FAIL"
            note = "  (early-stopped)" if s["early_stopped"] else ""
            print(f"  {mark}  {s['task_id']:<32} score {s['avg_score']:.2f}  "
                  f"turns {s['avg_turns']}  {s['avg_elapsed_sec']:.0f}s{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
