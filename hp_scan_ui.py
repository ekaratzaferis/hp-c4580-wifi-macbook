#!/usr/bin/env python3
"""
HP Photosmart C4580 WiFi Scanner — GUI
"""

import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from datetime import datetime
from pathlib import Path


# ── ensure Pillow is available ────────────────────────────────────────────────
try:
    from PIL import Image
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pillow', '-q'])
    from PIL import Image

import socket, struct, time  # noqa: E401 (after PIL check)
from hp_scan import scan_raw, parse_mfpdtf, build_image


# ── scanner thread ────────────────────────────────────────────────────────────
def run_scan(ip, dpi, fmt, out_dir, progress_cb, done_cb):
    try:
        def on_progress(received, expected):
            progress_cb(received, expected)

        # Monkey-patch scan_raw progress into this thread
        raw = _scan_raw_with_progress(ip, dpi, on_progress)
        progress_cb(-1, -1)  # signal "parsing"

        pixels, width, height = parse_mfpdtf(raw)
        img = build_image(pixels, width, height)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = Path(out_dir) / f'scan_{timestamp}.{fmt}'

        if fmt == 'pdf':
            img.save(str(out_path), 'PDF', resolution=dpi)
        else:
            img.save(str(out_path), 'PNG')

        done_cb(str(out_path), None)
    except Exception as e:
        done_cb(None, str(e))


def _scan_raw_with_progress(ip, dpi, progress_cb):
    """scan_raw with live progress callback."""
    import socket, time

    SCAN_PORT = 9290
    x_extent, y_extent = int(8.5 * 300), int(11.0 * 300)

    commands = [
        b'\x1bE', b'\x1b*oE', b'\x1b*a5T', b'\x1b*a24G', b'\x1b*m2S', b'\x1b*a0C',
        f'\x1b*a{dpi}R'.encode(), f'\x1b*a{dpi}S'.encode(),
        b'\x1b*f0X', b'\x1b*f0Y',
        f'\x1b*f{x_extent}P'.encode(), f'\x1b*f{y_extent}Q'.encode(),
        b'\x1b*oE', b'\x1b*f0S',
    ]

    s = socket.socket()
    s.settimeout(10)
    s.connect((ip, SCAN_PORT))
    time.sleep(0.3)
    s.settimeout(3)
    greeting = s.recv(64).strip()
    if greeting != b'00':
        raise RuntimeError(f'Unexpected scanner greeting: {greeting!r}')

    for cmd in commands:
        s.send(cmd)
        time.sleep(0.05)

    expected = int(8.5 * 11 * dpi * dpi * 3)
    chunks, total = [], 0
    s.settimeout(30)  # long timeout before first chunk (scanner is warming up)
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            progress_cb(total, expected)
            s.settimeout(5)  # short timeout once data is flowing
    except socket.timeout:
        pass
    finally:
        s.close()

    return b''.join(chunks)


# ── UI ────────────────────────────────────────────────────────────────────────
class ScannerApp(tk.Tk):
    PRINTER_IP = '192.168.1.109'

    def __init__(self):
        super().__init__()
        self.title('HP C4580 Scanner')
        self.resizable(False, False)
        self._build_ui()

    def _build_ui(self):
        pad = dict(padx=20, pady=6)

        # ── title ─────────────────────────────────────────────────────────────
        tk.Label(self, text='HP Photosmart C4580', font=('Helvetica', 15, 'bold')).pack(pady=(20, 2))
        tk.Label(self, text='WiFi Scanner', font=('Helvetica', 11), fg='gray').pack(pady=(0, 16))

        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=20)

        # ── resolution ────────────────────────────────────────────────────────
        tk.Label(self, text='Resolution', font=('Helvetica', 11, 'bold')).pack(anchor='w', **pad)

        self.dpi = tk.IntVar(value=75)
        options = [(75, '75 DPI  — fast, small file'), (150, '150 DPI — balanced'),
                   (300, '300 DPI — best quality, slow')]
        for val, label in options:
            ttk.Radiobutton(self, text=label, variable=self.dpi, value=val).pack(anchor='w', padx=36, pady=1)

        # ── format ────────────────────────────────────────────────────────────
        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=20, pady=(12, 0))
        tk.Label(self, text='Format', font=('Helvetica', 11, 'bold')).pack(anchor='w', **pad)

        self.fmt = tk.StringVar(value='png')
        frm_fmt = tk.Frame(self)
        frm_fmt.pack(anchor='w', padx=36)
        for val in ('PNG', 'PDF'):
            ttk.Radiobutton(frm_fmt, text=val, variable=self.fmt, value=val.lower()).pack(side='left', padx=(0, 16))

        # ── output directory ──────────────────────────────────────────────────
        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=20, pady=(12, 0))
        tk.Label(self, text='Save to', font=('Helvetica', 11, 'bold')).pack(anchor='w', **pad)

        frm_dir = tk.Frame(self)
        frm_dir.pack(fill='x', padx=20, pady=(0, 8))
        self.out_dir = tk.StringVar(value=str(Path.home() / 'Downloads'))
        tk.Entry(frm_dir, textvariable=self.out_dir, width=32).pack(side='left', padx=(0, 8))
        ttk.Button(frm_dir, text='…', width=3, command=self._browse).pack(side='left')

        # ── scan button ───────────────────────────────────────────────────────
        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=20, pady=(8, 0))

        self.scan_btn = ttk.Button(self, text='Scan Now', command=self._start_scan)
        self.scan_btn.pack(pady=16, ipadx=20, ipady=6)

        # ── status ────────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value='Ready.')
        tk.Label(self, textvariable=self.status_var, fg='gray', font=('Helvetica', 10)).pack(pady=(0, 4))

        self.progress = ttk.Progressbar(self, length=260, mode='determinate')
        self.progress.pack(pady=(0, 20))

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)

    def _start_scan(self):
        self.scan_btn.config(state='disabled')
        self.progress['value'] = 0
        self.status_var.set('Connecting to scanner…')

        threading.Thread(target=run_scan, daemon=True, kwargs=dict(
            ip=self.PRINTER_IP,
            dpi=self.dpi.get(),
            fmt=self.fmt.get(),
            out_dir=self.out_dir.get(),
            progress_cb=self._on_progress,
            done_cb=self._on_done,
        )).start()

    def _on_progress(self, received, expected):
        if received == -1:
            self.after(0, lambda: self.status_var.set('Processing image…'))
            self.after(0, lambda: self.progress.config(mode='indeterminate'))
            self.after(0, self.progress.start)
            return
        pct = min(received / expected * 100, 99) if expected > 0 else 0
        mb_recv = received / 1_000_000
        mb_exp = expected / 1_000_000
        self.after(0, lambda: self.status_var.set(f'Receiving… {mb_recv:.1f} / {mb_exp:.1f} MB'))
        self.after(0, lambda: self.progress.config(mode='determinate', value=pct))

    def _on_done(self, path, error):
        self.after(0, self.progress.stop)
        if error:
            self.after(0, lambda: self.status_var.set(f'Error: {error}'))
            self.after(0, lambda: self.progress.config(mode='determinate', value=0))
        else:
            self.after(0, lambda: self.status_var.set(f'Saved: {Path(path).name}'))
            self.after(0, lambda: self.progress.config(mode='determinate', value=100))
            self.after(0, lambda: subprocess.Popen(['open', path]))
        self.after(0, lambda: self.scan_btn.config(state='normal'))


if __name__ == '__main__':
    app = ScannerApp()
    app.mainloop()
