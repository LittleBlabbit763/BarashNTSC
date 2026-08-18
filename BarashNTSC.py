# BarashNTSC.py
from __future__ import annotations

import concurrent.futures as cf
import ctypes
import hashlib
import json
import multiprocessing
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import tkinter as tk
from collections import deque
from dataclasses import asdict, dataclass
from io import BytesIO
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

try:
    import sounddevice as sd
    HAVE_SD = True
except Exception:
    sd = None
    HAVE_SD = False

_CACHE = {}
_WORKER_PROC = None
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

COL_BG      = "#221636"
COL_PANEL   = "#2B1D45"
COL_FIELD   = "#332352"
COL_FG      = "#EFE7FB"
COL_DIM     = "#9C90B8"
COL_ACCENT  = "#B78CFF"
COL_PINK    = "#F277C8"
COL_BTN     = "#3B2A5E"
COL_BTN_ACT = "#4C3877"


def _resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _pil_to_clipboard_windows(img):
    """Put a PIL image on the Windows clipboard as DIB."""
    if os.name != "nt":
        raise RuntimeError("clipboard copy is supported on Windows only")

    buf = BytesIO()
    img.save(buf, "BMP")
    dib = buf.getvalue()[14:]  # strip BITMAPFILEHEADER -> CF_DIB payload

    CF_DIB = 8
    GMEM_MOVEABLE = 0x0002

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Correct 64-bit pointer handling (default restype=c_int truncates pointers).
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p

    if not user32.OpenClipboard(None):
        raise RuntimeError("cannot open clipboard")

    try:
        user32.EmptyClipboard()

        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
        if not h:
            raise RuntimeError("global alloc failed")

        p = kernel32.GlobalLock(h)
        if not p:
            raise RuntimeError("global lock failed")

        ctypes.memmove(p, dib, len(dib))
        kernel32.GlobalUnlock(h)

        if not user32.SetClipboardData(CF_DIB, h):
            raise RuntimeError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def apply_purple_theme(root: tk.Tk):
    root.configure(bg=COL_BG)
    style = ttk.Style(root)
    try:
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=COL_BG, foreground=COL_FG,
                    fieldbackground=COL_FIELD, borderwidth=1, relief="flat")
    style.configure("TFrame", background=COL_BG)
    style.configure("TLabel", background=COL_BG, foreground=COL_FG)
    style.configure("TButton", background=COL_BTN, foreground=COL_FG,
                    borderwidth=1, focusthickness=2, focuscolor=COL_ACCENT)
    style.map("TButton",
              background=[("active", COL_BTN_ACT), ("pressed", COL_BTN_ACT)],
              foreground=[("disabled", COL_DIM)])
    style.configure("TCheckbutton", background=COL_BG, foreground=COL_FG)
    style.map("TCheckbutton", background=[("active", COL_BG)])
    style.configure("TEntry", fieldbackground=COL_FIELD, foreground=COL_FG,
                    insertbackground=COL_FG, borderwidth=1)
    style.configure("TScale", background=COL_ACCENT, troughcolor=COL_FIELD, borderwidth=1)
    style.configure("TProgressbar", background=COL_PINK, troughcolor=COL_FIELD, borderwidth=1)
    style.configure("Vertical.TScrollbar", background=COL_BTN, troughcolor=COL_BG,
                    borderwidth=1, arrowcolor=COL_ACCENT)
    style.configure("Horizontal.TScrollbar", background=COL_BTN, troughcolor=COL_BG,
                    arrowcolor=COL_ACCENT)


@dataclass
class VHSParams:
    detail: int = 45
    noise: int = 12
    color_bleed: int = 12
    chroma_shift: int = 0
    color_phase: int = 4
    composite: int = 18
    ghosting: int = 15
    interlace: int = 10
    tracking: int = 12
    jitter: int = 8
    tape_wear: int = 12
    flicker: int = 5
    scanlines: int = 0
    vignette: int = 6
    edge_enh: int = 25
    fade: int = 15
    saturation: int = 105
    contrast: int = 100
    brightness: int = 0


SLIDERS = [
    ("detail", "Tape detail (horizontal)", 10, 100),
    ("noise", "Video noise", 0, 100),
    ("color_bleed", "Color bleed", 0, 20),
    ("chroma_shift", "Chroma shift", 0, 20),
    ("color_phase", "Color phase instability", 0, 50),
    ("composite", "Composite artifacts (rainbow)", 0, 100),
    ("edge_enh", "Edge enhancement", 0, 100),
    ("fade", "Tape fade", 0, 100),
    ("ghosting", "Motion ghost", 0, 100),
    ("interlace", "Interlace artifacts", 0, 100),
    ("tracking", "Tracking error", 0, 100),
    ("jitter", "Line jitter", 0, 100),
    ("tape_wear", "Tape wear / dropouts", 0, 100),
    ("flicker", "Flicker", 0, 100),
    ("scanlines", "CRT scanlines", 0, 100),
    ("vignette", "Vignette", 0, 100),
    ("saturation", "Saturation", 0, 200),
    ("contrast", "Contrast", 0, 200),
    ("brightness", "Brightness", -100, 100),
]


def _get_xy(h, w):
    key = ("xy", h, w)
    if key not in _CACHE:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        _CACHE[key] = (xx, yy)
    return _CACHE[key]


def _get_carriers(h, w, phase):
    key = ("carriers", h, w, phase)
    if key not in _CACHE:
        xx, yy = _get_xy(h, w)
        xph = (xx + float(phase)) * (np.pi / 2.0)
        lp = yy * np.pi
        c1 = np.sin(xph + lp).astype(np.float32)
        c2 = np.sin(xph + lp + np.pi / 2.0).astype(np.float32)
        _CACHE[key] = (c1, c2)
    return _CACHE[key]


def _get_base_maps(h, w):
    key = ("maps", h, w)
    if key not in _CACHE:
        y, x = np.indices((h, w), dtype=np.float32)
        _CACHE[key] = (x, y)
    return _CACHE[key]


def _get_scan_pattern(h):
    key = ("scan_pattern", h)
    if key not in _CACHE:
        pattern = np.zeros((h, 1, 1), dtype=np.float32)
        pattern[1::2] = 1.0
        _CACHE[key] = pattern
    return _CACHE[key]


def _get_vignette_mask(h, w):
    key = ("vignette", h, w)
    if key not in _CACHE:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx = max(1.0, (w - 1) / 2.0)
        cy = max(1.0, (h - 1) / 2.0)
        dx = (xx - cx) / cx
        dy = (yy - cy) / cy
        d = np.sqrt(dx * dx + dy * dy)
        d = d / max(1e-5, float(d.max()))
        _CACHE[key] = (d * d)[:, :, np.newaxis]
    return _CACHE[key]


