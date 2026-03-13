#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 -c "import PIL" 2>/dev/null || pip3 install pillow -q
cd "$DIR" && python3 hp_scan_ui.py
