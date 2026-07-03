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
import sys

from agent_bench.adapters import HermesAdapter, OpenAIAdapter
from agent_bench.runner import print_summary, run_pass_k
from agent_bench.sandbox import sweep_orphans
from agent_bench.schema import make_environment
from agent_bench.tasks import BUILTIN_TASKS, TaskSkillDriven


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="agent-bench",
        description="Deterministic agent-skill benchmark for local LLMs.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--task", required=True,
                    help=f"one of: {', '.join(BUILTIN_TASKS)}")
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
    ap.add_argument("--verbose", action="store_true",
                    help="stream per-turn progress lines while running")
    args = ap.parse_args()

    if not args.model.strip():
        ap.error("--model must not be empty (an empty model name makes "
                 "OpenAI-compatible servers silently answer with whatever "
                 "model happens to be loaded, corrupting attribution)")
    if args.task not in BUILTIN_TASKS:
        ap.error(f"unknown task {args.task!r}; available: {', '.join(BUILTIN_TASKS)}")

    if args.task == "skill_run_v1":
        if not args.skill_dir:
            ap.error("skill_run_v1 requires --skill-dir")
        task = TaskSkillDriven(args.skill_dir)
    else:
        task = BUILTIN_TASKS[args.task]()
    if args.max_turns:
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

    summary = asyncio.run(run_pass_k(
        task, adapter_factory, k=args.k, image=args.image, model=args.model,
        environment=environment, parallel=args.parallel,
        command_timeout=args.command_timeout, results_dir=args.results_dir,
        on_event=on_event if args.verbose else None))
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