def _smooth_1d(values, k=5):
    values = np.asarray(values, dtype=np.float32)
    if values.size < 2:
        return values
    k = min(k, values.size)
    if k % 2 == 0:
        k -= 1
    if k <= 1:
        return values
    return cv2.blur(values.reshape(-1, 1), (1, k)).reshape(-1)


def _shift_x(channel, shift):
    if shift == 0:
        return channel
    _, w = channel.shape[:2]
    if w <= 1:
        return channel
    out = np.empty_like(channel)
    if shift > 0:
        s = min(int(shift), w - 1)
        if s <= 0:
            return channel
        out[:, s:] = channel[:, :w - s]
        out[:, :s] = channel[:, :1]
    else:
        s = min(int(-shift), w - 1)
        if s <= 0:
            return channel
        out[:, :w - s] = channel[:, s:]
        out[:, w - s:] = channel[:, -1:]
    return out


def _randn(shape):
    arr = np.empty(shape, dtype=np.float32)
    cv2.randn(arr, 0.0, 1.0)
    return arr


def _worker_init(threads=1):
    cv2.setNumThreads(threads)
    global _WORKER_PROC
    _WORKER_PROC = None


def _get_worker_proc():
    global _WORKER_PROC
    if _WORKER_PROC is None:
        _WORKER_PROC = VHSProcessor()
    return _WORKER_PROC


