#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <release-tag>" >&2
  exit 1
fi

TAG="$1"
OUT_FILE=".github/badges/release.svg"

mkdir -p "$(dirname "$OUT_FILE")"

python3 - "$TAG" "$OUT_FILE" <<'PY'
import html
import sys
from pathlib import Path

tag = sys.argv[1]
out_file = Path(sys.argv[2])

left_label = "release"
right_label = tag

left_width = 54
right_width = max(58, 10 + len(right_label) * 7)
total_width = left_width + right_width

left_x = left_width / 2
right_x = left_width + right_width / 2

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" role="img" aria-label="{html.escape(left_label + ': ' + right_label)}">
<title>{html.escape(left_label + ': ' + right_label)}</title>
<rect width="{left_width}" height="20" fill="#555"/>
<rect x="{left_width}" width="{right_width}" height="20" fill="#007ec6"/>
<g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
  <text x="{left_x}" y="14">{html.escape(left_label)}</text>
  <text x="{right_x}" y="14">{html.escape(right_label)}</text>
</g>
</svg>
'''

out_file.write_text(svg)
PY
