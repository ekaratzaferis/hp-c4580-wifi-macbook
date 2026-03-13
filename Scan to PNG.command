#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$DIR/scan_$(date +%Y%m%d_%H%M%S).png"
python3 -c "import PIL" 2>/dev/null || pip3 install pillow -q
python3 "$DIR/hp_scan.py" "$OUTPUT" --dpi 300 && open "$OUTPUT"
