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
    chunks = []
    total = 0
    s.settimeout(30)  # long timeout before first chunk (scanner is warming up)
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            print(f"\r  Received: {total/1_000_000:.1f} / ~{expected_mb} MB", end='', flush=True)
            s.settimeout(5)  # short timeout once data is flowing
    except socket.timeout:
        pass
    finally:
        s.close()

    print(f"\r  Received: {total/1_000_000:.1f} MB — done.              ")
    return b''.join(chunks)


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


def save_output(img, path, dpi):
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else 'png'
    if ext == 'pdf':
        img.save(path, 'PDF', resolution=dpi)
        print(f"Saved PDF: {path}")
    else:
        img.save(path, 'PNG')
        print(f"Saved PNG: {path}")


def main():
    parser = argparse.ArgumentParser(description='Scan from HP Photosmart C4580 over WiFi.')
    parser.add_argument('output', help='Output file: scan.png or scan.pdf')
    parser.add_argument('--dpi', type=int, default=75, choices=[75, 100, 150, 200, 300],
                        help='Scan resolution in DPI (default: 75)')
    parser.add_argument('--ip', default=PRINTER_IP, help=f'Printer IP (default: {PRINTER_IP})')
    args = parser.parse_args()

    raw = scan_raw(args.ip, args.dpi)
    print("Parsing scan data...")
    pixels, width, height = parse_mfpdtf(raw)
    print(f"Image dimensions: {width}x{height} px at {args.dpi} DPI "
          f"({width/args.dpi:.1f}\" x {height/args.dpi:.1f}\")")

    img = build_image(pixels, width, height)
    save_output(img, args.output, args.dpi)


if __name__ == '__main__':
    main()
