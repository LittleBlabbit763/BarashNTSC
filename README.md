# BarashNTSC 📼
<img width="1380" height="720" alt="Без названия742_20260817160128" src="https://github.com/user-attachments/assets/19a49937-2b6f-4b04-9629-8d70de72b542" />
**BarashNTSC** is a real-time VHS / NTSC-style video effect processor with a cozy purple GUI.
It emulates the look of an old analog VCR tape: low tape resolution, color bleed, composite
"rainbow" artifacts, tracking errors, tape wear, motion ghosting, flicker and more — on both
**videos and still images**, with live preview, audio playback and multi-threaded export.

Built with Python, OpenCV, NumPy and Tkinter.

---

## ✨ Features

- 🎞️ **VHS emulation pipeline** — luma/chroma bandwidth limiting, composite cross-color,
  edge enhancement, tape fade, dropouts, tracking bands, interlace artifacts, jitter,
  motion ghosting, flicker, scanlines, vignette.
- 🖼️ **Videos *and* images** — open `.mp4 / .mov / .avi / .mkv / .webm / ...` or
  `.png / .jpg / .jpeg / .bmp / .webp / .tif / .tiff`.
- ▶️ **Live preview** with Play / Pause / Stop and a seek timeline; sliders update the
  picture in real time.
- 🔊 **Audio playback** in the preview (FFmpeg + sounddevice) with instant start and
  a disk cache for repeated opens.
- 📤 **Export** processed video (MP4 / MOV / AVI, H.264 via FFmpeg) with the original
  audio track, or save a processed image (PNG / JPG / BMP).
- ⚡ **Auto performance profiles** — the exporter picks worker count, internal resolution
  and encoder preset based on your CPU, so it works on weak laptops and strong PCs alike.
-  **Purple theme** matched to the app icon.

---

## 📋 Requirements

- **Windows** (primary target), Python **3.9+**
- Python packages:

  ```bash
  pip install opencv-python numpy Pillow sounddevice
  ```

  or simply:

  ```bash
  pip install -r requirements.txt
  ```

  with `requirements.txt`:

  ```
  opencv-python
  numpy
  Pillow
  sounddevice
  ```

- **FFmpeg (optional but recommended)** — needed for preview audio and for keeping the
  audio track on export. Get a static build from <https://www.gyan.dev/ffmpeg/builds/>
  and add `ffmpeg.exe` to your `PATH` (or place it next to the script).

---

## 🚀 Running from source

1. Download / clone this repository.
2. Install the dependencies (see above).
3. Run:

   ```bash
   python BarashNTSC_purple.py
   ```

4. Press **Open Video / Image**, tweak the sliders, press **Play**, and use **Export**
   to save the result.

> Tip: the **Internal width (0 = auto)** field controls the export's internal processing
> resolution. `0` lets the app choose automatically; lower values render faster,
> higher values keep more detail.

---

## 📦 Building a standalone `.exe` (for personal use)

You can pack the whole app — including FFmpeg and the icon — into a single executable
with [PyInstaller](https://pyinstaller.org/).

### 1. Install PyInstaller

```bash
pip install pyinstaller
```

> If your Python version is too new for PyInstaller, use Python 3.12 for the build.

### 2. Prepare the icon

Put your icon source image as `icon.png` next to the script and generate a
multi-size `icon.ico`:

```python
# make_icon.py
from PIL import Image

img = Image.open("icon.png").convert("RGBA")
w, h = img.size
side = max(w, h)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(img, ((side - w) // 2, (side - h) // 2))
canvas.save("icon.ico", format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
```

```bash
python make_icon.py
```

### 3. Create `hook_ffmpeg.py`

This runtime hook lets the packed `.exe` find a bundled `ffmpeg.exe` (or one placed
next to the executable):

```python
# hook_ffmpeg.py
import os, sys

if getattr(sys, "frozen", False):
    paths = [os.path.dirname(sys.executable), sys._MEIPASS]
    os.environ["PATH"] = os.pathsep.join(paths) + os.pathsep + os.environ.get("PATH", "")
```

### 4. Build

**With bundled FFmpeg** (recommended — audio works out of the box; put `ffmpeg.exe`
in the project folder first):

```bash
py -m PyInstaller --onefile --noconsole --name BarashNTSC --runtime-hook hook_ffmpeg.py --icon=icon.ico --add-binary "ffmpeg.exe;." --add-binary "icon.ico;." BarashNTSC_purple.py
```

**Without FFmpeg** (smaller file; audio features disabled unless the user has FFmpeg):

```bash
py -m PyInstaller --onefile --noconsole --name BarashNTSC --runtime-hook hook_ffmpeg.py --icon=icon.ico --add-binary "icon.ico;." BarashNTSC_purple.py
```

The finished executable appears in `dist\BarashNTSC.exe`.

> Notes:
> - The first launch of a `--onefile` build takes a few seconds (self-extraction).
> - Some antiviruses falsely flag `--onefile` PyInstaller builds; if that happens,
>   build with `--onedir` instead and distribute the whole `dist\BarashNTSC\` folder
>   as a ZIP archive.

---

## ️ Quick guide to the sliders

| Slider | What it does |
|---|---|
| Tape detail | Horizontal tape resolution (lower = softer, more "VHS") |
| Video noise | Luma/chroma noise amount |
| Color bleed | Chroma smearing |
| Chroma shift | Horizontal color displacement |
| Color phase instability | Hue wobble over time |
| Composite artifacts | NTSC "rainbow" / dot crawl on sharp edges |
| Edge enhancement | Classic VCR sharpening halos |
| Tape fade | Lifted blacks, washed-out color |
| Motion ghost | Temporal smear / ghosting |
| Interlace artifacts | Field misalignment |
| Tracking error | Moving tracking band with tearing and noise |
| Line jitter | Horizontal line instability |
| Tape wear / dropouts | White/black dropout sparkles |
| Flicker | Brightness fluctuation |
| CRT scanlines | Scanline overlay |
| Vignette | Corner darkening |

---

## 🐞 Troubleshooting

- **"FFmpeg not found: audio disabled."** — FFmpeg is not on `PATH`; install it or place
  `ffmpeg.exe` next to the script/exe.
- **Slow preview on weak CPUs** — normal for heavy effects; the preview automatically
  lowers its internal resolution on 2-core machines. Export still renders every frame.
- **Image won't open** — make sure it's a real PNG/JPG/etc.; paths with non-ASCII
  characters are supported.

---

## 📄 License

If you fork or redistribute this project, please keep the attribution to the original **BarashNTSC**. 
Add your own `LICENSE` file if you publish a derivative work.
