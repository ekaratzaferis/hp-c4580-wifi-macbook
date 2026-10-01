#!/usr/bin/env python3
"""
HP Photosmart C4580 WiFi Scanner
Connects directly to port 9290 (HP scan channel), sends SCL commands,
parses MFPDTF response, and saves as PNG or PDF.

Usage:
  python3 hp_scan.py output.png
  python3 hp_scan.py output.pdf
  python3 hp_scan.py output.png --dpi 150
"""

import argparse
import math
import socket
import struct
import sys
import time
from PIL import Image

PRINTER_IP = '192.168.1.109'
SCAN_PORT = 9290
STALL_TIMEOUT = 60  # seconds of silence (incl. warm-up) before giving up on a scan


def scan_raw(ip, dpi=75):
    """Connect to scanner, send SCL commands, return raw MFPDTF bytes."""
    # Extent in device pixels at 300 DPI (letter size: 8.5" x 11")
    x_extent = int(8.5 * 300)   # 2550
    y_extent = int(11.0 * 300)  # 3300

    commands = [
        b'\x1bE',                           # Reset
        b'\x1b*oE',                         # Clear error stack
        b'\x1b*a5T',                        # Output type: color
        b'\x1b*a24G',                       # Data width: 24-bit RGB
        b'\x1b*m2S',                        # MFPDTF: on
        b'\x1b*a0C',                        # Compression: none
        f'\x1b*a{dpi}R'.encode(),           # X resolution
        f'\x1b*a{dpi}S'.encode(),           # Y resolution
        b'\x1b*f0X',                        # X position: 0
        b'\x1b*f0Y',                        # Y position: 0
        f'\x1b*f{x_extent}P'.encode(),      # X extent
        f'\x1b*f{y_extent}Q'.encode(),      # Y extent
        b'\x1b*oE',                         # Clear error stack
        b'\x1b*f0S',                        # START SCAN
    ]

    print(f"Connecting to {ip}:{SCAN_PORT}...")
    s = socket.socket()
    s.settimeout(10)
    s.connect((ip, SCAN_PORT))
    time.sleep(0.3)

    s.settimeout(3)
    greeting = s.recv(64).strip()
    if greeting != b'00':
        raise RuntimeError(f"Unexpected greeting: {greeting!r} (expected b'00')")
    print("Scanner ready.")

    for cmd in commands:
        s.send(cmd)
        time.sleep(0.05)

    expected_mb = round(8.5 * 11 * dpi * dpi * 3 / 1_000_000, 1)
    print(f"Scan started at {dpi} DPI (expect ~{expected_mb} MB, may take a few minutes)...")
    buf = bytearray()
    walk_pos, seen_first, done = 0, False, False
    # The scanner can pause mid-page (carriage/buffer stalls), so a quiet socket
    # is not the end of the scan; only the end-of-page block is.
    s.settimeout(STALL_TIMEOUT)
    try:
        while not done:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                raise RuntimeError(
                    f"Scanner sent no data for {STALL_TIMEOUT}s before the end of the page "
                    f"({len(buf)/1_000_000:.1f} / ~{expected_mb} MB received).")
            if not chunk:
                break
            buf += chunk
            walk_pos, seen_first, done = _walk_blocks(buf, walk_pos, seen_first)
            print(f"\r  Received: {len(buf)/1_000_000:.1f} / ~{expected_mb} MB", end='', flush=True)

        # Drain anything trailing the end-of-page block so the scanner finishes the job cleanly
        s.settimeout(2)
        try:
            while s.recv(65536):
                pass
        except socket.timeout:
            pass
    finally:
        s.close()

    print(f"\r  Received: {len(buf)/1_000_000:.1f} MB — done.              ")
    return bytes(buf)


def _walk_blocks(data, pos, seen_first):
    """Advance over complete MFPDTF blocks. Returns (pos, seen_first, end_of_page_seen)."""
    while pos + 8 <= len(data):
        block_len = struct.unpack_from('<I', data, pos)[0]
        if block_len == 0 or pos + block_len > len(data):
            break
        # First block is start-of-page metadata, matching parse_mfpdtf
        if seen_first and data[pos + 7] == 0x1a:
            return pos + block_len, True, True
        seen_first = True
        pos += block_len
    return pos, seen_first, False


