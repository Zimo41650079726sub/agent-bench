#!/usr/bin/env python3
"""Generate a task x model compatibility matrix (Markdown) from results/.

  python3 scripts/make_matrix.py                        # all models
  python3 scripts/make_matrix.py --models qwen3.5-4b,gemma-4-e4b-it-qat

Uses the newest result file per (task, model), same rule as the dashboard.
Cell format: pass^k badge, avg_score, avg_turns. Intended to be pasted into
README as living proof that the bench runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(results_dir: Path) -> dict:
    newest: dict[tuple[str, str], dict] = {}
    for path in sorted(results_dir.glob("result_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = (data.get("task_id", "?"), data.get("model", "?"))
        data["_mtime"] = path.stat().st_mtime
        if key not in newest or data["_mtime"] > newest[key]["_mtime"]:
            newest[key] = data
    return newest


def fmt_time(sec: float) -> str:
    return f"{sec:.0f}s" if sec < 120 else f"{sec / 60:.1f}m"


def cell(r: dict | None) -> str:
    if r is None:
        return "—"
    t = fmt_time(r.get("avg_elapsed_sec", 0))
    if r.get("pass_all_k"):
        return f"✅ pass^{r['k']} · {r['avg_turns']}t · {t}"
    return f"✗ {r['avg_score']:.2f} (k={r['k']}) · {t}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--models", default="",
                    help="comma-separated allowlist; empty = all")
    args = ap.parse_args()

    newest = load(Path(args.results_dir))
    allow = [m.strip() for m in args.models.split(",") if m.strip()]
    models = sorted({m for (_, m) in newest
                     if not allow or m in allow})
    tasks = sorted({t for (t, m) in newest
                    if not allow or m in allow})
    if not models:
        print("no matching results")
        return

    def short(m: str) -> str:
        return m.split("/")[-1].removesuffix(".gguf")

    print("| task | " + " | ".join(short(m) for m in models) + " |")
    print("|" + "---|" * (len(models) + 1))
    for t in tasks:
        row = [cell(newest.get((t, m))) for m in models]
        print(f"| {t} | " + " | ".join(row) + " |")
    print()
    print("`✅ pass^k · Nt · T` = all k trials passed, avg N turns, "
          "avg wall time per trial. `✗ S` = avg score S with at least one "
          "failed trial. Newest result per (task, model).")

    # Tiers are derived mechanically from the flags, not by judgment:
    #   Tier 1: every benched task fully passed (process flags included)
    #   Tier 3: agentic loop itself fails (avg_turns 0, or every task < 0.5)
    #   Tier 2: everything else — outcomes achieved, process flags dropped
    print()
    print("## Model tiers (derived from flags)")
    print()
    print("| tier | definition | models |")
    print("|---|---|---|")
    # Within a tier, order by total clear time across benched tasks —
    # "fast" is an axis of its own once "correct" is settled.
    tiers: dict[int, list[tuple[float, str]]] = {1: [], 2: [], 3: []}
    for m in models:
        rs = [newest[(t, mm)] for (t, mm) in newest
              if mm == m and (not allow or mm in allow)]
        total = sum(r.get("avg_elapsed_sec", 0) * r.get("k", 1) for r in rs)
        label = f"{short(m)} ({fmt_time(total)})"
        if all(r.get("pass_all_k") for r in rs):
            tiers[1].append((total, label))
        elif all(r.get("avg_turns", 0) == 0 or r.get("avg_score", 0) < 0.5
                 for r in rs):
            tiers[3].append((total, label))
        else:
            tiers[2].append((total, label))
    for tier in tiers.values():
        tier.sort()
    defs = {
        1: "**Full discipline** — every task passes, process flags included",
        2: "**Outcome-capable** — completes tasks but drops process flags "
           "(e.g. never observes the failure before fixing)",
        3: "**Cannot drive tools** — the agentic loop itself does not run",
    }
    for tier in (1, 2, 3):
        names = ", ".join(label for _, label in tiers[tier]) or "—"
        print(f"| {tier} | {defs[tier]} | {names} |")
    print()
    print("Within each tier, models are ordered by total clear time "
          "(sum over benched tasks, all trials).")


if __name__ == "__main__":
    main()
