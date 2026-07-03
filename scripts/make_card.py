#!/usr/bin/env python3
"""Generate a shareable certificate card (SVG, 1200x675) for one model.

  python3 scripts/make_card.py --model gemma-4-26b-a4b-it
  python3 scripts/make_card.py --model qwen3.5-4b --out card.svg

Reads the newest result per (task, model) from results/, same rule as the
dashboard and make_matrix.py. Stdlib only. Convert to PNG with any headless
browser, e.g.:
  msedge --headless --screenshot=card.png --window-size=1200,675 card.svg
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1200, 675
REPO_URL = "github.com/Zimo41650079726sub/agent-bench"

# Dark-surface palette (validated set; see dataviz reference instance).
SURFACE = "#1a1a19"
INK = "#ffffff"
INK2 = "#c3c2b7"
ACCENT = "#3987e5"
GOOD = "#0ca30c"
FAIL = "#ec835a"

TASK_LABELS = {
    "debug_python_v1": "debug",
    "tdd_order_v1": "tdd",
    "context_manage_v1": "context",
    "skill_run_v1_report": "report",
    "skill_run_v1_landing_page": "landing",
    "skill_run_v1_imitate_dashboard": "dashboard",
}


def newest_for_model(results_dir: Path, model: str) -> dict[str, dict]:
    newest: dict[str, dict] = {}
    for path in results_dir.glob("result_*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("model") != model:
            continue
        t = d.get("task_id", "?")
        d["_mtime"] = path.stat().st_mtime
        if t not in newest or d["_mtime"] > newest[t]["_mtime"]:
            newest[t] = d
    return newest


def fmt_time(sec: float) -> str:
    return f"{sec:.0f}s" if sec < 120 else f"{sec / 60:.1f}m"


def build_svg(model: str, rows: dict[str, dict]) -> str:
    tasks = sorted(rows.values(), key=lambda r: TASK_LABELS.get(
        r["task_id"], r["task_id"]))
    n_pass = sum(1 for r in tasks if r.get("pass_all_k"))
    all_clear = n_pass == len(tasks)
    total_sec = sum(r.get("avg_elapsed_sec", 0) * r.get("k", 1) for r in tasks)
    n_trials = sum(r.get("k", 1) for r in tasks)

    title = "CLEAR CERTIFICATE" if all_clear else "RESULT CERTIFICATE"
    if all_clear:
        tier_label, tier_color = "TIER 1 — FULL DISCIPLINE", GOOD
    elif any(r.get("avg_turns", 0) > 0 for r in tasks):
        tier_label, tier_color = "TIER 2 — OUTCOME-CAPABLE", ACCENT
    else:
        tier_label, tier_color = "TIER 3 — CANNOT DRIVE TOOLS", FAIL

    e = []  # svg elements
    e.append(f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>')
    e.append(f'<rect x="0" y="0" width="{W}" height="6" fill="{ACCENT}"/>')

    e.append(f'<text x="64" y="92" font-size="26" font-weight="700" '
             f'fill="{ACCENT}">agent-bench</text>')
    e.append(f'<text x="{W - 64}" y="92" text-anchor="end" font-size="20" '
             f'fill="{INK2}">{date.today().isoformat()}</text>')

    e.append(f'<text x="64" y="158" font-size="52" font-weight="800" '
             f'letter-spacing="2" fill="{INK}">{title}</text>')
    e.append(f'<text x="64" y="208" font-size="34" font-weight="600" '
             f'fill="{INK}">{escape(model)}</text>')

    tw = 30 + 11 * len(tier_label)
    e.append(f'<rect x="64" y="232" width="{tw}" height="40" rx="20" '
             f'fill="none" stroke="{tier_color}" stroke-width="2"/>')
    e.append(f'<text x="{64 + tw / 2}" y="259" text-anchor="middle" '
             f'font-size="18" font-weight="700" fill="{tier_color}">'
             f'{tier_label}</text>')

    y = 336
    for r in tasks:
        label = TASK_LABELS.get(r["task_id"], r["task_id"])
        ok = r.get("pass_all_k")
        mark, color = ("✓", GOOD) if ok else ("✗", FAIL)
        verdict = (f'pass^{r.get("k", 1)}' if ok
                   else f'{r.get("avg_score", 0):.2f} (k={r.get("k", 1)})')
        detail = f'{r.get("avg_turns", 0):g} turns · ' \
                 f'{fmt_time(r.get("avg_elapsed_sec", 0))}'
        e.append(f'<circle cx="78" cy="{y - 7}" r="14" fill="none" '
                 f'stroke="{color}" stroke-width="2"/>')
        e.append(f'<text x="78" y="{y - 1}" text-anchor="middle" '
                 f'font-size="16" font-weight="700" fill="{color}">{mark}</text>')
        e.append(f'<text x="110" y="{y}" font-size="21" font-weight="600" '
                 f'fill="{INK}">{label}</text>')
        e.append(f'<text x="250" y="{y}" font-size="20" fill="{color}">'
                 f'{verdict}</text>')
        e.append(f'<text x="420" y="{y}" font-size="20" fill="{INK2}">'
                 f'{detail}</text>')
        y += 46

    hx = 790
    e.append(f'<line x1="720" y1="320" x2="720" y2="{y - 40}" '
             f'stroke="#33322f" stroke-width="2"/>')
    e.append(f'<text x="{hx}" y="420" font-size="104" font-weight="800" '
             f'fill="{INK}">{fmt_time(total_sec)}</text>')
    e.append(f'<text x="{hx}" y="458" font-size="20" fill="{INK2}">'
             f'total clear time · {n_trials} trials</text>')
    e.append(f'<text x="{hx}" y="510" font-size="26" font-weight="700" '
             f'fill="{GOOD if all_clear else INK}">{n_pass}/{len(tasks)} '
             f'tasks passed</text>')
    e.append(f'<text x="{hx}" y="542" font-size="18" fill="{INK2}">'
             f'deterministic scoring · no LLM judge</text>')

    e.append(f'<line x1="64" y1="{H - 84}" x2="{W - 64}" y2="{H - 84}" '
             f'stroke="#33322f" stroke-width="2"/>')
    e.append(f'<text x="64" y="{H - 44}" font-size="18" fill="{INK2}">'
             f'Snapdragon X Elite · 64GB · LM Studio (llama.cpp) '
             f'· WSL2 Docker sandbox</text>')
    e.append(f'<text x="{W - 64}" y="{H - 44}" text-anchor="end" '
             f'font-size="18" fill="{ACCENT}">{REPO_URL}</text>')

    body = "\n".join(e)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
            f'height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif">\n'
            f'{body}\n</svg>\n')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out", default=None, help="output .svg path")
    args = ap.parse_args()

    rows = newest_for_model(Path(args.results_dir), args.model)
    if not rows:
        raise SystemExit(f"no results for model {args.model!r}")
    out = Path(args.out or
               f"card_{args.model.split('/')[-1].replace('.', '_')}.svg")
    out.write_text(build_svg(args.model, rows), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} tasks)")


if __name__ == "__main__":
    main()