def parse_mfpdtf(data):
    """
    Parse MFPDTF stream and return raw BGR pixel bytes plus image dimensions.
    Each data block: 8-byte fixed header + 4-byte raster header + pixel data.
    Returns (pixel_bytes, width, height).
    """
    pos = 0
    pixel_chunks = []
    rows = None
    first = True

    while pos < len(data) - 8:
        block_len = struct.unpack_from('<I', data, pos)[0]
        header_len = struct.unpack_from('<H', data, pos + 4)[0]
        page_flags = data[pos + 7]

        if block_len == 0 or pos + block_len > len(data):
            break

        payload_start = pos + header_len
        payload_len = block_len - header_len

        if first:
            first = False  # skip start-of-page metadata block
        elif page_flags == 0x1a:
            # End-of-page record: extract row count (bytes 4-7, little-endian)
            if payload_len >= 8:
                rows = struct.unpack_from('<I', data, payload_start + 4)[0]
        else:
            # Data block: skip 4-byte raster header (traits + pad + byteCount)
            pixel_chunks.append(data[payload_start + 4: payload_start + payload_len])

        pos += block_len

    pixels = b''.join(pixel_chunks)

    if rows is None or rows == 0:
        raise RuntimeError(
            f"Could not determine image height from MFPDTF stream. "
            f"Raw data: {len(data):,} bytes, pixel chunks: {len(pixel_chunks)}, "
            f"pixel bytes extracted: {len(pixels):,}. "
            f"The scanner may be in a bad state — try restarting the printer."
        )

    total_pixels = len(pixels) // 3
    if total_pixels % rows != 0:
        # Trim to nearest complete row
        total_pixels = (total_pixels // rows) * rows
        pixels = pixels[:total_pixels * 3]

    width = total_pixels // rows
    return pixels, width, rows


def build_image(pixels, width, height):
    """Build a PIL RGB image from raw pixel bytes."""
    return Image.frombytes('RGB', (width, height), pixels)


def file_ext(path):
    return path.rsplit('.', 1)[-1].lower() if '.' in path else 'png'


def save_output(images, path, dpi):
    if file_ext(path) == 'pdf':
        # Pages are embedded as JPEG; Pillow's default quality (75) visibly smears text
        images[0].save(path, 'PDF', resolution=dpi, save_all=True, append_images=images[1:],
                       quality=92)
        print(f"Saved {len(images)}-page PDF: {path}")
    else:
        images[0].save(path, 'PNG')
        print(f"Saved PNG: {path}")


def scan_page(ip, dpi):
    raw = scan_raw(ip, dpi)
    print("Parsing scan data...")
    pixels, width, height = parse_mfpdtf(raw)
    print(f"Image dimensions: {width}x{height} px at {dpi} DPI "
          f"({width/dpi:.1f}\" x {height/dpi:.1f}\")")
    return build_image(pixels, width, height)


def main():
    parser = argparse.ArgumentParser(description='Scan from HP Photosmart C4580 over WiFi.')
    parser.add_argument('output', help='Output file: scan.png or scan.pdf')
    parser.add_argument('--dpi', type=int, default=75, choices=[75, 100, 150, 200, 300],
                        help='Scan resolution in DPI (default: 75)')
    parser.add_argument('--ip', default=PRINTER_IP, help=f'Printer IP (default: {PRINTER_IP})')
    parser.add_argument('--pages', type=int, default=1,
                        help='Number of pages to scan into one PDF (default: 1)')
    args = parser.parse_args()

    if args.pages < 1:
        parser.error('--pages must be at least 1')
    if args.pages > 1 and file_ext(args.output) != 'pdf':
        parser.error('--pages > 1 requires a .pdf output file')

    images = []
    try:
        for n in range(1, args.pages + 1):
            if args.pages > 1:
                print(f"\n=== Page {n} of {args.pages} ===")
                if n > 1:
                    input(f"Place page {n} on the glass and press Enter...")
            while True:
                try:
                    images.append(scan_page(args.ip, args.dpi))
                    break
                except (OSError, RuntimeError) as e:
                    print(f"\nScan failed: {e}")
                    if args.pages == 1:
                        sys.exit(1)
                    input(f"Press Enter to retry page {n} (Ctrl+C to save pages so far)...")
    except (KeyboardInterrupt, EOFError):
        print()
        if not images:
            sys.exit(130)
        print(f"Stopped early — saving the {len(images)} page(s) scanned so far.")

    save_output(images, args.output, args.dpi)


if __name__ == '__main__':
    main()
