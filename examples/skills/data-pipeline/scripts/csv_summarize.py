#!/usr/bin/env python3
"""csv_summarize: aggregate a sales CSV into a plain-text summary."""
import argparse
import csv
import os

ap = argparse.ArgumentParser()
ap.add_argument("--input", required=True)
ap.add_argument("--out", required=True)
args = ap.parse_args()

rows = list(csv.DictReader(open(args.input)))
per: dict[str, int] = {}
total = 0
for r in rows:
    amount = int(r["units"]) * int(r["price"])
    per[r["region"]] = per.get(r["region"], 0) + amount
    total += amount

os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
with open(args.out, "w") as f:
    f.write(f"ROWS={len(rows)}\n")
    for region, amount in per.items():
        f.write(f"REGION {region}={amount}\n")
    f.write(f"TOTAL={total}\n")
print(f"wrote {args.out}")
