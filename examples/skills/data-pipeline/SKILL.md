# data-pipeline skill

Turns the bundled sales data into a published Markdown report.

## Steps (run in order)

1. **read_manifest** — inspect `data/manifest.json` (inside this skill
   directory) to confirm the dataset location and row count.
2. **csv_summarize** — summarize the dataset: input `data/sales.csv`,
   output `/workspace/output/summary.txt`.
   Implemented by `scripts/csv_summarize.py`
   (`--input <csv> --out <txt>`).
3. **publish_report** — publish the summary as Markdown:
   `/workspace/output/summary.txt` -> `/workspace/output/report.md`.
   Implemented by `scripts/publish_report.sh <summary> <out_md>`.

Both artifacts must exist when the pipeline is done.
