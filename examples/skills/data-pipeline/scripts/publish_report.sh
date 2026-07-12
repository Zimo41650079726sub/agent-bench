#!/bin/sh
# publish_report: wrap a summary file into a Markdown report.
[ $# -eq 2 ] || { echo "usage: publish_report.sh SUMMARY OUT_MD" >&2; exit 2; }
mkdir -p "$(dirname "$2")"
{
  echo "# Sales Report"
  echo
  echo '```'
  cat "$1"
  echo '```'
} > "$2"
echo "published $2"
