# HP Photosmart C4580 WiFi Scanner

Scan from an HP Photosmart C4580 over WiFi on macOS — **no drivers required**.

macOS dropped support for this printer's drivers, and the C4580 is too old to support eSCL/AirScan. This project reverse-engineers the HP scan protocol directly: it connects to the scanner on TCP port 9290, sends SCL commands, and decodes the raw MFPDTF image stream into PNG or PDF.

Comes with a simple GUI and double-click shortcuts.

![Scanner GUI](https://github.com/user-attachments/assets/placeholder)

## Requirements

- macOS (tested on macOS 26 Tahoe)
- Python 3
- [Pillow](https://python-pillow.org/)

```bash
pip3 install pillow
```

## Setup

1. Clone or download this repo
2. Open `hp_scan.py` and set `PRINTER_IP` to your printer's IP address
3. Do the same in `hp_scan_ui.py` (`PRINTER_IP` constant in `ScannerApp`)

To find your printer's IP, check your router's device list, or use:

```bash
dns-sd -B _scanner._tcp local
```

## Usage

### GUI

Double-click `Scanner.command` (right-click → Open the first time, due to macOS security).

Choose resolution, format (PNG/PDF), output folder, and press **Scan Now**.

### Command line

```bash
# PNG at default resolution (75 DPI, fast)
python3 hp_scan.py scan.png

# PDF
python3 hp_scan.py scan.pdf

# Higher resolution
python3 hp_scan.py scan.png --dpi 150
python3 hp_scan.py scan.png --dpi 300

# Override printer IP
python3 hp_scan.py scan.png --ip 192.168.1.200
```

### Global commands

Link the scripts in `bin/` onto your `PATH`:

```bash
ln -s "$PWD/bin/scan-pdf" ~/.local/bin/scan-pdf
ln -s "$PWD/bin/scan-img" ~/.local/bin/scan-img
```

Then, from any terminal:

```bash
scan-pdf             # 150 DPI PDF → ~/Desktop/scan_<timestamp>.pdf
scan-pdf 4           # 4 pages, prompts you to swap pages, merged into one PDF
scan-img             # 300 DPI PNG → ~/Desktop/scan_<timestamp>.png
scan-img --dpi 150   # extra args are passed to hp_scan.py
```

Both commands use the project's `.venv` if it exists, and open the file when the scan finishes.

### Double-click shortcuts

- **`Scan to PNG.command`** — 300 DPI, saves timestamped PNG, opens automatically
- **`Scan to PDF.command`** — 75 DPI (fast), saves timestamped PDF, opens automatically
- **`Scanner.command`** — launches the GUI

Right-click → Open the first time (macOS Gatekeeper prompt).

## How it works

| Layer | Detail |
|---|---|
| Transport | Raw TCP to port 9290 (HP scan channel, `HPMUD_SCAN_CHANNEL`) |
| Greeting | Scanner responds with `b'00'` when ready |
| Commands | HP SCL (Scanner Control Language) — ESC-prefixed ASCII commands |
| Data format | MFPDTF (Multi-Function Peripheral Data Transfer Format) — HP's binary block framing |
| Pixel data | 24-bit RGB, uncompressed, full letter size (8.5" × 11") |

MFPDTF block structure:
- 4 bytes: block length (little-endian)
- 2 bytes: header length
- 1 byte: data type
- 1 byte: page flags (`0x1a` = end-of-page, contains row count)
- Variable header + payload
- Data blocks have an extra 4-byte raster header before pixel data

Image height is read from the end-of-page block; width is derived as `total_pixels / rows`.

## Troubleshooting

**"Could not determine image height"** — The scanner may be in a bad state after an interrupted scan. Restart the printer and try again.

**Printer shows "Cancelling"** — Restart the printer.

**Printer not on WiFi** — Use the printer display:
1. Press the **Wireless** button (antenna icon)
2. Go to **Wireless Setup Wizard**
3. Select your network and enter the password

The blue WiFi light stays solid when connected. Then find the new IP with:

```bash
dns-sd -B _scanner._tcp local
```

## License

MIT
