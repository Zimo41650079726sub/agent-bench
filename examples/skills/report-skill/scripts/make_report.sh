#!/bin/sh
# The "real" heavy script. The bench replaces this with a recorder stub via
# bench_manifest.json, so this body never runs during benchmarking.
mkdir -p /workspace/output
echo "report: $*" > /workspace/output/report.txt
