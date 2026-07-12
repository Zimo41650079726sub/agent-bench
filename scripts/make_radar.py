#!/usr/bin/env python3
"""Generate a 12-axis radar chart (SVG in HTML) comparing up to 4 models.

  python3 scripts/make_radar.py --models qwen3.5-4b,gemma-4-e4b-it-qat
  python3 scripts/make_radar.py --models a,b --out results/radar.html

Axes are the 6 original tasks + the 6 failure-luring tasks; the value is
avg_score (0-1) from the newest result per (task, model) — same rule as
make_matrix.py. Tasks without a result render at 0 and show "—" in the
table below the chart (the table doubles as the accessibility channel:
series identity is never carried by color alone).

PNG export (headless Edge; the script prints the exact command):
  msedge --headless --hide-scrollbars --screenshot=radar.png \
         --window-size=1040,H radar.html
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_matrix import load  # noqa: E402  (newest result per task/model)

# Canonical axis order: the original six, then the failure-luring six.
AXES = [
    ("debug_python_v1", "debug"),
    ("tdd_order_v1", "tdd"),
    ("context_manage_v1", "context"),
    ("skill_run_v1_landing_page", "landing"),
    ("skill_run_v1_report", "report"),
    ("skill_run_v1_imitate_dashboard", "imitate"),
    ("doc_trap_v1", "doc_trap*"),
    ("wrong_fix_trap_v1", "wrong_fix*"),
    ("tdd_strict_v1", "tdd_strict*"),
    ("big_file_edit_v1", "big_edit*"),
    ("tool_mirage_v1", "tool_mirage*"),
    ("long_procedure_v1", "long_proc*"),
]

# Dark categorical slots 1-4 from the reference palette, validated with
# scripts/validate_palette.js on surface #1a1a19 (band/chroma/contrast PASS;
# CVD floor band -> the value table below the chart is the mandatory
# secondary encoding).
SERIES = ["#3987e5", "#199e70", "#c98500", "#008300"]

SURFACE = "#1a1a19"
PAGE = "#0d0d0d"
INK = "#ffffff"
INK_2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"

CX, CY, R = 520, 400, 285
LEGEND_X = 24


def _pt(axis_i: int, value: float) -> tuple[float, float]:
    ang = -math.pi / 2 + axis_i * 2 * math.pi / len(AXES)
    return (CX + R * value * math.cos(ang), CY + R * value * math.sin(ang))


def _poly(values: list[float]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in
                    (_pt(i, v) for i, v in enumerate(values)))


def _axis_label_attrs(i: int) -> str:
    x, y = _pt(i, 1.0)
    ang = -math.pi / 2 + i * 2 * math.pi / len(AXES)
    dx, dy = math.cos(ang), math.sin(ang)
    anchor = "middle" if abs(dx) < 0.35 else ("start" if dx > 0 else "end")
    return (f'x="{x + dx * 16:.1f}" y="{y + dy * 16 + 4:.1f}" '
            f'text-anchor="{anchor}"')


def short(m: str) -> str:
    return m.split("/")[-1].removesuffix(".gguf")


def build(models: list[str], scores: dict[str, list[float | None]]) -> str:
    svg = []
    # grid rings + spokes (recessive)
    for ring in (0.25, 0.5, 0.75, 1.0):
        svg.append(f'<polygon points="{_poly([ring] * len(AXES))}" '
                   f'fill="none" stroke="{GRID}" stroke-width="1"/>')
    for i in range(len(AXES)):
        x, y = _pt(i, 1.0)
        svg.append(f'<line x1="{CX}" y1="{CY}" x2="{x:.1f}" y2="{y:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
    for ring in (0.5, 1.0):
        _, y = _pt(0, ring)
        svg.append(f'<text x="{CX + 6}" y="{y + 12}" fill="{MUTED}" '
                   f'font-size="11">{ring:.1f}</text>')
    for i, (_, label) in enumerate(AXES):
        svg.append(f'<text {_axis_label_attrs(i)} fill="{INK_2}" '
                   f'font-size="13">{label}</text>')
    # Series polygons: fill wash + 2px stroke + ringed vertex markers.
    # Quantized scores tie constantly (1.00 on easy tasks), so coincident
    # edges hide earlier series entirely; the descending marker radii make
    # shared vertices read as concentric rings — every series stays visible
    # even where the outlines coincide exactly.
    radius = [8.0, 6.3, 4.6, 3.0]
    for si, model in enumerate(models):
        color = SERIES[si]
        vals = [v if v is not None else 0.0 for v in scores[model]]
        svg.append(f'<polygon points="{_poly(vals)}" fill="{color}" '
                   f'fill-opacity="0.10" stroke="{color}" stroke-width="2"/>')
    for si, model in enumerate(models):
        color = SERIES[si]
        for i, v in enumerate(scores[model]):
            x, y = _pt(i, v if v is not None else 0.0)
            if v is None:  # unmeasured: small open marker at the center
                svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" '
                           f'fill="none" stroke="{color}" stroke-width="1.5"/>')
            else:
                svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" '
                           f'r="{radius[si]}" fill="{color}" '
                           f'stroke="{SURFACE}" stroke-width="2"/>')

    legend = "".join(
        f'<span class="chip"><span class="swatch" '
        f'style="background:{SERIES[i]}"></span>{short(m)}</span>'
        for i, m in enumerate(models))

    head = ("<tr><th>task</th>"
            + "".join(f"<th>{short(m)}</th>" for m in models) + "</tr>")
    rows = []
    for ai, (tid, label) in enumerate(AXES):
        cells = []
        for m in models:
            v = scores[m][ai]
            cells.append(f"<td>{'—' if v is None else f'{v:.2f}'}</td>")
        rows.append(f"<tr><td class='t'>{label}</td>{''.join(cells)}</tr>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>agent-bench radar</title>
<style>
body {{ background:{PAGE}; margin:0; padding:24px;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
.viz-root {{ background:{SURFACE}; border:1px solid rgba(255,255,255,.10);
  border-radius:10px; padding:20px 24px; width:992px; box-sizing:border-box; }}
h1 {{ color:{INK}; font-size:19px; margin:0 0 2px; }}
.sub {{ color:{MUTED}; font-size:12px; margin:0 0 8px; }}
.legend {{ margin:6px 0 0 {LEGEND_X - 24}px; }}
.chip {{ color:{INK_2}; font-size:13px; margin-right:18px; }}
.swatch {{ display:inline-block; width:12px; height:12px; border-radius:3px;
  vertical-align:-1px; margin-right:6px; }}
table {{ border-collapse:collapse; margin:4px auto 6px; }}
th, td {{ font-size:12px; padding:3px 14px; text-align:right;
  font-variant-numeric: tabular-nums; color:{INK_2};
  border-bottom:1px solid {GRID}; }}
th {{ color:{MUTED}; font-weight:600; }}
td.t {{ text-align:left; color:{INK_2}; }}
.foot {{ color:{MUTED}; font-size:11px; margin-top:8px; }}
</style></head><body>
<div class="viz-root">
<h1>agent-bench — task coverage radar</h1>
<p class="sub">avg_score per task (newest result), 0 = center · 1 = outer ring</p>
<div class="legend">{legend}</div>
<svg viewBox="0 0 1040 780" width="944" role="img"
     aria-label="Radar chart of per-task average scores">
{chr(10).join(svg)}
</svg>
<table>{head}{''.join(rows)}</table>
<p class="foot">* failure-luring task set (v2) · — = not measured
(rendered at the center) · scores are deterministic flag ratios, not LLM
judgments.</p>
</div></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True,
                    help="comma-separated model ids as recorded in results "
                         "(max 4)")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out", default="radar.html")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not 1 <= len(models) <= 4:
        raise SystemExit("--models takes 1 to 4 model ids "
                         "(4 series is the categorical ceiling here)")

    newest = load(Path(args.results_dir))
    known = {m for (_, m) in newest}
    scores: dict[str, list[float | None]] = {}
    for m in models:
        if m not in known:
            print(f"warning: no results at all for {m!r}", file=sys.stderr)
        scores[m] = [
            (newest[(tid, m)]["avg_score"]
             if (tid, m) in newest else None)
            for tid, _ in AXES
        ]

    out = Path(args.out)
    out.write_text(build(models, scores), encoding="utf-8")
    print(f"wrote {out}")
    print("PNG: msedge --headless --hide-scrollbars "
          f"--screenshot={out.with_suffix('.png')} "
          f"--window-size=1040,1240 {out}")


if __name__ == "__main__":
    main()
