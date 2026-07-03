# imitate-dashboard

Builds a new dashboard page in the **design language of a reference page**.

## Procedure

1. Read `/workspace/skill/reference.html` carefully. Note its design language:
   dark navy theme driven by CSS custom properties, a sticky header with a
   badge and an accent-colored word in the title, a two-column layout
   (settings sidebar + tabbed main area), cards with headers, range sliders
   with value readouts, a gradient run button, tab bar, and a results table
   with mini progress bars.
2. Create `/workspace/output/index.html` — a **new** dashboard on the topic
   given in your instructions, reusing the same design language and
   component structure (header with badge, sidebar settings card with at
   least one `<select>` and one range slider, a run button, a tab bar with
   at least 3 tabs, and a results table with mini bars).
3. The file must be **one self-contained HTML file**: all CSS inline in a
   `<style>` block, system fonts only, no external resources, no network.
4. Verify the file exists (e.g. `ls output/`).

Do not copy the reference verbatim — the topic, texts and data must match
the requested subject.
