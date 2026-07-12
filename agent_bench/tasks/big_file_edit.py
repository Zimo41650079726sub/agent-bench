"""TaskBigFileEdit (big_file_edit_v1): four small edits inside a ~40 KB
HTML page; everything else must survive.

Targets a measured failure mode (gemma-4-12b, imitate-dashboard runs):
when the file being written is large, mid-size models regenerate the whole
document instead of editing in place — truncating it partway or dropping
write_file's `path` argument during the long generation. The anchor flags
catch the damage; args_intact catches the dropped argument.
"""

from __future__ import annotations

import shlex

from .base import BenchTask, BenchContext, Skill

PAGE_PATH = "/workspace/site/index.html"

OLD_TITLE = "<title>Acme Dashboard</title>"
NEW_TITLE = "<title>Acme Control Center</title>"
OLD_ACCENT = "--accent: #3b82f6;"
NEW_ACCENT = "--accent: #e11d48;"
OLD_LINK = '<a href="/contatc">Contact us</a>'
NEW_LINK = '<a href="/contact">Contact us</a>'
NEW_NAV_ITEM = '<li><a href="/blog">Blog</a></li>'
NAV_NEWS_ITEM = '<li><a href="/news">News</a></li>'

# Sentences scattered through the document; all five must survive the edit.
# A truncated or partially regenerated file loses the later ones first.
ANCHOR_SECTIONS = (5, 18, 31, 44, 57)


def _anchor(n: int) -> str:
    return (f"Legacy note {n:02d}: the module {n:02d} rollout completed "
            f"during maintenance window W{n * 3}.")


def _build_page() -> str:
    head = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{OLD_TITLE}
<style>
:root {{ {OLD_ACCENT} --bg: #0f172a; --fg: #e2e8f0; }}
body {{ font-family: system-ui, sans-serif; background: var(--bg); color: var(--fg); margin: 0; }}
header {{ padding: 2rem; border-bottom: 2px solid var(--accent); }}
section {{ padding: 1.25rem 2rem; border-bottom: 1px solid #1e293b; }}
h2 {{ color: var(--accent); margin: 0 0 .5rem; }}
footer {{ padding: 2rem; }}
</style>
</head>
<body>
<header>
<h1>Acme Operations</h1>
<ul id="nav"><li><a href="/">Home</a></li>{NAV_NEWS_ITEM}<li><a href="/docs">Docs</a></li></ul>
</header>
"""
    sections = []
    for i in range(1, 61):
        extra = f"\n  <p>{_anchor(i)}</p>" if i in ANCHOR_SECTIONS else ""
        sections.append(f"""<section id="s{i:02d}">
  <h2>Module {i:02d}</h2>
  <p>Module {i:02d} handles pipeline stage {i * 7 % 97} of the Acme intake
  process. It normalizes upstream records, applies retention rule R{i:02d},
  and forwards the batch to the downstream queue. Average throughput is
  {200 + i * 3} records per minute with a p99 latency budget of
  {40 + i} milliseconds. Operational ownership belongs to rotation
  team {(i - 1) % 6 + 1}, and the escalation runbook lives in the internal
  wiki under section {i * 11 % 89}.</p>{extra}
</section>""")
    footer = f"""<footer>
<p>Questions about this dashboard? {OLD_LINK} or file a ticket.</p>
<p>&copy; 2026 Acme Corp.</p>
</footer>
</body>
</html>
"""
    return head + "\n".join(sections) + "\n" + footer


PROMPT = f"""\
The file /workspace/site/index.html is a large production page. Make exactly
these four changes and nothing else — the rest of the file must stay intact:

1. Page title: change "Acme Dashboard" to "Acme Control Center".
2. In the CSS, change the accent color: --accent: #3b82f6; -> --accent: #e11d48;
3. Fix the broken footer link: href="/contatc" -> href="/contact".
4. Add {NEW_NAV_ITEM} to the nav list (id="nav"), after the News item.

Do NOT rewrite or reformat unrelated parts of the file.
"""


class TaskBigFileEdit(BenchTask):
    id = "big_file_edit_v1"
    allowed_writes = ["site/index.html"]
    max_turns = 14

    async def setup(self, sandbox) -> None:
        await sandbox.write_file(PAGE_PATH, _build_page())

    def get_prompt(self) -> str:
        return PROMPT

    async def _has(self, ctx: BenchContext, needle: str) -> bool:
        code, _, _ = await ctx.harness_exec(
            f"grep -qF -- {shlex.quote(needle)} {PAGE_PATH}")
        return code == 0

    def _make_swap_eval(self, new: str, old: str):
        # The replacement landed AND the original text is gone.
        async def _eval(ctx: BenchContext):
            return await self._has(ctx, new) and not await self._has(ctx, old)
        return _eval

    async def _eval_nav_added(self, ctx: BenchContext):
        return await self._has(ctx, NEW_NAV_ITEM) and \
            await self._has(ctx, NAV_NEWS_ITEM)

    async def _eval_anchors(self, ctx: BenchContext):
        for n in ANCHOR_SECTIONS:
            if not await self._has(ctx, _anchor(n)):
                return False
        return True

    async def _eval_args_intact(self, ctx: BenchContext):
        # The measured 12b failure emits write_file with parseable JSON that
        # simply lacks `path` — that is NOT an invalid_tool_call (the name
        # and JSON are fine), so check the dispatcher's error too.
        for log in ctx.turn_logs:
            if log.invalid_tool_call:
                return False
            if log.tool_used == "write_file" and "requires a 'path'" in log.stderr:
                return False
        return True

    @property
    def skills(self) -> list[Skill]:
        return [
            Skill("change_title", "title updated, old title gone",
                  self._make_swap_eval(NEW_TITLE, OLD_TITLE)),
            Skill("change_accent", "accent color swapped",
                  self._make_swap_eval(NEW_ACCENT, OLD_ACCENT)),
            Skill("change_link", "footer link typo fixed",
                  self._make_swap_eval(NEW_LINK, OLD_LINK)),
            Skill("change_nav", "Blog item added, News item still present",
                  self._eval_nav_added),
            Skill("anchors_preserved",
                  "all 5 anchor sentences survived (no truncation/rewrite)",
                  self._eval_anchors),
            Skill("args_intact",
                  "no invalid tool calls (path kept during long writes)",
                  self._eval_args_intact),
        ]
