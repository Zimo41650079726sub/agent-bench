#!/usr/bin/env python3
"""agent-bench results dashboard. Stdlib only.

  python3 webui.py [--results-dir results] [--port 8765]

Reads results/result_*.json (the run_pass_k summaries), keeps the newest file
per (task, model), and serves a local dashboard: a model x task matrix and
per-skill reach-rate charts.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import subprocess
import sys
import threading
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RESULT_FILE_RE = re.compile(r"^result_[\w.\-]+\.json$")
REPO_ROOT = Path(__file__).resolve().parent
BUILTIN_TASK_IDS = ["debug_python_v1", "tdd_order_v1", "context_manage_v1"]

# One benchmark run at a time, driven from the GUI.
RUN_LOCK = threading.Lock()
RUN_STATE = {"running": False, "lines": deque(maxlen=4000),
             "exit_code": None, "label": ""}


def discover_skills() -> list[dict]:
    """Skill dirs (bench_manifest.json present) under examples/skills and skills."""
    found = []
    for base in (REPO_ROOT / "examples" / "skills", REPO_ROOT / "skills"):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if (d / "bench_manifest.json").is_file():
                found.append({"name": d.name,
                              "dir": str(d.relative_to(REPO_ROOT))})
    return found


def fetch_models(base_url: str) -> list[str]:
    req = urllib.request.Request(f"{base_url.rstrip('/')}/models")
    with urllib.request.urlopen(req, timeout=4) as resp:
        data = json.loads(resp.read().decode())
    return [m["id"] for m in data.get("data", [])]


def start_run(payload: dict) -> tuple[bool, str]:
    model = str(payload.get("model", "")).strip()
    task = str(payload.get("task", "")).strip()
    base_url = str(payload.get("base_url", "")).strip()
    k = int(payload.get("k", 3))
    if not model or not base_url or not 1 <= k <= 10:
        return False, "model / base_url / k (1-10) are required"
    argv = [sys.executable, str(REPO_ROOT / "cli.py"),
            "--model", model, "--k", str(k), "--base-url", base_url,
            "--server", str(payload.get("server", "unknown")),
            "--results-dir", str(RESULTS_DIR), "--verbose"]
    skills = {s["name"]: s["dir"] for s in discover_skills()}
    if task in BUILTIN_TASK_IDS:
        argv += ["--task", task]
        label = task
    elif task in skills:
        argv += ["--task", "skill_run_v1",
                 "--skill-dir", str(REPO_ROOT / skills[task])]
        label = f"skill: {task}"
    else:
        return False, f"unknown task {task!r}"
    if not RUN_LOCK.acquire(blocking=False):
        return False, "a run is already in progress"
    RUN_STATE["running"] = True
    RUN_STATE["lines"].clear()
    RUN_STATE["exit_code"] = None
    RUN_STATE["label"] = f"{label} | {model} | k={k}"
    RUN_STATE["lines"].append(f"$ {' '.join(argv[2:])}")

    def worker():
        try:
            proc = subprocess.Popen(argv, cwd=REPO_ROOT,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            for line in proc.stdout:
                RUN_STATE["lines"].append(line.rstrip())
            RUN_STATE["exit_code"] = proc.wait()
        except Exception as exc:  # noqa: BLE001
            RUN_STATE["lines"].append(f"launcher error: {exc}")
            RUN_STATE["exit_code"] = -1
        finally:
            RUN_STATE["running"] = False
            RUN_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return True, "started"

RESULTS_DIR = Path("results")


def load_results() -> list[dict]:
    rows: dict[tuple[str, str], dict] = {}
    for path in sorted(RESULTS_DIR.glob("result_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = (data.get("task_id", "?"), data.get("model", "?"))
        entry = {
            "file": path.name,
            "mtime": path.stat().st_mtime,
            "task_id": data.get("task_id"),
            "model": data.get("model"),
            "k": data.get("k"),
            "pass_all_k": data.get("pass_all_k"),
            "pass_hat_k": data.get("pass_hat_k"),
            "avg_score": data.get("avg_score"),
            "avg_turns": data.get("avg_turns"),
            "avg_elapsed_sec": data.get("avg_elapsed_sec"),
            "tamper_detected_count": data.get("tamper_detected_count"),
            "invalid_tool_call_count": data.get("invalid_tool_call_count"),
            "skills": data.get("skills", {}),
            "base_url": (data.get("environment") or {}).get("base_url"),
        }
        if key not in rows or entry["mtime"] > rows[key]["mtime"]:
            rows[key] = entry
    return list(rows.values())


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-bench results</title>
<style>
.viz-root {
  --surface-1: #fcfcfb; --page: #f9f9f7;
  --ink-1: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --good: #006300;
  --s1: #2a78d6; --s2: #1baf7a; --s3: #eda100;
  --s4: #008300; --s5: #4a3aa7; --s6: #e34948;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --surface-1: #1a1a19; --page: #0d0d0d;
    --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --good: #0ca30c;
    --s1: #3987e5; --s2: #199e70; --s3: #c98500;
    --s4: #008300; --s5: #9085e9; --s6: #e66767;
  }
}
* { box-sizing: border-box; }
body.viz-root {
  margin: 0; padding: 24px; background: var(--page); color: var(--ink-1);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 20px; margin: 0 0 2px; }
.sub { color: var(--ink-2); margin: 0 0 20px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 0 0 18px; }
.legend .chip { display: flex; align-items: center; gap: 6px; color: var(--ink-2); }
.legend .swatch { width: 12px; height: 12px; border-radius: 3px; }
section.card {
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px; margin-bottom: 22px;
}
h2 { font-size: 15px; margin: 0 0 12px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
th, td {
  text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--grid);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
th { color: var(--ink-muted); font-weight: 500; font-size: 12px; }
th:first-child, td:first-child { text-align: left; }
td.model { color: var(--ink-1); max-width: 340px; overflow: hidden; text-overflow: ellipsis; }
td .dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 7px; }
.pass { color: var(--good); font-weight: 600; }
.fail { color: var(--ink-muted); }
svg text { font: 12px system-ui, -apple-system, "Segoe UI", sans-serif; }
.tooltip {
  position: fixed; pointer-events: none; z-index: 10; display: none;
  background: var(--surface-1); color: var(--ink-1);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 10px; font-size: 12px;
  box-shadow: 0 4px 14px rgba(0,0,0,0.18);
}
.tooltip .t-title { font-weight: 600; margin-bottom: 2px; }
.tooltip .t-sub { color: var(--ink-2); }
.empty { color: var(--ink-2); padding: 40px 0; text-align: center; }
tbody tr.clickable { cursor: pointer; }
tbody tr.clickable:hover td { background: rgba(128,128,128,0.06); }
tr.detail-row > td { padding: 0 0 14px; }
.trial {
  border: 1px solid var(--border); border-radius: 8px;
  margin: 10px 0 0; padding: 10px 12px; background: var(--page);
}
.trial-head { display: flex; gap: 14px; flex-wrap: wrap; color: var(--ink-2);
              font-size: 12px; margin-bottom: 8px; align-items: baseline; }
.trial-head .t-pass { color: var(--good); font-weight: 600; }
.trial-head .t-fail { color: var(--ink-muted); font-weight: 600; }
.turn { border-left: 2px solid var(--grid); margin: 6px 0 6px 4px;
        padding: 2px 0 2px 12px; }
.turn-line { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.turn-no { color: var(--ink-muted); font-size: 11px; min-width: 18px; }
.tool-badge { font-size: 11px; padding: 1px 7px; border-radius: 9px;
              border: 1px solid var(--border); color: var(--ink-2); }
.tool-badge.invalid { border-color: var(--s6); color: var(--s6); }
.turn-cmd { font-family: ui-monospace, Consolas, monospace; font-size: 12px;
            overflow-wrap: anywhere; }
.exit-ok { color: var(--good); font-size: 12px; white-space: nowrap; }
.exit-bad { color: #d03b3b; font-size: 12px; white-space: nowrap; }
.turn-io { display: none; margin: 6px 0 2px; }
.turn.open .turn-io { display: block; }
.turn-io pre {
  margin: 4px 0; padding: 8px 10px; border-radius: 6px; max-height: 260px;
  overflow: auto; background: var(--surface-1); border: 1px solid var(--border);
  font: 11.5px/1.5 ui-monospace, Consolas, monospace; white-space: pre-wrap;
  overflow-wrap: anywhere; color: var(--ink-2);
}
.turn-io .io-label { font-size: 11px; color: var(--ink-muted); }
.note { color: var(--ink-2); font-size: 12.5px; font-style: italic;
        margin: 4px 0 4px 4px; padding-left: 12px;
        border-left: 2px solid var(--baseline); overflow-wrap: anywhere; }
.flags-line { font-size: 12px; color: var(--ink-2); margin-top: 8px; }
.hint { color: var(--ink-muted); font-size: 12px; margin: -8px 0 10px; }
.runbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.runbar select, .runbar input, .runbar button {
  font: inherit; color: var(--ink-1); background: var(--page);
  border: 1px solid var(--border); border-radius: 7px; padding: 6px 10px;
}
.runbar select { max-width: 300px; }
.runbar .grow { flex: 1 1 220px; min-width: 180px; }
.runbar button {
  background: var(--s1); color: #fff; border: none; cursor: pointer;
  font-weight: 600; padding: 6px 18px;
}
.runbar button:disabled { opacity: 0.5; cursor: default; }
.runlog {
  display: none; margin-top: 12px; max-height: 300px; overflow: auto;
  background: var(--page); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; font: 11.5px/1.55 ui-monospace, Consolas, monospace;
  white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink-2);
}
.runstatus { font-size: 12px; color: var(--ink-muted); }
.artifacts { margin-top: 8px; }
.artifacts .a-links { display: flex; gap: 10px; flex-wrap: wrap;
                      font-size: 12px; margin-bottom: 6px; }
.artifacts a { color: var(--s1); }
.artifacts iframe {
  width: 100%; height: 420px; border: 1px solid var(--border);
  border-radius: 8px; background: #fff;
}
.artifacts img { max-width: 100%; border: 1px solid var(--border);
                 border-radius: 8px; }
</style>
</head>
<body class="viz-root">
<h1>agent-bench</h1>
<p class="sub" id="subtitle">loading…</p>
<section class="card">
  <h2>Run</h2>
  <div class="runbar">
    <input class="grow" id="run-base" placeholder="base URL (http://host:1234/v1)">
    <select id="run-model"><option value="">model…</option></select>
    <select id="run-task"></select>
    <select id="run-k"><option>1</option><option selected>3</option><option>5</option></select>
    <button id="run-btn">Run</button>
    <span class="runstatus" id="run-status"></span>
  </div>
  <div class="runlog" id="run-log"></div>
</section>
<div class="legend" id="legend"></div>
<div id="sections"></div>
<div class="tooltip" id="tooltip"></div>
<script>
const SLOTS = ['--s1','--s2','--s3','--s4','--s5','--s6'];
const css = v => getComputedStyle(document.body).getPropertyValue(v).trim();

function shortModel(m) {
  const parts = m.split('/');
  return parts[parts.length - 1].replace(/\.gguf$/, '');
}

async function main() {
  const rows = await loadAndRender();
  initRunPanel(rows);
}

async function loadAndRender() {
  const rows = await (await fetch('api/results')).json();
  document.getElementById('legend').innerHTML = '';
  document.getElementById('sections').innerHTML = '';
  const sub = document.getElementById('subtitle');
  if (!rows.length) {
    sub.textContent = 'No result files found in results/.';
    document.getElementById('sections').innerHTML =
      '<div class="empty">Run a benchmark from the panel above.</div>';
    return rows;
  }
  // Color follows the entity: fixed assignment from the sorted model list,
  // never re-assigned when a task section has fewer models.
  const models = [...new Set(rows.map(r => r.model))].sort();
  const colorOf = m => css(SLOTS[models.indexOf(m) % SLOTS.length]);
  sub.textContent = `${rows.length} result set(s) · ${models.length} model(s) · newest file per (task, model)`;

  const legend = document.getElementById('legend');
  for (const m of models) {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.innerHTML = `<span class="swatch" style="background:${colorOf(m)}"></span>${shortModel(m)}`;
    legend.appendChild(chip);
  }

  const tasks = [...new Set(rows.map(r => r.task_id))].sort();
  const container = document.getElementById('sections');
  for (const task of tasks) {
    const trows = rows.filter(r => r.task_id === task)
                      .sort((a, b) => models.indexOf(a.model) - models.indexOf(b.model));
    const sec = document.createElement('section');
    sec.className = 'card';
    sec.innerHTML = `<h2>${task}</h2>`;
    sec.appendChild(matrixTable(trows, colorOf));
    sec.appendChild(skillChart(trows, colorOf));
    container.appendChild(sec);
  }
  // #open or #open=N auto-expands a row (handy for screenshots/shared links).
  const m = location.hash.match(/^#open(?:=(\d+))?$/);
  if (m) document.querySelectorAll('tr.clickable')[Number(m[1] || 0)]?.click();
  return rows;
}

async function initRunPanel(rows) {
  const base = document.getElementById('run-base');
  const modelSel = document.getElementById('run-model');
  const taskSel = document.getElementById('run-task');
  const btn = document.getElementById('run-btn');
  const status = document.getElementById('run-status');
  const log = document.getElementById('run-log');

  const latest = [...rows].sort((a, b) => b.mtime - a.mtime)[0];
  base.value = (latest && latest.base_url) || 'http://localhost:1234/v1';

  const tasks = await (await fetch('api/tasks')).json();
  taskSel.innerHTML = tasks.builtin.map(t => `<option value="${t}">${t}</option>`).join('') +
    tasks.skills.map(s => `<option value="${esc(s.name)}">skill: ${esc(s.name)}</option>`).join('');

  async function loadModels() {
    modelSel.innerHTML = '<option value="">loading…</option>';
    const data = await (await fetch('api/models?base_url=' +
                                    encodeURIComponent(base.value))).json();
    if (Array.isArray(data) && data.length) {
      modelSel.innerHTML = data.map(m =>
        `<option value="${esc(m)}">${esc(shortModel(m))}</option>`).join('');
      status.textContent = '';
    } else {
      modelSel.innerHTML = '<option value="">(no models)</option>';
      status.textContent = data.error ? 'server unreachable: ' + data.error : '';
    }
  }
  base.addEventListener('change', loadModels);
  loadModels();

  let polling = null;
  async function poll(offset) {
    const st = await (await fetch('api/run/status?offset=' + offset)).json();
    if (st.lines.length) {
      log.textContent += st.lines.join('\n') + '\n';
      log.scrollTop = log.scrollHeight;
    }
    status.textContent = st.label + (st.running ? ' — running…'
      : st.exit_code === 0 ? ' — done' : ` — exited ${st.exit_code}`);
    if (st.running) {
      polling = setTimeout(() => poll(st.total), 1200);
    } else {
      btn.disabled = false;
      if (st.exit_code !== null) await loadAndRender();
    }
  }

  btn.addEventListener('click', async () => {
    if (!modelSel.value) { status.textContent = 'pick a model'; return; }
    btn.disabled = true;
    log.style.display = 'block';
    log.textContent = '';
    const resp = await (await fetch('api/run', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: modelSel.value, task: taskSel.value,
        k: Number(document.getElementById('run-k').value),
        base_url: base.value, server: 'GUI',
      }),
    })).json();
    if (!resp.ok) {
      status.textContent = resp.message;
      btn.disabled = false;
      return;
    }
    clearTimeout(polling);
    poll(0);
  });
}

const esc = s => String(s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function matrixTable(trows, colorOf) {
  const wrap = document.createElement('div');
  const hint = document.createElement('p');
  hint.className = 'hint';
  hint.textContent = 'クリックで試行トランスクリプト（ターンごとの実行内容）を展開';
  const t = document.createElement('table');
  t.innerHTML = `<thead><tr>
    <th>model</th><th>pass^k</th><th>pass rate</th><th>score</th>
    <th>turns</th><th>elapsed</th><th>tamper</th><th>bad calls</th><th>k</th>
  </tr></thead>`;
  const tb = document.createElement('tbody');
  for (const r of trows) {
    const tr = document.createElement('tr');
    tr.className = 'clickable';
    tr.innerHTML = `
      <td class="model" title="${esc(r.model)}"><span class="dot" style="background:${colorOf(r.model)}"></span>${esc(shortModel(r.model))}</td>
      <td class="${r.pass_all_k ? 'pass' : 'fail'}">${r.pass_all_k ? '✓ pass' : '✗'}</td>
      <td>${Math.round(r.pass_hat_k * 100)}%</td>
      <td>${r.avg_score.toFixed(2)}</td>
      <td>${r.avg_turns}</td>
      <td>${r.avg_elapsed_sec}s</td>
      <td>${r.tamper_detected_count}</td>
      <td>${r.invalid_tool_call_count}</td>
      <td>${r.k}</td>`;
    tb.appendChild(tr);
    const detail = document.createElement('tr');
    detail.className = 'detail-row';
    detail.style.display = 'none';
    detail.innerHTML = `<td colspan="9"></td>`;
    tb.appendChild(detail);
    tr.addEventListener('click', async () => {
      if (detail.style.display !== 'none') { detail.style.display = 'none'; return; }
      const cell = detail.firstElementChild;
      if (!cell.dataset.loaded) {
        cell.innerHTML = '<div class="hint" style="padding:10px 0 0">loading…</div>';
        const data = await (await fetch('api/detail?file=' + encodeURIComponent(r.file))).json();
        cell.innerHTML = '';
        for (const trial of data.trials || []) cell.appendChild(trialView(trial));
        cell.dataset.loaded = '1';
      }
      detail.style.display = '';
    });
  }
  t.appendChild(tb);
  wrap.appendChild(hint);
  wrap.appendChild(t);
  return wrap;
}

// One trial = a transcript: what the model said and did, turn by turn.
// Click a turn line to unfold its stdout/stderr.
function trialView(trial) {
  const div = document.createElement('div');
  div.className = 'trial';
  const flags = (trial.skill_results || [])
    .map(s => `${s.reached ? '✓' : '✗'} ${esc(s.skill_id)}` +
              (s.reached_turn ? `@${s.reached_turn}` : ''))
    .join(' · ');
  div.innerHTML = `<div class="trial-head">
      <span class="${trial.passed ? 't-pass' : 't-fail'}">trial ${trial.trial_index}: ${trial.passed ? 'pass' : 'fail'}</span>
      <span>score ${trial.score}</span><span>${trial.turns_taken} turns</span>
      <span>${trial.total_elapsed_sec}s</span>
      ${trial.tamper_detected ? '<span style="color:#d03b3b">⚠ tamper</span>' : ''}
      ${trial.failure_reason ? `<span>${esc(trial.failure_reason)}</span>` : ''}
    </div>`;
  const notes = {};
  for (const n of trial.assistant_notes || []) notes[n.turn] = n.text;
  const byTurn = {};
  for (const log of trial.turn_logs || []) (byTurn[log.turn] ??= []).push(log);
  const turns = [...new Set([...Object.keys(byTurn), ...Object.keys(notes)])]
    .map(Number).sort((a, b) => a - b);
  for (const tn of turns) {
    if (notes[tn]) {
      const note = document.createElement('div');
      note.className = 'note';
      note.textContent = notes[tn].length > 400 ? notes[tn].slice(0, 400) + '…' : notes[tn];
      div.appendChild(note);
    }
    for (const log of byTurn[tn] || []) {
      const arg = log.tool_used === 'execute_command' ? (log.args.command ?? '')
                : log.tool_used === 'read_file' ? (log.args.path ?? '')
                : log.tool_used === 'write_file' ? (log.args.path ?? '')
                : JSON.stringify(log.args);
      const turn = document.createElement('div');
      turn.className = 'turn';
      turn.innerHTML = `<div class="turn-line">
          <span class="turn-no">${log.turn}</span>
          <span class="tool-badge ${log.invalid_tool_call ? 'invalid' : ''}">${esc(log.tool_used)}${log.invalid_tool_call ? ' (invalid)' : ''}</span>
          <span class="turn-cmd">${esc(arg)}</span>
          <span class="${log.exit_code === 0 ? 'exit-ok' : 'exit-bad'}">${log.exit_code === 0 ? '✓ exit 0' : '✗ exit ' + log.exit_code}</span>
          <span class="turn-no">${log.elapsed_sec}s</span>
        </div>
        <div class="turn-io">
          ${log.tool_used === 'write_file' && log.args.content ? `<div class="io-label">content</div><pre>${esc(log.args.content)}</pre>` : ''}
          ${log.stdout ? `<div class="io-label">stdout</div><pre>${esc(log.stdout)}</pre>` : ''}
          ${log.stderr ? `<div class="io-label">stderr</div><pre>${esc(log.stderr)}</pre>` : ''}
        </div>`;
      turn.querySelector('.turn-line').addEventListener('click',
        () => turn.classList.toggle('open'));
      div.appendChild(turn);
    }
  }
  if (trial.final_text) {
    const fin = document.createElement('div');
    fin.className = 'note';
    fin.textContent = '最終回答: ' + (trial.final_text.length > 600
      ? trial.final_text.slice(0, 600) + '…' : trial.final_text);
    div.appendChild(fin);
  }
  if (trial.artifacts_dir && (trial.artifacts || []).length) {
    const art = document.createElement('div');
    art.className = 'artifacts';
    const href = p => encodeURI('/' + trial.artifacts_dir + '/' + p);
    art.innerHTML = '<div class="io-label">成果物 (artifacts)</div>' +
      '<div class="a-links">' + trial.artifacts.map(p =>
        `<a href="${href(p)}" target="_blank">${esc(p)}</a>`).join('') + '</div>';
    // Inline preview of the first visual artifact: HTML in a sandboxed
    // iframe (styles render, scripts stay off), images directly.
    const html = trial.artifacts.find(p => /\.html?$/i.test(p));
    const img = trial.artifacts.find(p => /\.(png|jpe?g|gif|svg|webp)$/i.test(p));
    if (html) {
      const f = document.createElement('iframe');
      f.setAttribute('sandbox', '');
      f.src = href(html);
      art.appendChild(f);
    } else if (img) {
      const i = document.createElement('img');
      i.src = href(img);
      art.appendChild(i);
    }
    div.appendChild(art);
  }
  const fl = document.createElement('div');
  fl.className = 'flags-line';
  fl.textContent = flags;
  div.appendChild(fl);
  return div;
}

// Horizontal grouped bars: skill groups on y, one thin bar per model,
// reach_rate 0-100% on x. Direct % label at every bar end (relief rule for
// the sub-3:1 light slots), 2px gap between adjacent bars, 4px rounded
// data-end anchored to the left baseline.
function skillChart(trows, colorOf) {
  const skills = [...new Set(trows.flatMap(r => Object.keys(r.skills)))];
  const BAR = 13, GAP = 2, GROUP_PAD = 12, LEFT = 210, RIGHT = 56, TOP = 18;
  const plotW = 420;
  const groupH = trows.length * (BAR + GAP) - GAP;
  const H = TOP + skills.length * (groupH + GROUP_PAD) + 22;
  const W = LEFT + plotW + RIGHT;
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.style.maxWidth = W + 'px';
  const el = (n, at, parent) => {
    const e = document.createElementNS(svgNS, n);
    for (const k in at) e.setAttribute(k, at[k]);
    (parent || svg).appendChild(e); return e;
  };
  // Recessive hairline grid at 0/25/50/75/100%.
  for (const pct of [0, 25, 50, 75, 100]) {
    const x = LEFT + plotW * pct / 100;
    el('line', {x1: x, y1: TOP - 4, x2: x, y2: H - 22,
                stroke: pct === 0 ? css('--baseline') : css('--grid'),
                'stroke-width': 1});
    const tx = el('text', {x: x, y: H - 8, 'text-anchor': 'middle',
                           fill: css('--ink-muted')});
    tx.textContent = pct + '%';
  }
  const tip = document.getElementById('tooltip');
  skills.forEach((skill, si) => {
    const gy = TOP + si * (groupH + GROUP_PAD);
    const label = el('text', {x: LEFT - 10, y: gy + groupH / 2 + 4,
                              'text-anchor': 'end', fill: css('--ink-2')});
    label.textContent = skill.length > 26 ? skill.slice(0, 25) + '…' : skill;
    trows.forEach((r, mi) => {
      const st = r.skills[skill];
      if (!st) return;
      const y = gy + mi * (BAR + GAP);
      const w = Math.max(plotW * st.reach_rate, st.reach_rate > 0 ? 4 : 0);
      const rr = Math.min(4, w);
      if (w > 0) {
        // Flat at the baseline (left), 4px rounded data-end (right).
        el('path', {d: `M${LEFT},${y} h${w - rr} a${rr},${rr} 0 0 1 ${rr},${rr}
                        v${BAR - 2 * rr} a${rr},${rr} 0 0 1 -${rr},${rr}
                        h-${w - rr} z`,
                    fill: colorOf(r.model)});
      } else {
        el('line', {x1: LEFT, y1: y, x2: LEFT, y2: y + BAR,
                    stroke: css('--baseline'), 'stroke-width': 2});
      }
      const vt = el('text', {x: LEFT + w + 6, y: y + BAR - 3,
                             fill: css('--ink-2')});
      vt.textContent = Math.round(st.reach_rate * 100) + '%';
      // Hover target taller than the mark itself.
      const hit = el('rect', {x: LEFT - 2, y: y - 1, width: plotW + RIGHT,
                              height: BAR + 2, fill: 'transparent'});
      hit.addEventListener('mousemove', ev => {
        tip.style.display = 'block';
        tip.style.left = (ev.clientX + 14) + 'px';
        tip.style.top = (ev.clientY + 12) + 'px';
        tip.innerHTML = `<div class="t-title">${shortModel(r.model)}</div>
          <div>${skill}: ${Math.round(st.reach_rate * 100)}%</div>
          <div class="t-sub">avg turns to reach: ${st.avg_turns ?? '—'}</div>`;
      });
      hit.addEventListener('mouseleave', () => tip.style.display = 'none');
    });
  });
  return svg;
}
main();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        elif url.path == "/api/results":
            body = json.dumps(load_results()).encode()
            ctype = "application/json"
        elif url.path == "/api/detail":
            name = parse_qs(url.query).get("file", [""])[0]
            path = RESULTS_DIR / name
            if not RESULT_FILE_RE.match(name) or not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            ctype = "application/json"
        elif url.path == "/api/tasks":
            body = json.dumps({"builtin": BUILTIN_TASK_IDS,
                               "skills": discover_skills()}).encode()
            ctype = "application/json"
        elif url.path == "/api/models":
            base = parse_qs(url.query).get("base_url", [""])[0]
            try:
                body = json.dumps(fetch_models(base)).encode()
            except Exception as exc:  # noqa: BLE001
                body = json.dumps({"error": str(exc)}).encode()
            ctype = "application/json"
        elif url.path == "/api/run/status":
            offset = int(parse_qs(url.query).get("offset", ["0"])[0])
            lines = list(RUN_STATE["lines"])
            body = json.dumps({
                "running": RUN_STATE["running"],
                "label": RUN_STATE["label"],
                "exit_code": RUN_STATE["exit_code"],
                "total": len(lines),
                "lines": lines[offset:],
            }).encode()
            ctype = "application/json"
        elif url.path.startswith("/artifacts/"):
            rel = url.path[len("/artifacts/"):]
            base = (RESULTS_DIR / "artifacts").resolve()
            target = (base / rel).resolve()
            if not str(target).startswith(str(base)) or not target.is_file():
                self.send_error(404)
                return
            body = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "text/plain"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/run":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode())
            ok, msg = start_run(payload)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        body = json.dumps({"ok": ok, "message": msg}).encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main() -> None:
    global RESULTS_DIR
    ap = argparse.ArgumentParser(description="agent-bench results dashboard")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    RESULTS_DIR = Path(args.results_dir)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"agent-bench dashboard: http://{args.host}:{args.port}/  "
          f"(results: {RESULTS_DIR}/)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