class VHSProcessor:
    def __init__(self):
        self.prev_small = None
        self.rng = np.random.default_rng()
        self._noise_bank = {}

    def _noise(self, shape):
        h, w = shape
        key = (h, w)
        if key not in self._noise_bank:
            self._noise_bank[key] = self.rng.standard_normal((h * 2, w * 2), dtype=np.float32)
        big = self._noise_bank[key]
        y0 = int(self.rng.integers(0, h + 1))
        x0 = int(self.rng.integers(0, w + 1))
        crop = big[y0:y0 + h, x0:x0 + w]
        if self.rng.random() < 0.5:
            crop = np.fliplr(crop)
        if self.rng.random() < 0.5:
            crop = np.flipud(crop)
        if self.rng.random() < 0.5:
            crop = -crop
        return crop

    def reset(self):
        self.prev_small = None

    def process(self, frame, p, frame_idx=0, ghost=True):
        if frame is None or frame.size == 0:
            return frame
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        h0, w0 = frame.shape[:2]
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb).astype(np.float32)
        y, cr, cb = cv2.split(ycrcb)

        detail = max(0.06, float(p.detail) / 100.0)
        lw = max(48, int(w0 * detail))
        if lw < w0:
            y = cv2.resize(y, (lw, h0), interpolation=cv2.INTER_LINEAR)
            y = cv2.resize(y, (w0, h0), interpolation=cv2.INTER_LINEAR)

        if p.edge_enh > 0:
            ee = float(p.edge_enh) / 100.0 * 0.7
            y = y + ee * (y - cv2.blur(y, (3, 1)))

        if p.fade > 0:
            f = float(p.fade) / 100.0
            y *= (1.0 - 0.15 * f)
            y += 255.0 * 0.09 * f
            sat_f = 1.0 - 0.25 * f
            cr -= 128.0; cr *= sat_f; cr += 128.0
            cb -= 128.0; cb *= sat_f; cb += 128.0

        bleed = float(p.color_bleed)
        factor = 2.0 + bleed * 0.6
        cw = max(16, int(w0 / factor))
        chh = max(16, int(h0 / (1.5 + bleed * 0.10)))
        cr = cv2.resize(cr, (cw, chh), interpolation=cv2.INTER_AREA)
        cb = cv2.resize(cb, (cw, chh), interpolation=cv2.INTER_AREA)

        if p.noise > 0:
            cn = float(p.noise) * 0.18
            cr = cr + self._noise(cr.shape) * cn
            cb = cb + self._noise(cb.shape) * cn

        cr = cv2.resize(cr, (w0, h0), interpolation=cv2.INTER_LINEAR)
        cb = cv2.resize(cb, (w0, h0), interpolation=cv2.INTER_LINEAR)

        if p.chroma_shift > 0:
            s = int(p.chroma_shift)
            cr = _shift_x(cr, s)
            cb = _shift_x(cb, -s)

        if p.composite > 0:
            k = float(p.composite) / 100.0 * 0.30
            c1, c2 = _get_carriers(h0, w0, frame_idx % 4)
            hf = y - cv2.blur(y, (5, 1))
            hf_k = hf * k
            cr = cr + hf_k * c1
            cb = cb + hf_k * 0.6 * c2

        if p.color_phase > 0:
            amount = float(p.color_phase) / 100.0
            angle = (0.35 * amount * float(np.sin(
                float(frame_idx) * 0.087 + 0.7 * np.sin(float(frame_idx) * 0.019))))
            if abs(angle) > 1e-4:
                ca = float(np.cos(angle))
                sa = float(np.sin(angle))
                cr_c = cr - 128.0
                cb_c = cb - 128.0
                cr = (cr_c * ca - cb_c * sa + 128.0).astype(np.float32)
                cb = (cr_c * sa + cb_c * ca + 128.0).astype(np.float32)

        if p.contrast != 100:
            y -= 128.0; y *= (float(p.contrast) / 100.0); y += 128.0
        if p.brightness != 0:
            y += float(p.brightness)
        if p.saturation != 100:
            sat = float(p.saturation) / 100.0
            cr -= 128.0; cr *= sat; cr += 128.0
            cb -= 128.0; cb *= sat; cb += 128.0

        if p.noise > 0:
            n = float(p.noise)
            nh = max(8, h0 * 3 // 4)
            nw = max(8, w0 * 3 // 4)
            ln = self._noise((nh, nw)) * (n * 0.35)
            ln = cv2.resize(ln, (w0, h0), interpolation=cv2.INTER_NEAREST)
            y = y + ln

        if p.tape_wear > 0:
            wd = float(p.tape_wear)
            base = wd / 6.0
            n_drop = int(base)
            if self.rng.random() < (base - n_drop):
                n_drop += 1
            for _ in range(n_drop):
                yyi = int(self.rng.integers(0, h0))
                length = int(self.rng.integers(1, 9))
                xxi = int(self.rng.integers(0, max(1, w0 - length)))
                amp = float(self.rng.uniform(40.0, 140.0))
                y[yyi, xxi:xxi + length] = np.clip(y[yyi, xxi:xxi + length] + amp, 0, 255)
            if self.rng.random() < wd / 60.0:
                yyi = int(self.rng.integers(0, h0))
                length = int(self.rng.integers(2, 12))
                xxi = int(self.rng.integers(0, max(1, w0 - length)))
                y[yyi, xxi:xxi + length] *= 0.4

        y = np.clip(y, 0, 255).astype(np.float32)
        cr = np.clip(cr, 0, 255).astype(np.float32)
        cb = np.clip(cb, 0, 255).astype(np.float32)
        img = cv2.cvtColor(cv2.merge([y, cr, cb]).astype(np.uint8), cv2.COLOR_YCrCb2BGR)

        band_mask = np.zeros(h0, dtype=np.float32)

        if p.jitter > 0 or p.tracking > 0 or p.interlace > 0:
            rows = np.arange(h0, dtype=np.float32)
            offsets = np.zeros(h0, dtype=np.float32)
            y_offsets = np.zeros(h0, dtype=np.float32)
            width_factor = max(0.35, float(w0) / 640.0)
            t = float(frame_idx) * 0.41

            if p.jitter > 0:
                amp = float(p.jitter) * 0.03 * width_factor
                offsets += np.sin(rows * 0.048 + t) * amp
                offsets += np.sin(rows * 0.011 - t * 0.67) * amp * 0.55
                line_noise = self.rng.uniform(-float(p.jitter) * 0.02, float(p.jitter) * 0.02, h0).astype(np.float32)
                offsets += _smooth_1d(line_noise, 5)

            if p.tracking > 0:
                band_h = max(5, int(h0 * 0.08))
                speed = 3.0 + 1.6 * np.sin(float(frame_idx) * 0.013) + 0.8 * np.sin(float(frame_idx) * 0.041)
                speed = max(0.7, abs(speed))
                period = max(1, h0 + 2 * band_h)
                center = int((float(frame_idx) * speed) % period) - band_h
                dist = np.abs(rows - float(center))
                band_mask = np.clip(1.0 - dist / float(band_h), 0.0, 1.0).astype(np.float32)
                band_mask = _smooth_1d(band_mask, 7)
                track_amp = float(p.tracking) * 0.10 * width_factor
                offsets += band_mask * (np.sin(rows * 0.17 + float(frame_idx) * 1.9) * track_amp)
                offsets += band_mask * self.rng.uniform(-track_amp, track_amp, h0).astype(np.float32)
                y_offsets += band_mask * self.rng.uniform(-float(p.tracking) * 0.02, float(p.tracking) * 0.02, h0).astype(np.float32)

            if p.interlace > 0:
                inter_strength = float(p.interlace) / 100.0
                field_dir = 1.0 if (frame_idx % 2 == 0) else -1.0
                phase = float(np.sin(float(frame_idx) * 0.73) * inter_strength * max(1.0, w0 * 0.004))
                offsets[0::2] += phase * field_dir
                offsets[1::2] -= phase * field_dir
                y_shift = 0.15 * inter_strength * field_dir
                y_offsets[0::2] += y_shift
                y_offsets[1::2] -= y_shift

            base_x, base_y = _get_base_maps(h0, w0)
            map_x = base_x + offsets[:, np.newaxis]
            map_y = base_y + y_offsets[:, np.newaxis]
            img = cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)

        if p.tracking > 0:
            add = (band_mask * (float(p.tracking) * 0.15))[:, np.newaxis, np.newaxis]
            tn = self._noise((max(8, h0 // 4), max(8, w0 // 4)))
            tn = cv2.resize(tn, (w0, h0), interpolation=cv2.INTER_NEAREST)
            tn = (tn * float(p.tracking) * 0.25)[:, :, np.newaxis]
            tn *= band_mask[:, np.newaxis, np.newaxis]
            img = np.clip(img.astype(np.float32) + add + tn, 0, 255).astype(np.uint8)

        if ghost and p.ghosting > 0:
            alpha = min(0.60, float(p.ghosting) / 100.0 * 0.40)
            if self.prev_small is not None and self.prev_small.shape == img.shape:
                img = cv2.addWeighted(img, 1.0 - alpha, self.prev_small, alpha, 0.0)
            self.prev_small = img
        elif ghost:
            self.prev_small = None

        out = img
        need_float = p.scanlines > 0 or p.vignette > 0 or p.flicker > 0
        if need_float:
            out_f = out.astype(np.float32)
            if p.scanlines > 0:
                scan_strength = min(0.85, float(p.scanlines) / 100.0 * 0.40)
                out_f *= (1.0 - scan_strength * _get_scan_pattern(h0))
            if p.vignette > 0:
                vignette_amount = min(0.80, float(p.vignette) / 100.0 * 0.70)
                out_f *= (1.0 - vignette_amount * _get_vignette_mask(h0, w0))
            if p.flicker > 0:
                flicker_amount = float(np.sin(float(frame_idx) * 0.73) * float(p.flicker) * 0.10
                                       + self.rng.uniform(-1.0, 1.0) * float(p.flicker) * 0.20)
                out_f += flicker_amount
            out = np.clip(out_f, 0, 255).astype(np.uint8)
        return out


def _export_task(task):
    start_idx, raws, fh, fw, params_dict, out_w, out_h, internal_w = task
    params = VHSParams(**params_dict)
    proc = _get_worker_proc()
    outs = []
    for i, raw in enumerate(raws):
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(fh, fw, 3)
        if internal_w < fw:
            ih = max(2, (int(internal_w * fh / fw) // 2) * 2)
            frame = cv2.resize(frame, (internal_w, ih), interpolation=cv2.INTER_AREA)
        out = proc.process(frame, params, start_idx + i, ghost=False)
        if out_w != internal_w or out_h != out.shape[0]:
            out = cv2.resize(out, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        outs.append(out.tobytes())
    return outs


class AudioPlayer:
    def __init__(self, data, samplerate):
        self.data = np.ascontiguousarray(data, dtype=np.float32)
        self.sr = int(samplerate)
        self.pos = 0
        self.playing = False
        self.stream = sd.OutputStream(samplerate=self.sr, channels=self.data.shape[1],
                                      dtype="float32", blocksize=1024, callback=self._callback)
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status):
        if not self.playing:
            outdata[:] = 0
            return
        n = self.data.shape[0]
        start = self.pos
        if start >= n:
            outdata[:] = 0
            return
        end = start + frames
        chunk = self.data[start:min(end, n)]
        outdata[:len(chunk)] = chunk
        if len(chunk) < frames:
            outdata[len(chunk):] = 0
        self.pos = min(end, n)

    def set_position(self, seconds):
        self.pos = int(max(0.0, seconds) * self.sr)

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class BarashNTSCApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BarashNTSC")
        self.root.geometry("1280x820")

        self.default_params = VHSParams()
        self.vars = {}
        self.value_labels = {}
        self.scales = {}
        self.input_path = None
        self.cap = None
        self.audio = None
        self.is_image = False
        self.image_frame = None
        self.current_params = VHSParams()

        self.fps = 25.0
        self.total = 0
        self.duration = 0.0
        self.pos = 0.0
        self.playing = False
        self.play_wall = 0.0
        self.play_pos = 0.0

        self.preview_active = False
        self.preview_thread = None
        self.latest_frame = None
        self.frame_version = 0
        self.shown_version = -1
        self.worker_seek = False
        self.need_refresh = False

        self.audio_loading = False
        self.audio_state = "disabled"

        cores = os.cpu_count() or 2
        self.preview_max_w = 480 if cores <= 2 else 768

        self.disp_w = 600
        self.disp_h = 450

        self.updating_seek = False
        self._photo = None
        self._img_id = None
        self.running = True
        self.exporting = False

        self.audio_enabled_var = tk.BooleanVar(value=True)
        self.fps_var = tk.StringVar(value="0")
        self.scale_var = tk.StringVar(value="100")
        self.internal_var = tk.StringVar(value="0")
        self.status_var = tk.StringVar(value="Open a video or image file.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.seek_var = tk.DoubleVar(value=0.0)
        self.ui_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()
        self.root.after(33, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        toolbar = ttk.Frame(main)
        toolbar.pack(fill="x", pady=(0, 6))
        self.btn_open = ttk.Button(toolbar, text="Open Video / Image", command=self.open_file)
        self.btn_open.pack(side="left", padx=(0, 6))
        self.btn_export = ttk.Button(toolbar, text="Export", command=self.export, state="disabled")
        self.btn_export.pack(side="left", padx=(0, 12))
        ttk.Checkbutton(toolbar, text="Audio (requires FFmpeg)",
                        variable=self.audio_enabled_var, command=self._on_audio_toggle).pack(side="left")

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)

        options = ttk.Frame(left)
        options.pack(fill="x", pady=(0, 6))
        ttk.Label(options, text="Export FPS (0 = source):").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.fps_var, width=8).grid(row=0, column=1, padx=(4, 12))
        ttk.Label(options, text="Export scale, %:").grid(row=0, column=2, sticky="w")
        ttk.Entry(options, textvariable=self.scale_var, width=8).grid(row=0, column=3, padx=(4, 12))
        ttk.Label(options, text="Internal width (0 = auto):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(options, textvariable=self.internal_var, width=8).grid(row=1, column=1, padx=(4, 12), pady=(4, 0))
        presets = ttk.Frame(left)
        presets.pack(fill="x", pady=(0, 6))
        ttk.Button(presets, text="Import preset (.json)", command=self._import_preset).pack(side="left", padx=(0, 6))
        ttk.Button(presets, text="Export preset (.json)", command=self._export_preset).pack(side="left")

        area = ttk.Frame(left)
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, highlightthickness=0, bg=COL_PANEL)
        scroll = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        self.slider_frame = ttk.Frame(canvas)
        self.slider_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.slider_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for key, label, mn, mx in SLIDERS:
            row = ttk.Frame(self.slider_frame)
            row.pack(fill="x", pady=2, padx=4)
            var = tk.IntVar(value=getattr(self.default_params, key))
            self.vars[key] = var
            ttk.Label(row, text=label, width=28, anchor="w").grid(row=0, column=0, sticky="w")
            value_label = ttk.Label(row, text=str(var.get()), width=5, anchor="e")
            value_label.grid(row=0, column=2, sticky="e")
            self.value_labels[key] = value_label
            scale = ttk.Scale(row, from_=mn, to=mx, orient="horizontal",
                              command=lambda val, v=var, lbl=value_label: self._on_scale(v, lbl, val))
            scale.set(getattr(self.default_params, key))
            self.scales[key] = scale
            scale.grid(row=0, column=1, sticky="ew", padx=6)
            row.columnconfigure(1, weight=1)

        right = ttk.Frame(body)
        right.pack(side="right", fill="y", padx=(8, 0))

        self.canvas_video = tk.Canvas(right, width=self.disp_w, height=self.disp_h,
                                      bg="black", highlightthickness=0)
        self.canvas_video.pack(fill="x", pady=(0, 6))

        # Right-click frame menu (copy / save current processed frame).
        self.frame_menu = tk.Menu(self.root, tearoff=0)
        self.frame_menu.add_command(label="Copy frame", command=self._copy_frame)
        self.frame_menu.add_command(label="Save frame as...", command=self._save_frame)
        self.canvas_video.bind("<Button-3>", self._show_frame_menu)

        t1 = ttk.Frame(right)
        t1.pack(fill="x", pady=(0, 4))
        self.btn_play = ttk.Button(t1, text="Play", command=self.toggle_play, state="disabled", width=8)
        self.btn_play.pack(side="left", padx=(0, 6))
        self.btn_stop = ttk.Button(t1, text="Stop", command=self.stop_playback, state="disabled", width=8)
        self.btn_stop.pack(side="left", padx=(0, 10))
        self.time_label = ttk.Label(t1, text="00:00:00 / 00:00:00", width=20)
        self.time_label.pack(side="left")

        t2 = ttk.Frame(right)
        t2.pack(fill="x")
        self.seek_scale = ttk.Scale(t2, from_=0, to=1, orient="horizontal",
                                    variable=self.seek_var, command=self._on_seek)
        self.seek_scale.pack(fill="x", expand=True)

        status = ttk.Frame(main)
        status.pack(fill="x", pady=(6, 0))
        self.progress = ttk.Progressbar(status, variable=self.progress_var, maximum=100.0, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w")

    # -------------------------------------------------- frame menu --------

    def _show_frame_menu(self, event):
        if self.input_path is None:
            return
        try:
            self.frame_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.frame_menu.grab_release()

    def _current_full_frame(self):
        """(source frame at current position, idx) at FULL source resolution."""
        if self.is_image:
            return self.image_frame, 0

        if self.cap is None:
            return None, 0

        idx = int(self.pos * self.fps)
        if self.total > 0:
            idx = min(idx, self.total - 1)

        # Separate capture so we don't fight the preview worker over self.cap.
        cap2 = cv2.VideoCapture(self.input_path)
        cap2.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap2.read()
        cap2.release()

        return (frame if ok else None), idx

    def _grab_full_frame(self):
        frame, idx = self._current_full_frame()
        if frame is None:
            return None
        return VHSProcessor().process(frame, self.get_params(), idx)

    def _copy_frame(self):
        if self.input_path is None:
            return

        self.status_var.set("Copying frame...")

        def worker():
            try:
                full = self._grab_full_frame()
                if full is None:
                    raise RuntimeError("no frame")

                pil = Image.fromarray(cv2.cvtColor(full, cv2.COLOR_BGR2RGB))
                self.root.after(0, lambda: self._put_to_clipboard(pil))
            except Exception as e:
                traceback.print_exc()
                self.root.after(0, lambda: self.status_var.set(f"Copy failed: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _put_to_clipboard(self, pil):
        try:
            _pil_to_clipboard_windows(pil)
            self.status_var.set("Frame copied to clipboard.")
        except Exception as e:
            self.status_var.set(f"Copy failed: {e}")

    def _save_frame(self):
        if self.input_path is None:
            return

        path = filedialog.asksaveasfilename(
            title="Save Frame",
            defaultextension=".png",
            initialfile="BarashNTSC_frame.png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")],
        )

        if not path:
            return

        self.status_var.set("Saving frame...")

        def worker():
            try:
                full = self._grab_full_frame()
                if full is None:
                    raise RuntimeError("no frame")

                if not self._imwrite_unicode(path, full):
                    raise RuntimeError("write failed")

                self.ui_queue.put(("status", f"Frame saved: {path}"))
            except Exception as e:
                traceback.print_exc()
                self.ui_queue.put(("status", f"Save failed: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------ helpers -------

    @staticmethod
    def _imread_unicode(path):
        try:
            data = np.fromfile(path, dtype=np.uint8)
            if data.size == 0:
                return None
            return cv2.imdecode(data, cv2.IMREAD_COLOR)
        except Exception:
            return None

    @staticmethod
    def _imwrite_unicode(path, img):
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".bmp"):
            ext = ".png"
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        buf.tofile(path)
        return True

    # ---------------------------------------------------- presets ---------

    def _import_preset(self):
        path = filedialog.askopenfilename(
            title="Import VHS Preset",
            filetypes=[("JSON preset", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            params = data.get("params", data)  # accept wrapped or raw dict
            if not isinstance(params, dict):
                raise RuntimeError("bad preset file")

            applied = 0
            for key, mn, mx in ((s[0], s[2], s[3]) for s in SLIDERS):
                if key not in params:
                    continue
                try:
                    val = int(round(float(params[key])))
                except Exception:
                    continue
                val = max(mn, min(mx, val))
                self.vars[key].set(val)
                self.value_labels[key].config(text=str(val))
                if key in self.scales:
                    self.scales[key].set(val)
                applied += 1

            if applied == 0:
                raise RuntimeError("no known parameters in preset")

            self.current_params = self.get_params()
            self.need_refresh = True

            name = data.get("name", os.path.basename(path))
            self.status_var.set(f"Preset loaded: {name} ({applied} params)")
        except Exception as e:
            messagebox.showerror("BarashNTSC", f"Failed to import preset: {e}")

    def _export_preset(self):
        path = filedialog.asksaveasfilename(
            title="Export VHS Preset",
            defaultextension=".json",
            initialfile="BarashNTSC_preset.json",
            filetypes=[("JSON preset", "*.json")],
        )
        if not path:
            return

        try:
            name = os.path.splitext(os.path.basename(path))[0]
            data = {
                "app": "BarashNTSC",
                "type": "vhs-preset",
                "version": 1,
                "name": name,
                "params": {k: v.get() for k, v in self.vars.items()},
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status_var.set(f"Preset saved: {path}")
        except Exception as e:
            messagebox.showerror("BarashNTSC", f"Failed to export preset: {e}")
            
    def _on_scale(self, var, label, value):
        try:
            val = int(round(float(value)))
        except Exception:
            val = var.get()
        var.set(val)
        label.config(text=str(val))
        self.current_params = self.get_params()
        self.need_refresh = True

    def get_params(self):
        return VHSParams(**{k: v.get() for k, v in self.vars.items()})

    def _parse_float(self, text, default):
        try:
            value = float(str(text).strip().replace(",", "."))
            if not np.isfinite(value):
                return default
            return value
        except Exception:
            return default

    @staticmethod
    def _fmt_time(t):
        t = int(max(0.0, t))
        m, s = divmod(t, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # --------------------------------------------------------- file -------

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Select Video or Image File",
            filetypes=[
                ("Video", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v *.ts *.wmv"),
                ("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        self._pause()
        self._stop_preview_worker()

        if self.cap is not None:
            self.cap.release()
            self.cap = None

        self._close_audio()

        ext = os.path.splitext(path)[1].lower()

        if ext in IMAGE_EXTS:
            img = self._imread_unicode(path)
            if img is None:
                messagebox.showerror("BarashNTSC", "Failed to open the image file.")
                return

            self.input_path = path
            self.is_image = True
            self.image_frame = img
            self.fps = 25.0
            self.total = 0
            self.duration = 0.0

            h, w = img.shape[:2]
            max_w, max_h = 600, 520
            s = min(max_w / max(1, w), max_h / max(1, h), 1.0)
            self.disp_w = max(2, int(w * s) // 2 * 2)
            self.disp_h = max(2, int(h * s) // 2 * 2)
            self.canvas_video.config(width=self.disp_w, height=self.disp_h)

            if self._img_id is None:
                self._img_id = self.canvas_video.create_image(0, 0, anchor="nw")

            self.pos = 0.0
            self.latest_frame = None
            self.frame_version = 0
            self.shown_version = -1
            self.worker_seek = True
            self.need_refresh = True
            self.current_params = self.get_params()

            self.updating_seek = True
            self.seek_scale.config(to=1.0)
            self.seek_var.set(0.0)
            self.updating_seek = False

            self.btn_play.config(state="disabled")
            self.btn_stop.config(state="disabled")
            self.btn_export.config(state="normal")
            self._set_audio_state("disabled")
            self.status_var.set(f"Loaded image: {os.path.basename(path)} | {w}x{h}")
            self._start_preview_worker()
            return

        self.is_image = False
        self.image_frame = None
        self.cap = cv2.VideoCapture(path)

        if not self.cap.isOpened():
            messagebox.showerror("BarashNTSC", "Failed to open the video file.")
            self.cap = None
            return

        self.input_path = path
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        if not self.fps or self.fps <= 0:
            self.fps = 25.0
        self.total = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total / self.fps if self.total > 0 else 0.0

        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        max_w, max_h = 600, 520
        s = min(max_w / max(1, w), max_h / max(1, h), 1.0)
        self.disp_w = max(2, int(w * s) // 2 * 2)
        self.disp_h = max(2, int(h * s) // 2 * 2)
        self.canvas_video.config(width=self.disp_w, height=self.disp_h)

        if self._img_id is None:
            self._img_id = self.canvas_video.create_image(0, 0, anchor="nw")

        self.pos = 0.0
        self.latest_frame = None
        self.frame_version = 0
        self.shown_version = -1
        self.worker_seek = True
        self.need_refresh = True
        self.current_params = self.get_params()

        self.updating_seek = True
        self.seek_scale.config(to=max(1.0, self.duration))
        self.seek_var.set(0.0)
        self.updating_seek = False

        self.btn_export.config(state="normal")
        self.status_var.set(f"Loaded: {os.path.basename(path)} | FPS: {self.fps:.2f} | Frames: {self.total}")

        self._load_audio(path)
        self._start_preview_worker()

    def _set_audio_state(self, state):
        self.audio_state = state
        has_input = self.cap is not None
        ready = state in ("ready", "error", "disabled")
        playable = has_input and ready and not self.is_image
        self.btn_play.config(state="normal" if playable else "disabled")
        self.btn_stop.config(state="normal" if playable else "disabled")

    def _stop_preview_worker(self):
        self.preview_active = False
        if self.preview_thread is not None:
            self.preview_thread.join(timeout=1.5)
            self.preview_thread = None

    def _start_preview_worker(self):
        self.preview_active = True
        self.preview_thread = threading.Thread(target=self._preview_worker, daemon=True)
        self.preview_thread.start()

    # ---------------------------------------------------- playback --------

    def toggle_play(self):
        if self.cap is None or self.is_image:
            return
        if self.playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if self.cap is None:
            return
        if self.duration > 0 and self.pos >= self.duration - 0.05:
            self._seek(0.0)
        self.playing = True
        self.play_wall = time.perf_counter()
        self.play_pos = self.pos
        if self.audio is not None:
            self.audio.set_position(self.pos)
            self.audio.playing = True
        self.btn_play.config(text="Pause")

    def _pause(self):
        if self.playing:
            self.play_pos = self.pos
        self.playing = False
        if self.audio is not None:
            self.audio.playing = False
        self.btn_play.config(text="Play")

    def stop_playback(self):
        self._pause()
        self._seek(0.0)

    def _seek(self, seconds):
        if self.cap is None:
            return
        seconds = max(0.0, min(seconds, max(0.0, self.duration - 0.001)))
        self.pos = seconds
        self.play_pos = seconds
        self.play_wall = time.perf_counter()
        if self.audio is not None:
            self.audio.set_position(seconds)
        self.worker_seek = True
        self.need_refresh = True

    def _on_seek(self, *args):
        if self.updating_seek or self.cap is None:
            return
        self._seek(float(self.seek_var.get()))

    # ------------------------------------------- preview worker -----------

    def _preview_worker(self):
        proc = VHSProcessor()
        cap_idx = -1
        pw = min(self.disp_w, self.preview_max_w)
        ph = max(2, (int(pw * self.disp_h / max(1, self.disp_w)) // 2) * 2)

        def read_at(idx):
            nonlocal cap_idx
            cap = self.cap
            if cap is None:
                return None
            if idx == cap_idx + 1:
                ok, frame = cap.read()
                if ok:
                    cap_idx = idx
                    return frame
                return None
            if cap_idx >= 0 and cap_idx < idx <= cap_idx + 12:
                frame = None
                while cap_idx < idx:
                    ok, frame = cap.read()
                    if not ok:
                        return None
                    cap_idx += 1
                return frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok:
                cap_idx = idx
                return frame
            return None

        def produce(frame, idx):
            if frame.shape[1] != pw or frame.shape[0] != ph:
                interp = cv2.INTER_AREA if frame.shape[1] > pw else cv2.INTER_LINEAR
                frame = cv2.resize(frame, (pw, ph), interpolation=interp)
            processed = proc.process(frame, self.current_params, idx)
            if pw != self.disp_w or ph != self.disp_h:
                processed = cv2.resize(processed, (self.disp_w, self.disp_h), interpolation=cv2.INTER_LINEAR)
            self.latest_frame = processed
            self.frame_version += 1

        while self.running and self.preview_active and (self.cap is not None or self.is_image):
            try:
                if self.is_image:
                    if self.need_refresh and self.image_frame is not None:
                        self.need_refresh = False
                        produce(self.image_frame, 0)
                    time.sleep(0.02)
                    continue

                if self.worker_seek:
                    self.worker_seek = False
                    cap_idx = -1

                if self.playing:
                    pos = self.play_pos + (time.perf_counter() - self.play_wall)
                    if self.duration > 0 and pos >= self.duration:
                        self.play_pos = 0.0
                        self.play_wall = time.perf_counter()
                        pos = 0.0
                        cap_idx = -1
                        try:
                            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        except Exception:
                            pass
                    idx = int(pos * self.fps)
                    if self.total > 0:
                        idx = min(idx, self.total - 1)
                    frame = read_at(idx)
                    if frame is not None:
                        produce(frame, idx)
                    time.sleep(0.25 if self.audio_loading else 0.002)
                elif self.need_refresh:
                    self.need_refresh = False
                    idx = int(self.pos * self.fps)
                    if self.total > 0:
                        idx = min(idx, self.total - 1)
                    frame = read_at(idx)
                    if frame is not None:
                        produce(frame, idx)
                    time.sleep(0.1 if self.audio_loading else 0.01)
                else:
                    time.sleep(0.02)
            except Exception:
                traceback.print_exc()
                time.sleep(0.1)

    # ----------------------------------------------------- ui tick --------

    def _tick(self):
        if not self.running:
            return
        try:
            if self.cap is not None or self.is_image:
                if self.playing and self.cap is not None:
                    self.pos = self.play_pos + (time.perf_counter() - self.play_wall)
                    if self.duration > 0 and self.pos >= self.duration:
                        self.pos = 0.0

                if self.latest_frame is not None and self.frame_version != self.shown_version:
                    rgb = cv2.cvtColor(self.latest_frame, cv2.COLOR_BGR2RGB)
                    self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
                    self.canvas_video.itemconfig(self._img_id, image=self._photo)
                    self.shown_version = self.frame_version

                self.updating_seek = True
                self.seek_var.set(self.pos)
                self.updating_seek = False

                self.time_label.config(text=f"{self._fmt_time(self.pos)} / {self._fmt_time(self.duration)}")

                if self.audio is not None and self.playing:
                    apos = self.audio.pos / self.audio.sr
                    if abs(apos - self.pos) > 0.25:
                        self.audio.set_position(self.pos)
        except Exception as exc:
            traceback.print_exc()
            self.status_var.set(f"Playback error: {exc}")
            self.playing = False
            if self.audio is not None:
                self.audio.playing = False

        if self.running:
            self.root.after(33, self._tick)

    # ------------------------------------------------------- audio --------

    def _on_audio_toggle(self):
        if self.cap is None or self.is_image:
            return
        self._close_audio()
        if self.audio_enabled_var.get() and self.input_path:
            self._load_audio(self.input_path)
        else:
            self._set_audio_state("disabled")
            self.status_var.set("Audio disabled.")

    def _close_audio(self):
        if self.audio is not None:
            self.audio.close()
            self.audio = None

    @staticmethod
    def _audio_cache_path(path):
        try:
            st = os.stat(path)
            key = hashlib.md5(f"{path}|{st.st_mtime}|{st.st_size}".encode("utf-8", "replace")).hexdigest()
            return os.path.join(tempfile.gettempdir(), f"barash_audio_{key}.raw")
        except Exception:
            return None

    def _load_audio(self, path):
        if not self.audio_enabled_var.get():
            self._set_audio_state("disabled")
            return
        if shutil.which("ffmpeg") is None:
            self._set_audio_state("disabled")
            self.status_var.set("FFmpeg not found: audio disabled.")
            return
        if not HAVE_SD:
            self._set_audio_state("disabled")
            self.status_var.set("sounddevice not installed: audio disabled.")
            return

        self.audio_loading = True
        self._set_audio_state("loading")
        self.status_var.set("Loading audio...")

        def worker():
            try:
                data = None
                cpath = self._audio_cache_path(path)
                if cpath is not None and os.path.exists(cpath):
                    try:
                        data = np.fromfile(cpath, dtype=np.float32).reshape(-1, 1)
                    except Exception:
                        data = None

                if data is None or data.size == 0:
                    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                           "-vn", "-ac", "1", "-ar", "22050",
                           "-f", "f32le", "-acodec", "pcm_f32le", "-"]
                    sub_kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL}
                    if os.name == "nt":
                        si = subprocess.STARTUPINFO()
                        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        sub_kwargs["startupinfo"] = si
                    result = subprocess.run(cmd, **sub_kwargs)
                    data = np.frombuffer(result.stdout, dtype=np.float32).reshape(-1, 1)
                    if data.size == 0:
                        raise RuntimeError("empty audio")
                    if cpath is not None:
                        try:
                            data.tofile(cpath)
                        except Exception:
                            pass

                player = AudioPlayer(data, 22050)
                if self.running:
                    self.root.after(0, lambda: self._audio_ready(player))
            except Exception:
                if self.running:
                    self.root.after(0, lambda: (self._set_audio_state("error"),
                                                 self.status_var.set("Could not decode audio.")))
            finally:
                self.audio_loading = False

        threading.Thread(target=worker, daemon=True).start()

    def _audio_ready(self, player):
        self._close_audio()
        self.audio_loading = False
        self.audio = player
        self.audio.set_position(self.pos)
        self.audio.playing = self.playing
        self._set_audio_state("ready")
        self.status_var.set("Audio loaded.")

    # ------------------------------------------------------ export --------

    def export(self):
        if self.input_path is None or self.exporting:
            return
        if self.is_image:
            self._export_image_dialog()
            return

        self._pause()

        output_path = filedialog.asksaveasfilename(
            title="Save Processed Video",
            defaultextension=".mp4",
            initialfile="BarashNTSC_output.mp4",
            filetypes=[("MP4", "*.mp4"), ("MOV", "*.mov"), ("AVI", "*.avi")],
        )
        if not output_path:
            return

        fps = self._parse_float(self.fps_var.get(), 0.0)
        scale = self._parse_float(self.scale_var.get(), 100.0)
        internal = self._parse_float(self.internal_var.get(), 0.0)
        keep_audio = bool(self.audio_enabled_var.get())

        options = {"input": self.input_path, "output": output_path, "params": self.get_params(),
                   "fps": fps, "scale": scale, "internal": internal, "keep_audio": keep_audio}

        self.exporting = True
        self.btn_open.config(state="disabled")
        self.btn_play.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.btn_export.config(state="disabled")
        self.progress_var.set(0.0)
        self.status_var.set("Preparing export...")

        threading.Thread(target=self._export_worker, args=(options,), daemon=True).start()

    def _export_image_dialog(self):
        output_path = filedialog.asksaveasfilename(
            title="Save Processed Image",
            defaultextension=".png",
            initialfile="BarashNTSC_output.png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")],
        )
        if not output_path:
            return

        self.exporting = True
        self.btn_open.config(state="disabled")
        self.btn_export.config(state="disabled")
        self.progress_var.set(0.0)
        self.status_var.set("Processing image...")

        threading.Thread(target=self._export_image_worker, args=(output_path,), daemon=True).start()

    def _export_image_worker(self, output_path):
        try:
            params = self.get_params()
            proc = VHSProcessor()
            processed = proc.process(self.image_frame, params, 0)
            if not self._imwrite_unicode(output_path, processed):
                raise RuntimeError("Failed to write the image file.")
            self.ui_queue.put(("done", f"Image saved: {output_path}", 100.0))
        except Exception as exc:
            traceback.print_exc()
            self.ui_queue.put(("error", str(exc), 0.0))

    @staticmethod
    def _auto_profile():
        cores = os.cpu_count() or 2
        if cores <= 2:
            return 1, 360, "veryfast"
        if cores <= 4:
            return 2, 480, "veryfast"
        return min(6, cores - 1), 640, "veryfast"

    def _export_worker(self, options):
        input_path = options["input"]
        output_path = options["output"]
        params = options["params"]
        fps = options["fps"]
        scale_percent = options["scale"]
        internal_req = options["internal"]
        keep_audio_request = options["keep_audio"]

        temp_video_path = None
        ff_proc = None

        try:
            cap = cv2.VideoCapture(input_path)
            if not cap.isOpened():
                raise RuntimeError("Failed to open the source video file.")

            fps_src = cap.get(cv2.CAP_PROP_FPS)
            if not fps_src or fps_src <= 0 or not np.isfinite(fps_src):
                fps_src = 25.0
            if not np.isfinite(fps) or fps <= 0:
                fps = float(fps_src)
            if not np.isfinite(scale_percent):
                scale_percent = 100.0
            scale_percent = max(1.0, min(400.0, scale_percent))

            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if w <= 0 or h <= 0:
                raise RuntimeError("Failed to read source video dimensions.")

            scale = scale_percent / 100.0
            out_w = max(2, int(w * scale) // 2 * 2)
            out_h = max(2, int(h * scale) // 2 * 2)

            auto_workers, auto_internal, preset = self._auto_profile()
            if not np.isfinite(internal_req) or internal_req <= 0:
                internal_w = min(auto_internal, out_w)
            else:
                internal_w = max(128, min(int(internal_req), out_w))
            workers = 1 if options.get("force_single") else auto_workers
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            self.ui_queue.put(("status", f"Export profile: {workers} worker(s), internal {internal_w}, preset {preset}"))

            keep_audio = bool(keep_audio_request)
            if keep_audio:
                if shutil.which("ffmpeg") is None:
                    self.ui_queue.put(("status", "FFmpeg not found: export will be silent."))
                    keep_audio = False
                elif not output_path.lower().endswith((".mp4", ".mov")):
                    self.ui_queue.put(("status", "Audio can be kept only for MP4/MOV: export will be silent."))
                    keep_audio = False

            if keep_audio:
                fd, temp_video_path = tempfile.mkstemp(suffix=".mp4", prefix="barash_ntsc_")
                os.close(fd)
                video_target = temp_video_path
            else:
                video_target = output_path

            params_dict = asdict(params)
            use_ffmpeg_enc = shutil.which("ffmpeg") is not None
            writer = None

            if use_ffmpeg_enc:
                cmd = ["ffmpeg", "-y", "-loglevel", "error",
                       "-f", "rawvideo", "-pix_fmt", "bgr24",
                       "-s", f"{out_w}x{out_h}", "-r", f"{fps:.3f}", "-i", "-",
                       "-c:v", "libx264", "-preset", preset, "-crf", "25",
                       "-pix_fmt", "yuv420p", video_target]
                sub_kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE}
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    sub_kwargs["startupinfo"] = si
                ff_proc = subprocess.Popen(cmd, **sub_kwargs)
            else:
                writer = self._create_writer(video_target, fps, (out_w, out_h))

            def write_bytes(b):
                if ff_proc is not None:
                    ff_proc.stdin.write(b)
                else:
                    arr = np.frombuffer(b, dtype=np.uint8).reshape(out_h, out_w, 3)
                    writer.write(arr)

            ghost_alpha = min(0.60, float(params.ghosting) / 100.0 * 0.40) if params.ghosting > 0 else 0.0
            prev_frame = None
            processed_count = 0

            def handle(b):
                nonlocal prev_frame, processed_count
                frame = np.frombuffer(b, dtype=np.uint8).reshape(out_h, out_w, 3)
                if ghost_alpha > 0:
                    if prev_frame is not None:
                        frame = cv2.addWeighted(frame, 1.0 - ghost_alpha, prev_frame, ghost_alpha, 0.0)
                    prev_frame = frame.copy()
                write_bytes(np.ascontiguousarray(frame).tobytes())
                processed_count += 1

            CHUNK = 16

            def task_iter():
                chunk = []
                chunk_start = 0
                idx = 0
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if frame.shape[1] != out_w or frame.shape[0] != out_h:
                        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                        frame = cv2.resize(frame, (out_w, out_h), interpolation=interp)
                    if not chunk:
                        chunk_start = idx
                    chunk.append(np.ascontiguousarray(frame).tobytes())
                    idx += 1
                    if len(chunk) >= CHUNK:
                        yield (chunk_start, chunk, out_h, out_w, params_dict, out_w, out_h, internal_w)
                        chunk = []
                if chunk:
                    yield (chunk_start, chunk, out_h, out_w, params_dict, out_w, out_h, internal_w)

            if workers == 1:
                _worker_init(os.cpu_count() or 2)
                for task in task_iter():
                    for b in _export_task(task):
                        handle(b)
                    if total > 0:
                        progress = min(100.0, processed_count / max(1, total) * 100.0)
                        self.ui_queue.put(("progress", progress, f"Rendering: {progress:.1f}% ({processed_count}/{total} frames)"))
            else:
                with cf.ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(1,)) as ex:
                    pending = deque()
                    for task in task_iter():
                        pending.append(ex.submit(_export_task, task))
                        if len(pending) >= workers * 2:
                            for b in pending.popleft().result():
                                handle(b)
                            if total > 0:
                                progress = min(100.0, processed_count / max(1, total) * 100.0)
                                self.ui_queue.put(("progress", progress, f"Processing frame {processed_count}/{total}"))
                    while pending:
                        for b in pending.popleft().result():
                            handle(b)
                        if total > 0:
                            progress = min(100.0, processed_count / max(1, total) * 100.0)
                            self.ui_queue.put(("progress", progress, f"Processing frame {processed_count}/{total}"))

            cap.release()

            if ff_proc is not None:
                ff_proc.stdin.close()
                ff_proc.wait()
                if ff_proc.returncode != 0:
                    raise RuntimeError("FFmpeg encoder failed.")
            else:
                writer.release()

            if processed_count == 0:
                raise RuntimeError("The source video does not contain readable frames.")

            if keep_audio and temp_video_path is not None:
                self.ui_queue.put(("status", "Adding audio with FFmpeg..."))
                cmd = ["ffmpeg", "-y", "-loglevel", "error",
                       "-i", temp_video_path, "-i", input_path,
                       "-map", "0:v:0", "-map", "1:a:0?",
                       "-c:v", "copy", "-c:a", "aac", "-shortest", output_path]
                sub_kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                              "encoding": "utf-8", "errors": "ignore"}
                if os.name == "nt":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    sub_kwargs["startupinfo"] = si
                result = subprocess.run(cmd, **sub_kwargs)
                if result.returncode != 0:
                    shutil.copyfile(temp_video_path, output_path)
                    self.ui_queue.put(("status", "FFmpeg: audio was not added. File saved without audio."))
                else:
                    self.ui_queue.put(("status", "Audio added successfully."))
                try:
                    os.remove(temp_video_path)
                    temp_video_path = None
                except Exception:
                    pass

            self.ui_queue.put(("done", f"Export finished: {output_path}", 100.0))

        except Exception as exc:
            traceback.print_exc()
            if ff_proc is not None:
                try:
                    ff_proc.stdin.close()
                except Exception:
                    pass
                try:
                    ff_proc.terminate()
                except Exception:
                    pass
            if temp_video_path is not None and os.path.exists(temp_video_path):
                try:
                    os.remove(temp_video_path)
                except Exception:
                    pass
            if ("process pool" in str(exc).lower() or "brokenprocesspool" in type(exc).__name__.lower()) and not options.get("force_single"):
                opts2 = dict(options)
                opts2["force_single"] = True
                self.ui_queue.put(("status", "Worker pool failed; retrying single-threaded..."))
                threading.Thread(target=self._export_worker, args=(opts2,), daemon=True).start()
                return
            self.ui_queue.put(("error", str(exc), 0.0))

    def _create_writer(self, path, fps, size):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".avi":
            codecs = ["MJPG", "XVID"]
        elif ext == ".mov":
            codecs = ["mp4v", "avc1"]
        else:
            codecs = ["mp4v", "avc1"]
        for codec in codecs:
            try:
                writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), float(fps), size)
                if writer.isOpened():
                    return writer
                writer.release()
            except Exception:
                pass
        raise RuntimeError("Failed to create VideoWriter. Try another format or install codecs/FFmpeg.")

    # --------------------------------------------------- polling ----------

    def _poll_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self.progress_var.set(item[1])
                    self.status_var.set(item[2])
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "done":
                    message = item[1]
                    self.progress_var.set(100.0)
                    self.status_var.set(message)
                    self._export_finished(True, message)
                elif kind == "error":
                    message = item[1]
                    self.progress_var.set(0.0)
                    self.status_var.set("Export error.")
                    self._export_finished(False, message)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _export_finished(self, success, message):
        self.exporting = False
        has_input = self.input_path is not None
        self.btn_open.config(state="normal")
        self.btn_export.config(state="normal" if has_input else "disabled")
        ready = self.audio_state in ("ready", "error", "disabled")
        playable = has_input and ready and not self.is_image
        self.btn_play.config(state="normal" if playable else "disabled")
        self.btn_stop.config(state="normal" if playable else "disabled")
        if success:
            messagebox.showinfo("BarashNTSC", message)
        else:
            messagebox.showerror("BarashNTSC", message)

    def on_close(self):
        self.running = False
        self._pause()
        self._stop_preview_worker()
        self._close_audio()
        if self.cap is not None:
            self.cap.release()
        self.root.destroy()


def main():
    # Proper Windows taskbar identity (name + icon instead of "tk"/"Python").
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BarashNTSC.Purple")
    except Exception:
        pass

    root = tk.Tk()
    apply_purple_theme(root)

    try:
        ico = _resource_path("icon.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
            root.iconbitmap(default=ico)  # inherited by dialogs too
    except Exception:
        pass

    BarashNTSCApp(root)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
