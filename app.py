#!/usr/bin/env python3
"""ASCII Art Converter — single-file web app. Run and open http://localhost:8899"""

import io
import re
import sys
import webbrowser
import threading
from pathlib import Path

try:
    from flask import Flask, request, jsonify, render_template_string, send_file
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    print("Installing dependencies...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "flask", "pillow", "-q"])
    from flask import Flask, request, jsonify, render_template_string, send_file
    from PIL import Image, ImageDraw, ImageFont, ImageOps

import subprocess

# ── Optional feature flags (lazy-loaded on first use) ─────────────────────────
PYDUB_OK = False
PYZBAR_OK = False
QRCODE_OK = False
REQUESTS_OK = False
PLAYWRIGHT_OK = False

AudioSegment = None
_pyzbar = None
_qrcode = None
_requests = None
_BS = None
_sync_playwright = None


def _ensure_pydub() -> bool:
    global PYDUB_OK, AudioSegment
    if AudioSegment is not None:
        return True
    try:
        try:
            import audioop  # noqa: F401
        except ModuleNotFoundError:
            subprocess.run([sys.executable, "-m", "pip", "install", "audioop-lts", "-q"])
        from pydub import AudioSegment as _AS
        AudioSegment = _AS
        PYDUB_OK = True
    except Exception:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pydub", "audioop-lts", "-q"])
            from pydub import AudioSegment as _AS
            AudioSegment = _AS
            PYDUB_OK = True
        except Exception:
            PYDUB_OK = False
    return PYDUB_OK


def _ensure_pyzbar() -> bool:
    global PYZBAR_OK, _pyzbar
    if _pyzbar is not None:
        return True
    try:
        from pyzbar import pyzbar as pz
        _pyzbar = pz
        PYZBAR_OK = True
    except ImportError:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pyzbar", "-q"])
            from pyzbar import pyzbar as pz
            _pyzbar = pz
            PYZBAR_OK = True
        except Exception:
            PYZBAR_OK = False
    return PYZBAR_OK


def _ensure_qrcode() -> bool:
    global QRCODE_OK, _qrcode
    if _qrcode is not None:
        return True
    try:
        import qrcode as qc
        _qrcode = qc
        QRCODE_OK = True
    except ImportError:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "qrcode[pil]", "-q"])
            import qrcode as qc
            _qrcode = qc
            QRCODE_OK = True
        except Exception:
            QRCODE_OK = False
    return QRCODE_OK


def _ensure_requests() -> bool:
    global REQUESTS_OK, _requests, _BS
    if _requests is not None:
        return True
    try:
        import requests as req
        from bs4 import BeautifulSoup as BS
        _requests = req
        _BS = BS
        REQUESTS_OK = True
    except ImportError:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "requests", "beautifulsoup4", "lxml", "-q"])
            import requests as req
            from bs4 import BeautifulSoup as BS
            _requests = req
            _BS = BS
            REQUESTS_OK = True
        except Exception:
            REQUESTS_OK = False
    return REQUESTS_OK


def _ensure_playwright() -> bool:
    global PLAYWRIGHT_OK, _sync_playwright
    if _sync_playwright is not None:
        return True
    try:
        from playwright.sync_api import sync_playwright as sp
        _sync_playwright = sp
        PLAYWRIGHT_OK = True
    except ImportError:
        PLAYWRIGHT_OK = False
    return PLAYWRIGHT_OK

import base64

# ── Conversion core ───────────────────────────────────────────────────────────

_QUARTER = [
    " ","\u2597","\u2596","\u2584","\u259d","\u2590","\u259e","\u259f",
    "\u2598","\u259a","\u258c","\u2599","\u2580","\u259c","\u259b","\u2588",
]
_BRAILLE_BASE = 0x2800
_BRAILLE_BITS = ((0,1,2,6),(3,4,5,7))

RAMPS = {
    "detailed": r'$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,"^`\'. ',
    "simple":   "@#S%?*+;:,. ",
    "blocks":   "█▓▒░ ",
    "dots":     "●◉○◌ ",
}


def image_to_quarter_blocks(img, width, threshold):
    ratio = img.height / img.width
    pw, ph = width * 2, int(width * ratio)
    if ph % 2: ph += 1
    g = img.resize((pw, ph), Image.LANCZOS).convert("L").load()
    lines = []
    for row in range(0, ph, 2):
        line = []
        for col in range(0, pw, 2):
            tl = g[col,   row  ] < threshold
            tr = g[col+1, row  ] < threshold if col+1 < pw else False
            bl = g[col,   row+1] < threshold if row+1 < ph else False
            br = g[col+1, row+1] < threshold if col+1 < pw and row+1 < ph else False
            line.append(_QUARTER[(tl<<3)|(tr<<2)|(bl<<1)|br])
        lines.append("".join(line))
    return "\n".join(lines)


def image_to_braille(img, width, threshold):
    ratio = img.height / img.width
    pw = width * 2
    ph = int(pw * ratio)
    rem = ph % 4
    if rem: ph += 4 - rem
    g = img.resize((pw, ph), Image.LANCZOS).convert("L").load()
    lines = []
    for row in range(0, ph, 4):
        line = []
        for col in range(0, pw, 2):
            bits = 0
            for dc in range(2):
                for dr in range(4):
                    px, py = col+dc, row+dr
                    if px < pw and py < ph and g[px, py] < threshold:
                        bits |= 1 << _BRAILLE_BITS[dc][dr]
            line.append(chr(_BRAILLE_BASE + bits))
        lines.append("".join(line))
    return "\n".join(lines)


def image_to_color_halfblock(img, width):
    ratio = img.height / img.width
    height = int(width * ratio)
    if height % 2: height += 1
    rgb = img.resize((width, height), Image.LANCZOS).convert("RGB").load()
    RESET, TOP = "\x1b[0m", "\u2580"
    lines = []
    for row in range(0, height, 2):
        line = []
        for col in range(width):
            tr, tg, tb = rgb[col, row]
            br, bg, bb = rgb[col, row+1] if row+1 < height else (0,0,0)
            line.append(f"\x1b[38;2;{tr};{tg};{tb}m\x1b[48;2;{br};{bg};{bb}m{TOP}")
        lines.append("".join(line) + RESET)
    return "\n".join(lines)


def image_to_color_quarter(img, width, threshold):
    ratio = img.height / img.width
    pw, ph = width * 2, int(width * ratio)
    if ph % 2: ph += 1
    rgb_img = img.resize((pw, ph), Image.LANCZOS).convert("RGB")
    gray    = rgb_img.convert("L").load()
    rgb     = rgb_img.load()
    RESET   = "\x1b[0m"
    lines = []
    for row in range(0, ph, 2):
        line = []
        for col in range(0, pw, 2):
            tl = gray[col,   row  ] < threshold
            tr = gray[col+1, row  ] < threshold if col+1 < pw else False
            bl = gray[col,   row+1] < threshold if row+1 < ph else False
            br = gray[col+1, row+1] < threshold if col+1 < pw and row+1 < ph else False
            char = _QUARTER[(tl<<3)|(tr<<2)|(bl<<1)|br]
            dark = []
            if tl: dark.append(rgb[col, row])
            if tr and col+1 < pw: dark.append(rgb[col+1, row])
            if bl and row+1 < ph: dark.append(rgb[col, row+1])
            if br and col+1 < pw and row+1 < ph: dark.append(rgb[col+1, row+1])
            if dark and char != " ":
                r = sum(p[0] for p in dark)//len(dark)
                g = sum(p[1] for p in dark)//len(dark)
                b = sum(p[2] for p in dark)//len(dark)
                line.append(f"\x1b[38;2;{r};{g};{b}m{char}")
            else:
                line.append(RESET + char)
        lines.append("".join(line) + RESET)
    return "\n".join(lines)


def image_to_blocks(img, width, threshold):
    ratio = img.height / img.width
    height = int(width * ratio)
    if height % 2: height += 1
    g = img.resize((width, height), Image.LANCZOS).convert("L").load()
    lines = []
    for row in range(0, height, 2):
        line = []
        for col in range(width):
            t = g[col, row] < threshold
            b = g[col, row+1] < threshold if row+1 < height else False
            line.append("\u2588" if t and b else "\u2580" if t else "\u2584" if b else " ")
        lines.append("".join(line))
    return "\n".join(lines)


def image_to_text(img, width, ramp_name):
    ramp = RAMPS.get(ramp_name, RAMPS["simple"])
    ratio = img.height / img.width * 0.55
    height = max(1, int(width * ratio))
    g = img.resize((width, height), Image.LANCZOS).convert("L").load()
    n = len(ramp) - 1
    return "\n".join(
        "".join(ramp[int(g[col, row] / 255 * n)] for col in range(width))
        for row in range(height)
    )


# ── Text → Image ─────────────────────────────────────────────────────────────

# Curated list of fonts that render well as ASCII art (bold/chunky preferred)
_FONT_CANDIDATES = [
    # ── 推荐：粗体，轮廓清晰 ──────────────────────────────
    ("Impact",               "impact.ttf"),
    ("Haettenschweiler",     "HATTEN.TTF"),
    ("Gill Sans Ultra Bold", "GILSANUB.TTF"),
    ("Franklin Gothic Heavy","FRAHVIT.TTF"),
    ("Rockwell Extra Bold",  "ROCKEB.TTF"),
    ("Rockwell Bold",        "ROCKB.TTF"),
    ("Elephant",             "ELEPHNT.TTF"),
    ("Cooper Black",         "COOPBL.TTF"),
    ("Arial Bold",           "arialbd.ttf"),
    ("Consolas Bold",        "consolab.ttf"),
    # ── 装饰/展示字体 ─────────────────────────────────────
    ("Broadway",             "BROADW.TTF"),
    ("Bauhaus 93",           "BAUHS93.TTF"),
    ("Stencil",              "STENCIL.TTF"),
    ("Magneto",              "MAGNETOB.TTF"),
    ("Snap ITC",             "SNAP____.TTF"),
    ("Playbill",             "PLAYBILL.TTF"),
    ("Latin Wide",           "LATINWD.TTF"),
    ("Jokerman",             "JOKERMAN.TTF"),
    ("Ravie",                "RAVIE.TTF"),
    ("Old English Text",     "OLDENGL.TTF"),
    ("Blackadder ITC",       "ITCBLKAD.TTF"),
    ("OCR A Extended",       "OCRAEXT.TTF"),
    ("Onyx",                 "ONYX.TTF"),
    # ── 常规备选 ──────────────────────────────────────────
    ("Segoe UI Bold",        "segoeuib.ttf"),
    ("Calibri Bold",         "calibrib.ttf"),
    ("Verdana Bold",         "verdanab.ttf"),
    ("Arial",                "arial.ttf"),
    ("Consolas",             "consola.ttf"),
]
_FONT_DIR = Path("C:/Windows/Fonts")


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a named font by searching Windows Fonts dir, fall back to default."""
    for label, fname in _FONT_CANDIDATES:
        if label == name or fname.lower() == name.lower():
            path = _FONT_DIR / fname
            if path.exists():
                return ImageFont.truetype(str(path), size)
    # fallback: first available candidate
    for _, fname in _FONT_CANDIDATES:
        path = _FONT_DIR / fname
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def available_fonts() -> list[str]:
    return [label for label, fname in _FONT_CANDIDATES if (_FONT_DIR / fname).exists()]


def text_to_image(text: str, font_name: str, font_size: int, padding: int = 20) -> Image.Image:
    """Render text to a white-background grayscale image."""
    font   = _load_font(font_name, font_size)
    lines  = text.splitlines() or [""]
    dummy  = ImageDraw.Draw(Image.new("L", (1, 1)))
    bboxes = [dummy.textbbox((0, 0), ln, font=font) for ln in lines]
    w = max(b[2] - b[0] for b in bboxes) + padding * 2
    h = sum(b[3] - b[1] for b in bboxes) + padding * 2

    img  = Image.new("L", (max(w, 1), max(h, 1)), 255)
    draw = ImageDraw.Draw(img)
    y    = padding
    for ln, bb in zip(lines, bboxes):
        draw.text((padding, y), ln, fill=0, font=font)
        y += bb[3] - bb[1]
    return img


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

_ANSI_RE    = re.compile(r"\x1b\[([0-9;]*)m")
_COLOR_MODES = {"color", "color-quarter"}


@app.route("/")
def index():
    return render_template_string(_HTML, ramps=list(RAMPS.keys()),
                                  fonts=available_fonts())


_AUDIO_EXTS = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "m4a": "audio/mp4",
}

_SRC_FMT_MAP = {"m4a": "mp4", "aac": "adts"}


@app.route("/audio-check", methods=["GET"])
def audio_check():
    if not _ensure_pydub():
        return jsonify({"ok": False, "reason": "pydub 未安装"})
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return jsonify({"ok": False, "reason": "未检测到 ffmpeg，请先安装 ffmpeg 并添加到 PATH"})
    return jsonify({"ok": True})


@app.route("/audio-convert", methods=["POST"])
def audio_convert():
    if not _ensure_pydub():
        return jsonify({"error": "pydub 未安装，请运行 pip install pydub"}), 500
    import shutil
    if not shutil.which("ffmpeg"):
        return jsonify({"error": "未检测到 ffmpeg，请先安装 ffmpeg 并添加到 PATH"}), 500

    file = request.files.get("audio")
    target_fmt = request.form.get("format", "mp3").lower()
    bitrate = request.form.get("bitrate", "192k")

    if not file:
        return jsonify({"error": "没有收到音频文件"}), 400
    if target_fmt not in _AUDIO_EXTS:
        return jsonify({"error": f"不支持的输出格式: {target_fmt}"}), 400

    src_ext = Path(file.filename).suffix.lstrip(".").lower() if file.filename else "mp3"
    src_fmt = _SRC_FMT_MAP.get(src_ext, src_ext)

    try:
        audio_bytes = io.BytesIO(file.read())
        audio = AudioSegment.from_file(audio_bytes, format=src_fmt)
        out_buf = io.BytesIO()
        export_kwargs: dict = {"format": target_fmt}
        if target_fmt == "mp3":
            export_kwargs["bitrate"] = bitrate
        audio.export(out_buf, **export_kwargs)
        out_buf.seek(0)
        stem = Path(file.filename).stem if file.filename else "audio"
        download_name = f"{stem}.{target_fmt}"
        return send_file(
            out_buf,
            as_attachment=True,
            download_name=download_name,
            mimetype=_AUDIO_EXTS[target_fmt],
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Bilibili Downloader ───────────────────────────────────────────────────────

import json as _json
import os as _os
import subprocess as _subprocess
import tempfile as _tempfile
import re as _re


def _normalize_bilibili_url(url: str) -> str:
    url = url.strip()
    if _re.match(r'^BV[a-zA-Z0-9]+$', url):
        return f"https://www.bilibili.com/video/{url}"
    if _re.match(r'^av\d+$', url, _re.IGNORECASE):
        return f"https://www.bilibili.com/video/{url}"
    return url


def _ytdlp_cmd() -> list[str] | None:
    """Return the yt-dlp command list, or None if not available."""
    import shutil
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    # Fallback: check if the Python module is importable
    try:
        import importlib
        if importlib.util.find_spec("yt_dlp"):
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    return None


def _ffmpeg_available() -> bool:
    import shutil
    return bool(shutil.which("ffmpeg"))


@app.route("/bilibili-check", methods=["GET"])
def bilibili_check():
    if not _ytdlp_cmd():
        return jsonify({"ok": False, "reason": "未检测到 yt-dlp，请运行: python -m pip install yt-dlp"})
    if not _ffmpeg_available():
        return jsonify({"ok": False, "reason": "未检测到 ffmpeg，请安装 ffmpeg 并添加到 PATH"})
    return jsonify({"ok": True})


@app.route("/bilibili-info", methods=["POST"])
def bilibili_info():
    ytdlp = _ytdlp_cmd()
    if not ytdlp:
        return jsonify({"error": "yt-dlp 未安装"}), 500

    data = request.get_json() or {}
    url = _normalize_bilibili_url(data.get("url", "").strip())
    if not url:
        return jsonify({"error": "请输入视频链接"}), 400

    try:
        result = _subprocess.run(
            ytdlp + ["--dump-single-json", url],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            return jsonify({"error": (result.stderr or "获取信息失败")[:500]}), 400
        info = _json.loads(result.stdout)

        is_playlist = info.get("_type") == "playlist"
        entries = info.get("entries") or []
        parts = []
        if is_playlist:
            for i, e in enumerate(entries):
                if e:
                    parts.append({"index": i + 1, "title": e.get("title", f"P{i+1}")})
            thumbnail = info.get("thumbnail") or (entries[0].get("thumbnail", "") if entries else "")
            uploader  = info.get("uploader")  or (entries[0].get("uploader", "")  if entries else "")
            duration  = sum(e.get("duration") or 0 for e in entries if e)
        else:
            thumbnail = info.get("thumbnail", "")
            uploader  = info.get("uploader", "")
            duration  = info.get("duration", 0)

        video_id = info.get("id", "")

        return jsonify({
            "title":       info.get("title", "未知标题"),
            "duration":    duration,
            "uploader":    uploader,
            "thumbnail":   thumbnail,
            "is_playlist": is_playlist,
            "parts":       parts,
            "video_id":    video_id,
        })
    except _subprocess.TimeoutExpired:
        return jsonify({"error": "获取信息超时"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/bilibili-download", methods=["POST"])
def bilibili_download():
    import threading as _thr

    ytdlp = _ytdlp_cmd()
    if not ytdlp:
        return jsonify({"error": "yt-dlp 未安装"}), 500

    data = request.get_json() or {}
    url = _normalize_bilibili_url(data.get("url", "").strip())
    mode      = data.get("mode", "video")
    quality   = data.get("quality", "best")
    audio_fmt = data.get("audio_fmt", "mp3")
    part      = data.get("part", "all")   # "all" | "1" | "2" | ...

    if not url:
        return jsonify({"error": "请输入视频链接"}), 400

    # playlist-items arg: specific part or all
    pl_args = ["--playlist-items", str(part)] if part != "all" else []

    tmpdir = _tempfile.mkdtemp()
    try:
        if mode == "audio":
            audio_quality = data.get("audio_quality", "192k")
            q_args = ["--audio-quality", audio_quality] if audio_fmt in ("mp3", "aac", "ogg") else []
            cmd = ytdlp + ["-f", "bestaudio", "-x", f"--audio-format={audio_fmt}"] \
                + q_args + pl_args + ["-o", f"{tmpdir}/%(playlist_index)02d-%(title)s.%(ext)s", url]
        else:
            if quality == "best":
                fmt_spec = "bestvideo+bestaudio/best"
            else:
                fmt_spec = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
            cmd = ytdlp + ["-f", fmt_spec, "--merge-output-format=mp4"] \
                + pl_args + ["-o", f"{tmpdir}/%(playlist_index)02d-%(title)s.%(ext)s", url]

        result = _subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=600, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return jsonify({"error": (result.stderr or "下载失败")[-600:]}), 500

        files = sorted(f for f in _os.listdir(tmpdir) if not f.endswith(".part"))
        if not files:
            return jsonify({"error": "下载完成但未找到文件"}), 500

        def _cleanup():
            import time, shutil as _sh
            time.sleep(60)
            _sh.rmtree(tmpdir, ignore_errors=True)
        _thr.Thread(target=_cleanup, daemon=True).start()

        # Multiple files → zip
        if len(files) > 1:
            import zipfile as _zf
            zip_path = _os.path.join(tmpdir, "bilibili_videos.zip")
            with _zf.ZipFile(zip_path, "w", _zf.ZIP_STORED) as zf:
                for f in files:
                    zf.write(_os.path.join(tmpdir, f), f)
            return send_file(zip_path, as_attachment=True,
                             download_name="bilibili_videos.zip",
                             mimetype="application/zip")

        filepath = _os.path.join(tmpdir, files[0])
        ext = Path(filepath).suffix.lstrip(".").lower()
        mime_map = {
            "mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska",
            "mp3": "audio/mpeg", "m4a": "audio/mp4", "flac": "audio/flac",
            "ogg": "audio/ogg", "aac": "audio/aac", "wav": "audio/wav",
        }
        mimetype = mime_map.get(ext, "application/octet-stream")
        return send_file(filepath, as_attachment=True,
                         download_name=files[0], mimetype=mimetype)
    except _subprocess.TimeoutExpired:
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "下载超时"}), 500
    except Exception as exc:
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/convert", methods=["POST"])
def convert():
    mode      = request.form.get("mode",      "quarter")
    width     = max(20, min(int(request.form.get("width",      120)), 400))
    threshold = max(0,  min(int(request.form.get("threshold",  128)), 255))
    ramp      = request.form.get("ramp",      "simple")
    invert    = request.form.get("invert")    == "true"
    source    = request.form.get("source",    "image")   # "image" | "text"

    if source == "text":
        text      = request.form.get("text", "").strip()
        font_name = request.form.get("font_name", "Impact")
        font_size = max(20, min(int(request.form.get("font_size", 80)), 400))
        if not text:
            return jsonify({"error": "请输入文字"}), 400
        img = text_to_image(text, font_name, font_size)
    else:
        file = request.files.get("image")
        if not file:
            return jsonify({"error": "No image"}), 400
        try:
            img = Image.open(io.BytesIO(file.read()))
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    if invert:
        img = ImageOps.invert(img.convert("L"))

    ansi = None
    match mode:
        case "quarter":
            art  = image_to_quarter_blocks(img, width, threshold)
            html = _esc(art)
        case "braille":
            art  = image_to_braille(img, width, threshold)
            html = _esc(art)
        case "color":
            ansi = image_to_color_halfblock(img, width)
            html = _ansi_to_html(ansi)
        case "color-quarter":
            ansi = image_to_color_quarter(img, width, threshold)
            html = _ansi_to_html(ansi)
        case "block":
            art  = image_to_blocks(img, width, threshold)
            html = _esc(art)
        case _:
            art  = image_to_text(img, width, ramp)
            html = _esc(art)

    return jsonify({"art": html, "ansi": ansi, "mode": mode,
                    "is_color": mode in _COLOR_MODES, "threshold": threshold})


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def _ansi_to_html(text):
    parts, open_span, last_end = [], False, 0
    for m in _ANSI_RE.finditer(text):
        chunk = text[last_end:m.start()]
        if chunk: parts.append(_esc(chunk))
        last_end = m.end()
        codes = m.group(1)
        if not codes or codes == "0":
            if open_span: parts.append("</span>"); open_span = False
            continue
        style = _codes_to_css(codes)
        if style:
            if open_span: parts.append("</span>")
            parts.append(f'<span style="{style}">'); open_span = True
    tail = text[last_end:]
    if tail: parts.append(_esc(tail))
    if open_span: parts.append("</span>")
    return "".join(parts)


def _codes_to_css(codes):
    parts = codes.split(";"); fg = bg = None; i = 0
    while i < len(parts):
        c = parts[i]
        if c == "38" and i+4 < len(parts) and parts[i+1] == "2":
            fg = f"rgb({parts[i+2]},{parts[i+3]},{parts[i+4]})"; i += 5
        elif c == "48" and i+4 < len(parts) and parts[i+1] == "2":
            bg = f"rgb({parts[i+2]},{parts[i+3]},{parts[i+4]})"; i += 5
        else:
            i += 1
    styles = []
    if fg: styles.append(f"color:{fg}")
    if bg: styles.append(f"background:{bg}")
    return ";".join(styles)


# ── QR Code routes ───────────────────────────────────────────────────────────

@app.route("/qr-check", methods=["GET"])
def qr_check():
    return jsonify({"pyzbar": _ensure_pyzbar(), "qrcode": _ensure_qrcode()})


@app.route("/qr-scan", methods=["POST"])
def qr_scan():
    if not _ensure_pyzbar():
        return jsonify({"error": "需要安装 pyzbar：pip install pyzbar"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "未上传文件"}), 400
    try:
        img = Image.open(file.stream).convert("RGB")
        decoded = _pyzbar.decode(img)
        if not decoded:
            return jsonify({"error": "未检测到二维码或条形码"}), 400
        results = []
        for obj in decoded:
            results.append({
                "type": obj.type,
                "data": obj.data.decode("utf-8", errors="replace"),
            })
        return jsonify({"results": results})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Image Grabber ─────────────────────────────────────────────────────────────

import urllib.parse as _urllib_parse

# Known data-* attributes used by lazy-loaders / CMS / CDNs
_IMG_DATA_ATTRS = [
    "src", "data-src", "data-lazy-src", "data-original", "data-image",
    "data-img", "data-url", "data-thumb", "data-photo", "data-pic",
    "data-large", "data-full", "data-hires", "data-retina", "data-zoom-image",
    "data-main-image", "data-bg", "data-background", "data-poster",
    "data-echo", "data-lazyload", "data-lazy", "data-normal", "data-800",
    "data-1000", "data-original-src", "data-fallback-src", "data-hi-res",
    "data-desktop-src", "data-mobile-src", "data-pin-media",
    "data-flickity-lazyload", "data-jpibfi-src",
]

_CSS_URL_RE  = re.compile(r'url\(\s*["\']?([^"\')\s]+)["\']?\s*\)', re.IGNORECASE)
_IMG_EXT_RE  = re.compile(
    r'https?://[^\s"\'<>{}\[\]\\]+\.(?:jpe?g|png|gif|webp|avif|svg|bmp|tiff?|ico)'
    r'(?:[?#][^\s"\'<>]*)?', re.IGNORECASE)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def _make_session() -> "_requests.Session":
    s = _requests.Session()
    s.headers.update(_BROWSER_HEADERS)
    return s


def _parse_images_from_html(html: str, base_url: str) -> list[dict]:
    """Comprehensive image URL extraction from raw HTML."""
    seen: set[str] = set()
    images: list[dict] = []

    def add(src: str, alt: str = "", w=None, h=None):
        src = src.strip()
        if not src or src.startswith("data:") or src.startswith("javascript:"):
            return
        try:
            abs_url = _urllib_parse.urljoin(base_url, src)
        except Exception:
            return
        if not abs_url.startswith("http"):
            return
        key = _urllib_parse.urldefrag(abs_url)[0]
        if key in seen:
            return
        seen.add(key)
        images.append({"url": abs_url, "alt": alt or "", "width": w, "height": h})

    soup = _BS(html, "lxml") if REQUESTS_OK else _BS(html, "html.parser")

    # ── 1. <img> — all known data-* lazy attrs ────────────────────────
    for tag in soup.find_all("img"):
        added = False
        for attr in _IMG_DATA_ATTRS:
            val = tag.get(attr, "")
            if val:
                add(val, tag.get("alt", ""), tag.get("width"), tag.get("height"))
                added = True
                break
        # srcset on <img>
        for part in (tag.get("srcset") or "").split(","):
            p = part.strip().split()
            if p:
                add(p[0])

    # ── 2. <picture> / <source> ───────────────────────────────────────
    for tag in soup.find_all("source"):
        for part in (tag.get("srcset") or "").split(","):
            p = part.strip().split()
            if p:
                add(p[0])
        if tag.get("src"):
            add(tag["src"])

    # ── 3. <meta> OG / Twitter card ──────────────────────────────────
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "twitter:image",
                    "twitter:image:src", "thumbnail"):
            add(tag.get("content", ""))

    # ── 4. <link> preload / icon ──────────────────────────────────────
    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel") or []).lower()
        if any(k in rel for k in ("preload", "icon", "thumbnail", "image")):
            add(tag["href"])

    # ── 5. <a href> pointing directly to image files ─────────────────
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if re.search(r'\.(jpe?g|png|gif|webp|avif|svg|bmp)([?#]|$)', href, re.I):
            add(href)

    # ── 6. Inline style="background-image: url(...)" ──────────────────
    for tag in soup.find_all(style=True):
        for m in _CSS_URL_RE.finditer(tag["style"]):
            add(m.group(1))

    # ── 7. <style> block CSS ──────────────────────────────────────────
    for tag in soup.find_all("style"):
        for m in _CSS_URL_RE.finditer(tag.get_text()):
            add(m.group(1))

    # ── 8. data-bg / data-background / data-background-image ──────────
    for attr in ("data-bg", "data-background", "data-background-image",
                 "data-lazy-background", "data-section-bg"):
        for tag in soup.find_all(attrs={attr: True}):
            val = tag[attr]
            m = _CSS_URL_RE.search(val)
            add(m.group(1) if m else val)

    # ── 9. JSON-LD / script body — regex mine image URLs ─────────────
    for script in soup.find_all("script"):
        text = script.get_text()
        keywords = ("image", "photo", "thumb", "picture", "poster", "cover", "avatar")
        if any(k in text.lower() for k in keywords):
            for m in _IMG_EXT_RE.finditer(text):
                add(m.group(0))

    # ── 10. Any element with common image attribute patterns ──────────
    # (catches Vue/React rendered attrs that BS4 sees as literals)
    for m in _IMG_EXT_RE.finditer(html):
        add(m.group(0))

    return images


def _grab_with_requests(url: str) -> tuple[list[dict], str]:
    """Fetch page with requests and parse all images."""
    sess = _make_session()
    resp = sess.get(url, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    final_url = resp.url
    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    html = resp.content.decode(encoding, errors="replace")
    return _parse_images_from_html(html, final_url), final_url


def _grab_with_playwright(url: str) -> tuple[list[dict], str]:
    """Render page in headless Chromium, wait for network idle, then parse."""
    with _sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=_BROWSER_HEADERS["User-Agent"],
            locale="zh-CN",
        )
        page.goto(url, wait_until="networkidle", timeout=30000)
        # scroll to trigger lazy-loaders
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        html = page.content()
        final_url = page.url
        browser.close()
    return _parse_images_from_html(html, final_url), final_url


@app.route("/imgrab-check", methods=["GET"])
def imgrab_check():
    return jsonify({
        "requests_ok": _ensure_requests(),
        "playwright_ok": _ensure_playwright(),
    })


@app.route("/img-grab", methods=["POST"])
def img_grab():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    use_browser = data.get("browser", False)
    if not url:
        return jsonify({"error": "请输入网址"}), 400
    if not url.startswith("http"):
        url = "https://" + url
    try:
        if use_browser:
            if not _ensure_playwright():
                return jsonify({"error": "Playwright 未安装，请运行：pip install playwright && playwright install chromium"}), 400
            images, final_url = _grab_with_playwright(url)
            method = "playwright"
        else:
            if not _ensure_requests():
                return jsonify({"error": "requests/beautifulsoup4 未安装"}), 400
            images, final_url = _grab_with_requests(url)
            method = "requests"
        return jsonify({"images": images, "page_url": final_url,
                        "count": len(images), "method": method})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/img-proxy", methods=["GET"])
def img_proxy():
    img_url = request.args.get("url", "").strip()
    if not img_url or not img_url.startswith("http"):
        return jsonify({"error": "无效链接"}), 400
    try:
        if _ensure_requests():
            resp = _make_session().get(img_url, timeout=12, stream=True)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            if not ct.startswith("image/"):
                ct = "image/jpeg"
            return send_file(io.BytesIO(resp.content), mimetype=ct)
        else:
            import urllib.request as _ur
            req = _ur.Request(img_url, headers={"User-Agent": _BROWSER_HEADERS["User-Agent"]})
            with _ur.urlopen(req, timeout=12) as r:
                ct = r.info().get_content_type() or "image/jpeg"
                return send_file(io.BytesIO(r.read()), mimetype=ct)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/img-download-zip", methods=["POST"])
def img_download_zip():
    import zipfile as _zf
    data = request.get_json() or {}
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "未选择图片"}), 400

    sess = _make_session() if _ensure_requests() else None
    buf = io.BytesIO()
    seen_names: dict[str, int] = {}

    with _zf.ZipFile(buf, "w", _zf.ZIP_STORED) as zf:
        for i, img_url in enumerate(urls[:200]):
            try:
                if sess:
                    resp = sess.get(img_url, timeout=15)
                    resp.raise_for_status()
                    content = resp.content
                    ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                else:
                    import urllib.request as _ur
                    req = _ur.Request(img_url, headers={"User-Agent": _BROWSER_HEADERS["User-Agent"]})
                    with _ur.urlopen(req, timeout=15) as r:
                        content = r.read()
                        ct = r.info().get_content_type() or "image/jpeg"
                path = _urllib_parse.urlparse(img_url).path
                fname = Path(path).name or f"image_{i+1}"
                if not Path(fname).suffix:
                    ext = ct.split("/")[-1].replace("jpeg", "jpg")[:6]
                    fname = f"{fname}.{ext}"
                if fname in seen_names:
                    seen_names[fname] += 1
                    base, ext = fname.rsplit(".", 1) if "." in fname else (fname, "")
                    fname = f"{base}_{seen_names[fname]}.{ext}" if ext else f"{fname}_{seen_names[fname]}"
                else:
                    seen_names[fname] = 0
                zf.writestr(fname, content)
            except Exception:
                pass

    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="images.zip",
                     mimetype="application/zip")


# ── Aria2 Manager ─────────────────────────────────────────────────────────────

import socket as _socket
import signal as _signal

_aria2_proc = None  # subprocess.Popen handle for the aria2c we spawned
_ARIA2_RPC_PORT = 6800
_ARIA2_RPC_SECRET = "ascii_art_tool"

# Mutable config — updated each time aria2 is started
_aria2_config: dict = {
    "rpc_port": 6800,
    "rpc_secret": "ascii_art_tool",
    "save_dir": "",
    "max_concurrent": 5,
    "split": 16,
    "max_conn_per_server": 16,
    "min_split_size": "1M",
    "max_download_limit": "0",
    "max_upload_limit": "0",
    "seed_time": 0,
    "seed_ratio": "1.0",
    "all_proxy": "",
    "bt_tracker": "",
    "allow_overwrite": True,
    "continue_download": True,
}


def _aria2_rpc(method: str, params=None):
    """Call aria2 JSON-RPC. Returns (result, error)."""
    import urllib.request as _ur
    import json as _j
    payload = _j.dumps({
        "jsonrpc": "2.0", "id": "1", "method": method,
        "params": [f"token:{_ARIA2_RPC_SECRET}"] + (params or [])
    }).encode()
    req = _ur.Request(
        f"http://localhost:{_ARIA2_RPC_PORT}/jsonrpc",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _ur.urlopen(req, timeout=3) as resp:
            data = _j.loads(resp.read())
            return data.get("result"), data.get("error")
    except Exception as exc:
        return None, str(exc)


def _aria2_running() -> bool:
    """Quick TCP probe: is aria2c RPC port open?"""
    try:
        with _socket.create_connection(("localhost", _ARIA2_RPC_PORT), timeout=1):
            return True
    except OSError:
        return False


@app.route("/aria2-status", methods=["GET"])
def aria2_status():
    import shutil as _shutil
    installed = bool(_shutil.which("aria2c"))
    running = _aria2_running()
    return jsonify({"installed": installed, "running": running,
                    "port": _aria2_config["rpc_port"],
                    "config": _aria2_config})


@app.route("/aria2-start", methods=["POST"])
def aria2_start():
    global _aria2_proc, _aria2_config, _ARIA2_RPC_PORT, _ARIA2_RPC_SECRET
    import shutil as _shutil
    if not _shutil.which("aria2c"):
        return jsonify({"error": "未找到 aria2c，请先安装 aria2"}), 400

    # Merge user-supplied config into module config
    body = request.get_json(silent=True) or {}
    cfg = body.get("config", {})
    for k, v in cfg.items():
        if k in _aria2_config and v != "" and v is not None:
            _aria2_config[k] = v

    port = int(_aria2_config["rpc_port"])
    secret = str(_aria2_config["rpc_secret"])
    _ARIA2_RPC_PORT = port
    _ARIA2_RPC_SECRET = secret

    if _aria2_running():
        return jsonify({"ok": True, "msg": "aria2 已在运行", "config": _aria2_config})

    args = [
        "aria2c",
        "--enable-rpc=true",
        f"--rpc-listen-port={port}",
        f"--rpc-secret={secret}",
        "--rpc-listen-all=false",
        "--daemon=false",
        f"--continue={str(_aria2_config['continue_download']).lower()}",
        f"--max-concurrent-downloads={_aria2_config['max_concurrent']}",
        f"--split={_aria2_config['split']}",
        f"--min-split-size={_aria2_config['min_split_size']}",
        f"--max-connection-per-server={_aria2_config['max_conn_per_server']}",
        f"--allow-overwrite={str(_aria2_config['allow_overwrite']).lower()}",
        f"--max-download-limit={_aria2_config['max_download_limit']}",
        f"--max-upload-limit={_aria2_config['max_upload_limit']}",
        f"--seed-time={_aria2_config['seed_time']}",
        f"--seed-ratio={_aria2_config['seed_ratio']}",
    ]
    if _aria2_config.get("save_dir"):
        args.append(f"--dir={_aria2_config['save_dir']}")
    if _aria2_config.get("all_proxy"):
        args.append(f"--all-proxy={_aria2_config['all_proxy']}")
    if _aria2_config.get("bt_tracker"):
        args.append(f"--bt-tracker={_aria2_config['bt_tracker']}")

    try:
        _aria2_proc = _subprocess.Popen(
            args, stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL
        )
        import time as _time
        for _ in range(30):
            _time.sleep(0.1)
            if _aria2_running():
                return jsonify({"ok": True, "msg": "aria2 已启动", "config": _aria2_config})
        return jsonify({"error": "aria2c 启动超时，请检查端口是否被占用"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/aria2-stop", methods=["POST"])
def aria2_stop():
    global _aria2_proc
    _aria2_rpc("aria2.shutdown")
    if _aria2_proc is not None:
        try:
            _aria2_proc.terminate()
        except Exception:
            pass
        _aria2_proc = None
    return jsonify({"ok": True, "msg": "aria2 已停止"})


@app.route("/aria2-add", methods=["POST"])
def aria2_add():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入下载链接"}), 400
    if not _aria2_running():
        return jsonify({"error": "aria2 未运行，请先启动"}), 400
    options = {}
    if data.get("dir"):
        options["dir"] = data["dir"]
    if data.get("filename"):
        options["out"] = data["filename"]
    result, err = _aria2_rpc("aria2.addUri", [[url], options])
    if err:
        return jsonify({"error": str(err)}), 500
    return jsonify({"ok": True, "gid": result})


@app.route("/aria2-list", methods=["GET"])
def aria2_list():
    if not _aria2_running():
        return jsonify({"error": "aria2 未运行"}), 400
    keys = ["gid", "status", "totalLength", "completedLength",
            "downloadSpeed", "uploadSpeed", "files", "errorMessage"]
    active, _ = _aria2_rpc("aria2.tellActive", [keys])
    waiting, _ = _aria2_rpc("aria2.tellWaiting", [0, 50, keys])
    stopped, _ = _aria2_rpc("aria2.tellStopped", [0, 20, keys])
    return jsonify({
        "active": active or [],
        "waiting": waiting or [],
        "stopped": stopped or [],
    })


@app.route("/aria2-remove", methods=["POST"])
def aria2_remove():
    data = request.get_json() or {}
    gid = data.get("gid", "")
    if not gid:
        return jsonify({"error": "缺少 gid"}), 400
    if not _aria2_running():
        return jsonify({"error": "aria2 未运行"}), 400
    result, err = _aria2_rpc("aria2.remove", [gid])
    if err:
        # try forceRemove
        result, err = _aria2_rpc("aria2.forceRemove", [gid])
    if err:
        return jsonify({"error": str(err)}), 500
    return jsonify({"ok": True})


@app.route("/aria2-pause", methods=["POST"])
def aria2_pause():
    data = request.get_json() or {}
    gid = data.get("gid", "")
    if not gid:
        return jsonify({"error": "缺少 gid"}), 400
    if not _aria2_running():
        return jsonify({"error": "aria2 未运行"}), 400
    result, err = _aria2_rpc("aria2.pause", [gid])
    if err:
        return jsonify({"error": str(err)}), 500
    return jsonify({"ok": True})


@app.route("/aria2-resume", methods=["POST"])
def aria2_resume():
    data = request.get_json() or {}
    gid = data.get("gid", "")
    if not gid:
        return jsonify({"error": "缺少 gid"}), 400
    if not _aria2_running():
        return jsonify({"error": "aria2 未运行"}), 400
    result, err = _aria2_rpc("aria2.unpause", [gid])
    if err:
        return jsonify({"error": str(err)}), 500
    return jsonify({"ok": True})


@app.route("/aria2-pick-dir", methods=["POST"])
def aria2_pick_dir():
    """Open a native folder-picker dialog and return the chosen path."""
    import threading as _threading

    result = {}

    def _open_dialog():
        try:
            import tkinter as _tk
            from tkinter import filedialog as _fd
            root = _tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = _fd.askdirectory(title="选择下载目录", parent=root)
            root.destroy()
            result["path"] = path or ""
        except Exception as exc:
            result["error"] = str(exc)

    t = _threading.Thread(target=_open_dialog, daemon=True)
    t.start()
    t.join(timeout=60)

    if "error" in result:
        return jsonify({"error": result["error"]}), 500
    return jsonify({"path": result.get("path", "")})


@app.route("/qr-generate", methods=["POST"])
def qr_generate():
    if not _ensure_qrcode():
        return jsonify({"error": "需要安装 qrcode：pip install qrcode[pil]"}), 400
    body = request.get_json(silent=True) or {}
    text = body.get("text", "").strip()
    if not text:
        return jsonify({"error": "内容为空"}), 400
    try:
        qr = _qrcode.QRCode(error_correction=_qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return jsonify({"image": f"data:image/png;base64,{b64}"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# \u2500\u2500 QQ Chat Exporter (NapCat API) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
@app.route("/bing-wallpaper", methods=["GET"])
def bing_wallpaper():
    if not _ensure_requests():
        return jsonify({"error": "requests 库未安装"}), 500
    try:
        idx = request.args.get("idx", 0, type=int)
        n   = min(request.args.get("n", 8, type=int), 16)
        mkt = request.args.get("mkt", "zh-CN")
        url = f"https://www.bing.com/HPImageArchive.aspx?format=js&idx={idx}&n={n}&mkt={mkt}"
        resp = _requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
        images = []
        for img in data.get("images", []):
            base = img.get("urlbase", "")
            images.append({
                "title":     img.get("title", ""),
                "copyright": img.get("copyright", ""),
                "date":      img.get("startdate", ""),
                "url":       f"https://www.bing.com{base}_1920x1080.jpg",
                "thumb":     f"https://www.bing.com{base}_640x360.jpg",
            })
        return jsonify({"images": images})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Embedded HTML ─────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>ASCII Art</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--text:#e6edf3;--muted:#8b949e;--danger:#f85149;--r:8px}
html[data-theme="light"]{--bg:#ffffff;--surface:#f6f8fa;--border:#d0d7de;--accent:#0969da;--green:#1a7f37;--text:#1f2328;--muted:#57606a;--danger:#cf222e}
.theme-btn{padding:5px 10px;font-size:1rem;line-height:1;border:1px solid var(--border);border-radius:var(--r);background:var(--surface);color:var(--text);cursor:pointer;transition:border-color .15s,background .15s;flex-shrink:0}
.theme-btn:hover{border-color:var(--accent)}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column}
header{border-bottom:1px solid var(--border);padding:13px 20px;display:flex;align-items:center;gap:10px}
header h1{font-size:1rem;font-weight:600}
.badge{font-size:.68rem;background:var(--accent);color:#000;padding:2px 8px;border-radius:99px;font-weight:700}
.layout{display:grid;grid-template-columns:290px 1fr;flex:1;overflow:hidden}
aside{border-right:1px solid var(--border);overflow-y:auto;padding:16px 14px;display:flex;flex-direction:column;gap:18px}
.sec-title{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:8px}
.drop-zone{border:2px dashed var(--border);border-radius:var(--r);padding:22px 12px;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;position:relative}
.drop-zone:hover,.drop-zone.over{border-color:var(--accent);background:rgba(88,166,255,.06)}
.drop-zone input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer}
.drop-icon{font-size:1.8rem;margin-bottom:6px}
.drop-label{font-size:.8rem;color:var(--muted);line-height:1.5}
.drop-label strong{color:var(--accent)}
#preview-wrap{display:none;text-align:center}
#preview-wrap img{max-width:100%;max-height:120px;border-radius:var(--r);border:1px solid var(--border)}
#file-name{font-size:.72rem;color:var(--muted);margin-top:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.mode-grid input[type=radio]{display:none}
.mode-card{border:1px solid var(--border);border-radius:var(--r);padding:8px 10px;cursor:pointer;transition:border-color .15s,background .15s}
.mode-card:hover{border-color:var(--accent)}
.mode-grid input[type=radio]:checked+.mode-card{border-color:var(--accent);background:rgba(88,166,255,.08)}
.mode-card .mode-name{font-size:.8rem;font-weight:600;color:var(--text)}
.mode-card .mode-desc{font-size:.68rem;color:var(--muted);margin-top:2px;line-height:1.3}
.mode-card .mode-tag{display:inline-block;font-size:.6rem;padding:1px 5px;border-radius:4px;margin-top:3px;font-weight:700}
.tag-best{background:rgba(63,185,80,.2);color:var(--green)}
.tag-hires{background:rgba(88,166,255,.2);color:var(--accent)}
.tag-color{background:rgba(240,136,62,.2);color:#f0883e}
.tag-legacy{background:rgba(139,148,158,.15);color:var(--muted)}
.ctrl{display:flex;flex-direction:column;gap:5px}
label.lbl{font-size:.8rem;color:var(--muted)}
label.lbl span{color:var(--text);font-weight:500}
input[type=range]{-webkit-appearance:none;width:100%;height:4px;background:var(--border);border-radius:99px;outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:15px;height:15px;border-radius:50%;background:var(--accent);cursor:pointer}
select{width:100%;padding:7px 10px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.82rem;cursor:pointer;outline:none}
select:focus{border-color:var(--accent)}
.toggle-row{display:flex;align-items:center;justify-content:space-between}
.toggle{position:relative;width:36px;height:20px}
.toggle input{display:none}
.toggle-track{position:absolute;inset:0;background:var(--border);border-radius:99px;cursor:pointer;transition:background .2s}
.toggle input:checked~.toggle-track{background:var(--accent)}
.toggle-thumb{position:absolute;top:3px;left:3px;width:14px;height:14px;border-radius:50%;background:#fff;transition:transform .2s;pointer-events:none}
.toggle input:checked~.toggle-thumb{transform:translateX(16px)}
.src-tabs{display:flex;border:1px solid var(--border);border-radius:var(--r);overflow:hidden;margin-bottom:2px}
.src-tabs input[type=radio]{display:none}
.src-tabs label{flex:1;text-align:center;padding:7px;font-size:.8rem;font-weight:600;cursor:pointer;color:var(--muted);transition:background .15s,color .15s}
.src-tabs input[type=radio]:checked+label{background:var(--accent);color:#000}
textarea#text-input{width:100%;height:90px;padding:8px 10px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.85rem;resize:vertical;outline:none;font-family:inherit}
textarea#text-input:focus{border-color:var(--accent)}
.status-bar{width:100%;padding:9px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);font-size:.82rem;font-weight:600;display:flex;align-items:center;gap:8px;color:var(--muted)}
.status-bar.loading{color:var(--accent)}
.spinner{width:13px;height:13px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite;display:none;flex-shrink:0}
.status-bar.loading .spinner{display:block}
.error-msg{color:var(--danger);font-size:.8rem;text-align:center}
main{display:flex;flex-direction:column;overflow:hidden}
.toolbar{border-bottom:1px solid var(--border);padding:8px 14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.toolbar-info{font-size:.78rem;color:var(--muted);flex:1;min-width:0}
.fs-label{font-size:.75rem;color:var(--muted);white-space:nowrap}
#font-size-range{width:70px}
.btn-sm{padding:4px 10px;font-size:.75rem;font-weight:600;border-radius:5px;border:1px solid var(--border);background:var(--surface);color:var(--text);cursor:pointer;transition:border-color .15s,color .15s;white-space:nowrap}
.btn-sm:hover{border-color:var(--accent);color:var(--accent)}
.output-wrap{flex:1;overflow:auto;padding:14px;background:var(--bg)}
#art-output{font-family:'Cascadia Code','Fira Code','Consolas','Courier New',monospace;font-size:9px;line-height:1.1;white-space:pre;display:inline-block;min-width:100%}
.placeholder{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--muted)}
.placeholder-icon{font-size:2.8rem;opacity:.25}
.placeholder p{font-size:.85rem}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:680px){.layout{grid-template-columns:1fr;grid-template-rows:auto 1fr}aside{border-right:none;border-bottom:1px solid var(--border);max-height:50vh}}
#picker-canvas{display:block;margin:0 auto;max-width:100%;max-height:300px;object-fit:contain}
#panel-picker .drop-zone{margin-bottom:0}
#color-result{animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(-5px)}to{opacity:1;transform:translateY(0)}}
.main-nav{display:flex;gap:4px;margin-left:20px;background:var(--surface);padding:4px;border-radius:var(--r);border:1px solid var(--border)}
.nav-tab{padding:6px 16px;font-size:.85rem;font-weight:600;border:none;background:transparent;color:var(--muted);cursor:pointer;border-radius:6px;transition:all .15s}
.nav-tab:hover{color:var(--text)}
.nav-tab.active{background:var(--accent);color:#000}
.module{display:none !important;flex:1;overflow:hidden;width:100%}
.module.active{display:flex !important;flex-direction:column}
.module-header{padding:12px 20px;background:var(--surface);border-bottom:1px solid var(--border)}
.module-header h2{font-size:1.1rem;font-weight:600;margin-bottom:4px}
.module-desc{font-size:.8rem;color:var(--muted)}
#module-picker{display:flex;flex-direction:column}
.picker-container{display:grid;grid-template-columns:320px 1fr;flex:1;overflow:hidden}
.picker-sidebar{border-right:1px solid var(--border);padding:16px;overflow-y:auto}
.picker-main{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;overflow:auto;background:var(--bg)}
.picker-drop-area{border:3px dashed var(--border);border-radius:var(--r);padding:40px 60px;text-align:center;cursor:pointer;transition:all .2s;min-width:400px}
.picker-drop-area:hover,.picker-drop-area.over{border-color:var(--accent);background:rgba(88,166,255,.08)}
.picker-drop-area .icon{font-size:3rem;margin-bottom:12px}
.picker-drop-area h3{font-size:1.1rem;font-weight:600;margin-bottom:8px}
.picker-drop-area p{color:var(--muted);font-size:.9rem}
.picker-canvas-wrap{text-align:center}
.picker-canvas-wrap canvas{max-width:90vw;max-height:70vh;border:1px solid var(--border);border-radius:var(--r);box-shadow:0 4px 20px rgba(0,0,0,.3);cursor:crosshair}
.color-display{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:16px;background:var(--surface);padding:16px 24px;border-radius:var(--r);border:1px solid var(--border);box-shadow:0 4px 20px rgba(0,0,0,.4);z-index:100}
.color-swatch{width:60px;height:60px;border-radius:var(--r);border:2px solid var(--border);box-shadow:inset 0 0 0 1px rgba(255,255,255,.1)}
.color-info{min-width:200px}
.color-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.color-row:last-child{margin-bottom:0}
.color-label{font-size:.75rem;color:var(--muted);width:40px}
.color-value{flex:1;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:5px;font-family:monospace;font-size:.95rem;color:var(--text)}
.copy-btn{padding:8px 16px;background:var(--accent);color:#000;border:none;border-radius:5px;font-size:.85rem;font-weight:600;cursor:pointer;transition:opacity .15s}
.copy-btn:hover{opacity:.85}
@media(max-width:900px){.picker-container{grid-template-columns:1fr}.picker-sidebar{border-right:none;border-bottom:1px solid var(--border)}.picker-drop-area{min-width:unset;width:100%}}
/* ── Audio Converter ── */
.audio-container{display:grid;grid-template-columns:300px 1fr;flex:1;overflow:hidden}
.audio-sidebar{border-right:1px solid var(--border);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:16px}
.audio-main{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:30px;overflow:auto;background:var(--bg)}
.fmt-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.fmt-grid input[type=radio]{display:none}
.fmt-card{border:1px solid var(--border);border-radius:var(--r);padding:9px 10px;cursor:pointer;transition:border-color .15s,background .15s;text-align:center}
.fmt-card:hover{border-color:var(--accent)}
.fmt-grid input[type=radio]:checked+.fmt-card{border-color:var(--accent);background:rgba(88,166,255,.08)}
.fmt-name{font-size:.88rem;font-weight:700;color:var(--text)}
.fmt-desc{font-size:.67rem;color:var(--muted);margin-top:2px;line-height:1.3}
.convert-btn{width:100%;padding:10px;background:var(--accent);color:#000;border:none;border-radius:var(--r);font-size:.9rem;font-weight:700;cursor:pointer;transition:opacity .15s}
.convert-btn:hover:not(:disabled){opacity:.85}
.convert-btn:disabled{opacity:.35;cursor:not-allowed}
.audio-info-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:24px;width:100%;max-width:480px}
.audio-info-card h3{font-size:1rem;font-weight:600;margin-bottom:14px;color:var(--text)}
.audio-info-row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:.83rem}
.audio-info-row:last-child{border-bottom:none}
.ai-label{color:var(--muted)}
.ai-value{color:var(--text);font-weight:500;text-align:right;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
audio{width:100%;margin-bottom:16px;accent-color:var(--accent)}
.audio-warn{background:rgba(248,81,73,.1);border:1px solid var(--danger);color:var(--danger);border-radius:var(--r);padding:10px 14px;font-size:.82rem;line-height:1.5}
@media(max-width:700px){.audio-container{grid-template-columns:1fr;grid-template-rows:auto 1fr}.audio-sidebar{border-right:none;border-bottom:1px solid var(--border)}}
/* ── Bilibili Downloader ── */
.bili-container{display:grid;grid-template-columns:320px 1fr;flex:1;overflow:hidden}
.bili-sidebar{border-right:1px solid var(--border);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:14px}
.bili-main{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:30px;overflow:auto;background:var(--bg)}
.bili-url-input{width:100%;padding:8px 12px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.85rem;outline:none}
.bili-url-input:focus{border-color:var(--accent)}
.bili-mode-tabs{display:flex;border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
.bili-mode-tabs input[type=radio]{display:none}
.bili-mode-tabs label{flex:1;text-align:center;padding:8px;font-size:.82rem;font-weight:600;cursor:pointer;color:var(--muted);transition:background .15s,color .15s}
.bili-mode-tabs input[type=radio]:checked+label{background:var(--accent);color:#000}
.bili-info-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:20px;width:100%;max-width:520px}
.bili-thumb{width:100%;border-radius:6px;margin-bottom:12px;max-height:220px;object-fit:cover;border:1px solid var(--border)}
.bili-title{font-size:.95rem;font-weight:600;margin-bottom:6px;color:var(--text);line-height:1.4}
.bili-meta{font-size:.78rem;color:var(--muted)}
.bili-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--muted);text-align:center}
.bili-placeholder-icon{font-size:3rem;opacity:.3}
@media(max-width:700px){.bili-container{grid-template-columns:1fr;grid-template-rows:auto 1fr}.bili-sidebar{border-right:none;border-bottom:1px solid var(--border)}}
/* ── QR Code ── */
.qr-container{display:grid;grid-template-columns:300px 1fr;flex:1;overflow:hidden}
.qr-sidebar{border-right:1px solid var(--border);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:14px}
.qr-main{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:30px;overflow:auto;background:var(--bg);gap:20px}
.qr-mode-tabs{display:flex;border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
.qr-mode-tabs input[type=radio]{display:none}
.qr-mode-tabs label{flex:1;text-align:center;padding:8px;font-size:.82rem;font-weight:600;cursor:pointer;color:var(--muted);transition:background .15s,color .15s}
.qr-mode-tabs input[type=radio]:checked+label{background:var(--accent);color:#000}
.qr-result-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:20px;width:100%;max-width:520px}
.qr-result-card h3{font-size:.9rem;font-weight:600;margin-bottom:12px;color:var(--muted)}
.qr-result-item{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:8px}
.qr-result-item:last-child{margin-bottom:0}
.qr-type-badge{display:inline-block;font-size:.65rem;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(88,166,255,.15);color:var(--accent);margin-bottom:6px}
.qr-data{font-family:monospace;font-size:.85rem;word-break:break-all;color:var(--text)}
.qr-data a{color:var(--accent);text-decoration:none}
.qr-data a:hover{text-decoration:underline}
.qr-copy-row{display:flex;gap:6px;margin-top:8px}
.qr-img-wrap{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:20px;text-align:center}
.qr-img-wrap img{max-width:300px;width:100%;image-rendering:pixelated;border-radius:4px}
.qr-gen-textarea{width:100%;height:100px;padding:8px 10px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.85rem;resize:vertical;outline:none;font-family:inherit}
.qr-gen-textarea:focus{border-color:var(--accent)}
.qr-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--muted);text-align:center}
.qr-placeholder-icon{font-size:3rem;opacity:.3}
@media(max-width:700px){.qr-container{grid-template-columns:1fr;grid-template-rows:auto 1fr}.qr-sidebar{border-right:none;border-bottom:1px solid var(--border)}}
/* ── Image Grabber ── */
.imgrab-container{display:grid;grid-template-columns:280px 1fr;flex:1;overflow:hidden}
.imgrab-sidebar{border-right:1px solid var(--border);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:14px}
.imgrab-main{display:flex;flex-direction:column;overflow:hidden;background:var(--bg)}
.imgrab-toolbar{border-bottom:1px solid var(--border);padding:8px 14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--surface)}
.imgrab-grid{flex:1;overflow-y:auto;padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;align-content:start}
.imgrab-card{border:2px solid var(--border);border-radius:var(--r);overflow:hidden;cursor:pointer;transition:border-color .15s;position:relative;background:var(--surface)}
.imgrab-card:hover{border-color:var(--accent)}
.imgrab-card.selected{border-color:var(--accent);box-shadow:0 0 0 2px rgba(88,166,255,.3)}
.imgrab-check{position:absolute;top:6px;left:6px;width:18px;height:18px;accent-color:var(--accent);cursor:pointer;z-index:2}
.imgrab-thumb{width:100%;height:130px;object-fit:cover;display:block;background:var(--border)}
.imgrab-card-info{padding:5px 8px;font-size:.68rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.imgrab-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--muted);height:100%;grid-column:1/-1}
.imgrab-placeholder-icon{font-size:3rem;opacity:.3}
.imgrab-url-input{width:100%;padding:8px 12px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.85rem;outline:none}
.imgrab-url-input:focus{border-color:var(--accent)}
.imgrab-dl-btn{padding:5px 16px;background:var(--accent);color:#000;border:none;border-radius:5px;font-size:.8rem;font-weight:700;cursor:pointer;transition:opacity .15s;white-space:nowrap}
.imgrab-dl-btn:disabled{opacity:.35;cursor:not-allowed}
.imgrab-dl-btn:hover:not(:disabled){opacity:.85}
@media(max-width:700px){.imgrab-container{grid-template-columns:1fr;grid-template-rows:auto 1fr}.imgrab-sidebar{border-right:none;border-bottom:1px solid var(--border)}}
/* ── Aria2 Manager ── */
.aria2-container{display:grid;grid-template-columns:300px 1fr;flex:1;overflow:hidden}
.aria2-sidebar{border-right:1px solid var(--border);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:14px}
.aria2-main{display:flex;flex-direction:column;overflow:hidden}
.aria2-power-btn{width:100%;padding:14px;font-size:1rem;font-weight:700;border:none;border-radius:var(--r);cursor:pointer;transition:background .2s,color .2s,box-shadow .2s}
.aria2-power-btn.off{background:rgba(248,81,73,.15);color:var(--danger);border:1px solid var(--danger)}
.aria2-power-btn.off:hover{background:rgba(248,81,73,.25)}
.aria2-power-btn.on{background:rgba(63,185,80,.15);color:var(--green);border:1px solid var(--green)}
.aria2-power-btn.on:hover{background:rgba(63,185,80,.25)}
.aria2-status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.aria2-status-dot.off{background:var(--danger)}
.aria2-status-dot.on{background:var(--green);box-shadow:0 0 6px var(--green)}
.aria2-url-input{width:100%;padding:8px 12px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.85rem;outline:none;resize:vertical;min-height:60px;font-family:inherit}
.aria2-url-input:focus{border-color:var(--accent)}
.aria2-list-header{border-bottom:1px solid var(--border);padding:10px 16px;display:flex;align-items:center;gap:8px;background:var(--surface)}
.aria2-list-header span{font-size:.8rem;font-weight:600;color:var(--muted)}
.aria2-list-header .refresh-btn{margin-left:auto;padding:3px 10px;font-size:.75rem;background:transparent;border:1px solid var(--border);color:var(--text);border-radius:5px;cursor:pointer;transition:border-color .15s}
.aria2-list-header .refresh-btn:hover{border-color:var(--accent);color:var(--accent)}
.aria2-list{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:8px}
.aria2-item{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:10px 12px}
.aria2-item-name{font-size:.82rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:5px}
.aria2-item-meta{display:flex;align-items:center;gap:10px;font-size:.72rem;color:var(--muted)}
.aria2-progress-wrap{margin:5px 0;height:4px;background:var(--border);border-radius:99px;overflow:hidden}
.aria2-progress-bar{height:100%;border-radius:99px;transition:width .5s}
.aria2-progress-bar.active{background:var(--accent)}
.aria2-progress-bar.complete{background:var(--green)}
.aria2-progress-bar.error{background:var(--danger)}
.aria2-item-actions{display:flex;gap:6px;margin-top:6px}
.aria2-act-btn{padding:2px 8px;font-size:.72rem;border:1px solid var(--border);background:transparent;color:var(--muted);border-radius:4px;cursor:pointer;transition:all .15s}
.aria2-act-btn:hover{border-color:var(--accent);color:var(--accent)}
.aria2-act-btn.danger:hover{border-color:var(--danger);color:var(--danger)}
.aria2-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--muted);flex:1}
.aria2-placeholder-icon{font-size:3rem;opacity:.3}
.aria2-not-installed{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.3);border-radius:var(--r);padding:12px 14px;font-size:.82rem;color:var(--danger);line-height:1.6}
.aria2-not-installed code{color:var(--accent);font-size:.8rem}
/* aria2 main tabs */
.aria2-main-tabs{border-bottom:1px solid var(--border);padding:0 16px;display:flex;align-items:center;gap:2px;background:var(--surface);flex-shrink:0}
.aria2-main-tab{padding:9px 14px;font-size:.82rem;font-weight:600;border:none;background:transparent;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;white-space:nowrap}
.aria2-main-tab:hover{color:var(--text)}
.aria2-main-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.aria2-tab-count{font-size:.72rem;color:var(--muted);padding:0 4px}
.aria2-panel{display:none;flex:1;overflow:hidden;flex-direction:column}
.aria2-panel.active{display:flex}
/* config panel */
.aria2-config-scroll{flex:1;overflow-y:auto;padding:16px}
.aria2-cfg-group{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);margin-bottom:12px;overflow:hidden}
.aria2-cfg-group-title{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);padding:8px 14px;border-bottom:1px solid var(--border);background:rgba(255,255,255,.02)}
.aria2-cfg-row{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--border)}
.aria2-cfg-row:last-child{border-bottom:none}
.aria2-cfg-label{font-size:.8rem;color:var(--muted);flex:1;white-space:nowrap}
.aria2-cfg-input{padding:5px 10px;border-radius:6px;background:var(--bg);color:var(--text);border:1px solid var(--border);font-size:.82rem;outline:none;width:180px;font-family:inherit}
.aria2-cfg-input:focus{border-color:var(--accent)}
.aria2-cfg-input-sm{width:90px}
.aria2-browse-btn{flex-shrink:0;padding:5px 9px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:.9rem;cursor:pointer;transition:border-color .15s,background .15s;line-height:1}
.aria2-browse-btn:hover{border-color:var(--accent);background:rgba(88,166,255,.08)}
.aria2-browse-btn:active{opacity:.7}
@media(max-width:700px){.aria2-container{grid-template-columns:1fr;grid-template-rows:auto 1fr}.aria2-sidebar{border-right:none;border-bottom:1px solid var(--border)}}
/* ── Bing Wallpaper ── */
.bing-container{display:flex;flex-direction:column;flex:1;overflow:hidden}
.bing-toolbar{border-bottom:1px solid var(--border);padding:8px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:var(--surface)}
.bing-select{padding:6px 10px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.82rem;outline:none;cursor:pointer}
.bing-select:focus{border-color:var(--accent)}
.bing-grid{flex:1;overflow-y:auto;padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;align-content:start;background:var(--bg)}
.bing-card{border:1px solid var(--border);border-radius:var(--r);overflow:hidden;background:var(--surface);transition:border-color .15s,transform .15s;cursor:pointer}
.bing-card:hover{border-color:var(--accent);transform:translateY(-2px)}
.bing-thumb-link{display:block}.bing-thumb{width:100%;height:160px;object-fit:cover;display:block;background:var(--border)}
.bing-card-body{padding:10px 12px}
.bing-card-title{font-size:.85rem;font-weight:600;color:var(--text);margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bing-card-copy{font-size:.72rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bing-card-date{font-size:.7rem;color:var(--muted);margin-top:4px}
.bing-card-actions{display:flex;gap:6px;margin-top:8px}
.bing-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--muted);height:100%;grid-column:1/-1}
/* lightbox */
.bing-lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:1000;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:20px}
.bing-lb.open{display:flex}
.bing-lb-img{max-width:90vw;max-height:75vh;border-radius:var(--r);border:1px solid var(--border);object-fit:contain}
.bing-lb-title{font-size:1rem;font-weight:600;color:#fff;text-align:center;max-width:800px}
.bing-lb-copy{font-size:.82rem;color:rgba(255,255,255,.6);text-align:center;max-width:700px}
.bing-lb-actions{display:flex;gap:10px}
.bing-lb-close{position:absolute;top:14px;right:18px;font-size:1.5rem;color:#fff;background:none;border:none;cursor:pointer;opacity:.7;line-height:1}
.bing-lb-close:hover{opacity:1}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:10px;flex:1">
    <span style="font-size:1.3rem">⬛</span>
    <h1>工具箱</h1>
    <nav class="main-nav">
      <button class="nav-tab active" data-tab="ascii">ASCII 艺术</button>
      <button class="nav-tab" data-tab="picker">🎨 吸管工具</button>
      <button class="nav-tab" data-tab="audio">🎵 音频转换</button>
      <button class="nav-tab" data-tab="bili">📺 B站下载</button>
      <button class="nav-tab" data-tab="qr">📷 二维码</button>
      <button class="nav-tab" data-tab="imgrab">🖼️ 图片抓取</button>
      <button class="nav-tab" data-tab="aria2">⬇️ Aria2</button>
      <button class="nav-tab" data-tab="bing">🌄 必应壁纸</button>
    </nav>
  </div>
  <button class="theme-btn" id="theme-toggle" title="切换主题">🌙</button>
  <span class="badge">v2.5</span>
</header>

<!-- ASCII Art Module -->
<div id="module-ascii" class="module active">
  <div class="module-header">
    <h2>ASCII Art 转换器</h2>
    <p class="module-desc">将图片或文字转换为 ASCII 艺术</p>
  </div>
  <div class="layout">
  <aside>
    <section>
      <p class="sec-title">输入源</p>
      <div class="src-tabs">
        <input type="radio" name="source" id="src-image" value="image" checked/>
        <label for="src-image">🖼️ 图片</label>
        <input type="radio" name="source" id="src-text" value="text"/>
        <label for="src-text">T 文字</label>
      </div>
      <div id="panel-image">
        <div class="drop-zone" id="drop-zone">
          <input type="file" id="file-input" accept="image/*"/>
          <div class="drop-icon">🖼️</div>
          <p class="drop-label">拖放图片到此处<br>或 <strong>点击选择文件</strong></p>
        </div>
        <div id="preview-wrap">
          <img id="preview-img" src="" alt="preview"/>
          <p id="file-name"></p>
        </div>
      </div>
      <div id="panel-text" style="display:none">
        <textarea id="text-input" placeholder="输入要转换的文字…"></textarea>
        <div class="ctrl" style="margin-top:10px">
          <label class="lbl">字体</label>
          <select id="font-select">
            {% for f in fonts %}<option value="{{ f }}">{{ f }}</option>{% endfor %}
          </select>
        </div>
        <div class="ctrl" style="margin-top:8px">
          <label class="lbl">字号：<span id="font-size-val">80</span>px</label>
          <input type="range" id="font-size-input" min="20" max="300" value="80"/>
        </div>
      </div>
    </section>
    <section>
      <p class="sec-title">渲染模式</p>
      <div class="mode-grid">
        <input type="radio" name="mode" id="m-quarter" value="quarter" checked/>
        <label class="mode-card" for="m-quarter">
          <div class="mode-name">四分块</div>
          <div class="mode-desc">16 种块字符<br>▖▗▘▛▜▝▞▟</div>
          <span class="mode-tag tag-best">推荐</span>
        </label>
        <input type="radio" name="mode" id="m-braille" value="braille"/>
        <label class="mode-card" for="m-braille">
          <div class="mode-name">盲文点阵</div>
          <div class="mode-desc">256 种字符<br>2×4 像素/字</div>
          <span class="mode-tag tag-hires">高分辨率</span>
        </label>
        <input type="radio" name="mode" id="m-color" value="color"/>
        <label class="mode-card" for="m-color">
          <div class="mode-name">彩色半块</div>
          <div class="mode-desc">▀ fg/bg 全色<br>每像素精确</div>
          <span class="mode-tag tag-color">真彩色</span>
        </label>
        <input type="radio" name="mode" id="m-cquarter" value="color-quarter"/>
        <label class="mode-card" for="m-cquarter">
          <div class="mode-name">彩色四分块</div>
          <div class="mode-desc">高分辨率<br>+ 彩色</div>
          <span class="mode-tag tag-color">真彩色</span>
        </label>
        <input type="radio" name="mode" id="m-block" value="block"/>
        <label class="mode-card" for="m-block">
          <div class="mode-name">半块</div>
          <div class="mode-desc">▀▄█ 经典<br>黑白</div>
          <span class="mode-tag tag-legacy">基础</span>
        </label>
        <input type="radio" name="mode" id="m-text" value="text"/>
        <label class="mode-card" for="m-text">
          <div class="mode-name">文字渐变</div>
          <div class="mode-desc">@#%?*+;:,.<br>传统风格</div>
          <span class="mode-tag tag-legacy">基础</span>
        </label>
      </div>
    </section>
    <section class="ctrl">
      <label class="lbl">宽度：<span id="width-val">120</span> 字符</label>
      <input type="range" id="width-range" min="20" max="300" value="120"/>
    </section>
    <section class="ctrl" id="thresh-sec">
      <label class="lbl">亮度阈值：<span id="thresh-val">128</span></label>
      <input type="range" id="thresh-range" min="0" max="255" value="128"/>
    </section>
    <section class="ctrl" id="ramp-sec" style="display:none">
      <label class="lbl">字符梯度</label>
      <select id="ramp-select">
        {% for r in ramps %}<option value="{{ r }}"{% if r=='simple'%} selected{% endif %}>{{ r }}</option>{% endfor %}
      </select>
    </section>
    <section>
      <div class="toggle-row">
        <span style="font-size:.82rem">反色</span>
        <label class="toggle">
          <input type="checkbox" id="invert-check"/>
          <div class="toggle-track"></div>
          <div class="toggle-thumb"></div>
        </label>
      </div>
    </section>
    <div class="status-bar" id="status-bar">
      <div class="spinner"></div>
      <span id="status-text">等待图片…</span>
    </div>
    <p id="error-msg" class="error-msg" style="display:none"></p>
  </aside>
  <main>
    <div class="toolbar">
      <span class="toolbar-info" id="toolbar-info"></span>
      <span class="fs-label">字号 <strong id="fs-display">9</strong>px</span>
      <input type="range" id="font-size-range" min="4" max="24" value="9"/>
      <button class="btn-sm" id="copy-btn">复制文本</button>
      <button class="btn-sm" id="save-btn">保存 .txt</button>
      <button class="btn-sm" id="export-btn">导出图片</button>
    </div>
    <div class="output-wrap">
      <div class="placeholder" id="placeholder">
        <div class="placeholder-icon">⬛</div>
        <p>上传图片即可预览</p>
      </div>
      <pre id="art-output" style="display:none"></pre>
    </div>
  </main>
  </div>
</div>

<!-- Color Picker Module -->
<div id="module-picker" class="module">
  <div class="module-header">
    <h2>🎨 吸管工具</h2>
    <p class="module-desc">上传图片并点击任意位置提取颜色</p>
  </div>
  <div class="picker-container">
    <div class="picker-sidebar">
      <div class="drop-zone" id="picker-drop-zone">
        <input type="file" id="picker-file-input" accept="image/*"/>
        <div class="drop-icon">🖼️</div>
        <p class="drop-label">拖放图片到此处<br>或 <strong>点击选择</strong><br>Ctrl+V 粘贴</p>
      </div>
      <div id="picker-file-info" style="display:none;margin-top:10px;font-size:.75rem;color:var(--muted)"></div>
    </div>
    <div class="picker-main" id="picker-main">
      <div class="picker-drop-area" id="picker-drop-area">
        <div class="icon">🎨</div>
        <h3>吸管工具</h3>
        <p>拖放图片、点击上传或 Ctrl+V 粘贴</p>
      </div>
      <div class="picker-canvas-wrap" id="picker-canvas-wrap" style="display:none">
        <canvas id="picker-canvas"></canvas>
      </div>
    </div>
  </div>
  <div class="color-display" id="color-display" style="display:none">
    <div class="color-swatch" id="color-swatch"></div>
    <div class="color-info">
      <div class="color-row">
        <span class="color-label">HEX</span>
        <input type="text" class="color-value" id="picker-color-hex" readonly/>
      </div>
      <div class="color-row">
        <span class="color-label">RGB</span>
        <input type="text" class="color-value" id="picker-color-rgb" readonly/>
      </div>
    </div>
    <button class="copy-btn" id="picker-copy-btn">复制 HEX</button>
  </div>
</div>

<!-- Audio Converter Module -->
<div id="module-audio" class="module">
  <div class="module-header">
    <h2>🎵 音频格式转换</h2>
    <p class="module-desc">在 FLAC、MP3、WAV、OGG 等格式之间互转</p>
  </div>
  <div class="audio-container">
    <div class="audio-sidebar">
      <section>
        <p class="sec-title">上传音频</p>
        <div class="drop-zone" id="audio-drop-zone">
          <input type="file" id="audio-file-input" accept="audio/*,.flac,.mp3,.wav,.ogg,.aac,.m4a,.wma"/>
          <div class="drop-icon">🎵</div>
          <p class="drop-label">拖放音频到此处<br>或 <strong>点击选择文件</strong></p>
        </div>
      </section>
      <section>
        <p class="sec-title">输出格式</p>
        <div class="fmt-grid">
          <input type="radio" name="audio-fmt" id="afmt-mp3" value="mp3" checked/>
          <label class="fmt-card" for="afmt-mp3">
            <div class="fmt-name">MP3</div>
            <div class="fmt-desc">通用兼容<br>有损压缩</div>
          </label>
          <input type="radio" name="audio-fmt" id="afmt-flac" value="flac"/>
          <label class="fmt-card" for="afmt-flac">
            <div class="fmt-name">FLAC</div>
            <div class="fmt-desc">无损压缩<br>高保真</div>
          </label>
          <input type="radio" name="audio-fmt" id="afmt-wav" value="wav"/>
          <label class="fmt-card" for="afmt-wav">
            <div class="fmt-name">WAV</div>
            <div class="fmt-desc">无压缩<br>最大兼容</div>
          </label>
          <input type="radio" name="audio-fmt" id="afmt-ogg" value="ogg"/>
          <label class="fmt-card" for="afmt-ogg">
            <div class="fmt-name">OGG</div>
            <div class="fmt-desc">开源格式<br>有损压缩</div>
          </label>
          <input type="radio" name="audio-fmt" id="afmt-aac" value="aac"/>
          <label class="fmt-card" for="afmt-aac">
            <div class="fmt-name">AAC</div>
            <div class="fmt-desc">现代有损<br>Apple 生态</div>
          </label>
          <input type="radio" name="audio-fmt" id="afmt-m4a" value="m4a"/>
          <label class="fmt-card" for="afmt-m4a">
            <div class="fmt-name">M4A</div>
            <div class="fmt-desc">AAC 封装<br>iTunes 兼容</div>
          </label>
        </div>
      </section>
      <section id="audio-bitrate-sec">
        <p class="sec-title">MP3 比特率</p>
        <select id="audio-bitrate-select" style="width:100%;padding:7px 10px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.82rem;outline:none">
          <option value="320k">320 kbps（最高质量）</option>
          <option value="256k">256 kbps</option>
          <option value="192k" selected>192 kbps（推荐）</option>
          <option value="128k">128 kbps</option>
          <option value="96k">96 kbps</option>
          <option value="64k">64 kbps</option>
        </select>
      </section>
      <button class="convert-btn" id="audio-convert-btn" disabled>转换并下载</button>
      <div class="status-bar" id="audio-status-bar">
        <div class="spinner" id="audio-spinner" style="display:none"></div>
        <span id="audio-status-text">等待音频文件…</span>
      </div>
      <p id="audio-error-msg" class="error-msg" style="display:none"></p>
    </div>
    <div class="audio-main" id="audio-main">
      <div class="placeholder" id="audio-placeholder">
        <div class="placeholder-icon">🎵</div>
        <p>上传音频文件开始转换</p>
        <p style="font-size:.78rem;margin-top:10px;color:var(--muted)">支持：MP3 · FLAC · WAV · OGG · AAC · M4A · WMA</p>
        <p id="audio-dep-warn" style="display:none;margin-top:14px"></p>
      </div>
      <div id="audio-info-panel" style="display:none;width:100%;max-width:480px">
        <audio id="audio-player" controls class="audio-player"></audio>
        <div class="audio-info-card">
          <h3>文件信息</h3>
          <div class="audio-info-row"><span class="ai-label">文件名</span><span class="ai-value" id="ai-name">—</span></div>
          <div class="audio-info-row"><span class="ai-label">格式</span><span class="ai-value" id="ai-fmt">—</span></div>
          <div class="audio-info-row"><span class="ai-label">大小</span><span class="ai-value" id="ai-size">—</span></div>
          <div class="audio-info-row"><span class="ai-label">时长</span><span class="ai-value" id="ai-dur">—</span></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Bilibili Downloader Module -->
<div id="module-bili" class="module">
  <div class="module-header">
    <h2>📺 B站视频下载</h2>
    <p class="module-desc">输入 BV号、AV号或完整链接，下载视频或提取音频（需要 yt-dlp 和 ffmpeg）</p>
  </div>
  <div class="bili-container">
    <div class="bili-sidebar">
      <section>
        <p class="sec-title">视频链接</p>
        <input type="text" class="bili-url-input" id="bili-url-input" placeholder="BV1xx… / av123456 / 完整 URL"/>
        <button class="convert-btn" id="bili-fetch-btn" style="margin-top:8px">获取信息</button>
      </section>
      <section>
        <p class="sec-title">下载模式</p>
        <div class="bili-mode-tabs">
          <input type="radio" name="bili-mode" id="bili-mode-video" value="video" checked/>
          <label for="bili-mode-video">🎬 视频</label>
          <input type="radio" name="bili-mode" id="bili-mode-audio" value="audio"/>
          <label for="bili-mode-audio">🎵 仅音频</label>
        </div>
      </section>
      <section id="bili-quality-sec">
        <p class="sec-title">视频画质</p>
        <select id="bili-quality-select">
          <option value="best">最高画质</option>
          <option value="1080">1080p</option>
          <option value="720">720p</option>
          <option value="480">480p</option>
          <option value="360">360p</option>
        </select>
      </section>
      <section id="bili-part-sec" style="display:none">
        <p class="sec-title">选择分P</p>
        <select id="bili-part-select">
          <option value="all">全部分P（打包 zip）</option>
        </select>
      </section>
      <section id="bili-audiofmt-sec" style="display:none">
        <p class="sec-title">音频格式</p>
        <select id="bili-audiofmt-select">
          <option value="mp3">MP3</option>
          <option value="aac">AAC</option>
          <option value="m4a">M4A</option>
          <option value="flac">FLAC（无损·体积大）</option>
          <option value="wav">WAV（无损·体积很大）</option>
        </select>
      </section>
      <section id="bili-audioq-sec" style="display:none">
        <p class="sec-title">音频码率</p>
        <select id="bili-audioq-select">
          <option value="320k">320 kbps（最高）</option>
          <option value="192k" selected>192 kbps（推荐）</option>
          <option value="128k">128 kbps</option>
          <option value="96k">96 kbps</option>
        </select>
      </section>
      <button class="convert-btn" id="bili-dl-btn" disabled>下载</button>
      <div class="status-bar" id="bili-status-bar">
        <div class="spinner" id="bili-spinner" style="display:none"></div>
        <span id="bili-status-text">请输入链接</span>
      </div>
      <p id="bili-error-msg" class="error-msg" style="display:none"></p>
    </div>
    <div class="bili-main">
      <div class="bili-placeholder" id="bili-placeholder">
        <div class="bili-placeholder-icon">📺</div>
        <p>输入 B站链接后点击「获取信息」</p>
        <p style="font-size:.75rem;margin-top:6px;color:var(--muted)">需要安装 yt-dlp 和 ffmpeg</p>
        <p id="bili-dep-warn" style="display:none;margin-top:14px"></p>
      </div>
      <div id="bili-info-panel" style="display:none;width:100%;max-width:560px">
        <div class="bili-info-card">
          <div class="bili-title" id="bili-title"></div>
          <div class="bili-meta" id="bili-meta"></div>
        </div>
        <div id="bili-player-wrap" style="display:none;margin-top:10px;border-radius:var(--r);overflow:hidden;background:#000;aspect-ratio:16/9">
          <iframe id="bili-player-iframe"
            src=""
            width="100%" height="100%"
            scrolling="no" border="0" frameborder="no" framespacing="0"
            allowfullscreen="true"
            style="display:block;border:none">
          </iframe>
        </div>
        <div id="bili-preview-wrap" style="display:none;margin-top:12px">
          <p class="sec-title" style="margin-bottom:6px">预览</p>
          <video id="bili-video-preview" controls style="width:100%;border-radius:var(--r);background:#000;display:none;max-height:320px"></video>
          <audio id="bili-audio-preview" controls style="width:100%;margin-top:4px;display:none"></audio>
          <p id="bili-preview-name" style="font-size:.72rem;color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></p>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- QR Code Module -->
<div id="module-qr" class="module">
  <div class="module-header">
    <h2>📷 二维码工具</h2>
    <p class="module-desc">扫描识别二维码 / 条形码，或生成自定义二维码</p>
  </div>
  <div class="qr-container">
    <div class="qr-sidebar">
      <section>
        <p class="sec-title">模式</p>
        <div class="qr-mode-tabs">
          <input type="radio" name="qr-mode" id="qr-mode-scan" value="scan" checked/>
          <label for="qr-mode-scan">📷 扫描</label>
          <input type="radio" name="qr-mode" id="qr-mode-gen" value="gen"/>
          <label for="qr-mode-gen">✨ 生成</label>
        </div>
      </section>
      <!-- Scan panel -->
      <section id="qr-scan-panel">
        <p class="sec-title">上传图片</p>
        <div class="drop-zone" id="qr-drop-zone">
          <input type="file" id="qr-file-input" accept="image/*"/>
          <div class="drop-icon">📷</div>
          <p class="drop-label">拖放图片到此处<br>或 <strong>点击选择文件</strong></p>
        </div>
        <div id="qr-preview-wrap" style="display:none;margin-top:8px">
          <img id="qr-preview-img" src="" alt="preview" style="max-width:100%;border-radius:var(--r);border:1px solid var(--border)"/>
          <p id="qr-file-name" style="font-size:.72rem;color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></p>
        </div>
      </section>
      <!-- Generate panel -->
      <section id="qr-gen-panel" style="display:none">
        <p class="sec-title">输入内容</p>
        <textarea class="qr-gen-textarea" id="qr-gen-input" placeholder="输入文字、URL 或任意内容…"></textarea>
        <button class="convert-btn" id="qr-gen-btn" style="margin-top:8px">生成二维码</button>
      </section>
      <div class="status-bar" id="qr-status-bar">
        <div class="spinner" id="qr-spinner" style="display:none"></div>
        <span id="qr-status-text">就绪</span>
      </div>
      <p id="qr-error-msg" class="error-msg" style="display:none"></p>
    </div>
    <div class="qr-main" id="qr-main">
      <div class="qr-placeholder" id="qr-placeholder">
        <div class="qr-placeholder-icon">📷</div>
        <p id="qr-placeholder-text">上传含二维码的图片以识别</p>
      </div>
      <div id="qr-scan-result" style="display:none;width:100%;max-width:520px"></div>
      <div id="qr-gen-result" style="display:none"></div>
    </div>
  </div>
</div>

<!-- Image Grabber Module -->
<div id="module-imgrab" class="module">
  <div class="module-header">
    <h2>🖼️ 网站图片抓取</h2>
    <p class="module-desc">输入网页地址，一键获取页面上的所有图片，勾选后批量下载</p>
  </div>
  <div class="imgrab-container">
    <div class="imgrab-sidebar">
      <section>
        <p class="sec-title">网页地址</p>
        <input type="text" class="imgrab-url-input" id="imgrab-url-input" placeholder="https://example.com"/>
        <button class="convert-btn" id="imgrab-fetch-btn" style="margin-top:8px">获取图片</button>
      </section>
      <section>
        <p class="sec-title">抓取模式</p>
        <div class="toggle-row" style="margin-bottom:6px">
          <span style="font-size:.82rem">🌐 浏览器渲染（JS 页面）</span>
          <label class="toggle">
            <input type="checkbox" id="imgrab-browser-toggle"/>
            <div class="toggle-track"></div>
            <div class="toggle-thumb"></div>
          </label>
        </div>
        <p id="imgrab-browser-hint" style="font-size:.7rem;color:var(--muted);line-height:1.5">
          启用后使用 Playwright 无头浏览器，可抓取 JS 渲染页面，但速度较慢。<br>
          需先运行：<code style="color:var(--accent)">pip install playwright && playwright install chromium</code>
        </p>
        <p id="imgrab-playwright-warn" style="display:none;font-size:.75rem;color:var(--danger);margin-top:4px"></p>
      </section>
      <section class="ctrl">
        <label class="lbl">最小尺寸过滤：<span id="imgrab-minsize-val">50</span> px</label>
        <input type="range" id="imgrab-minsize" min="0" max="400" value="50" step="10"/>
        <p style="font-size:.7rem;color:var(--muted);margin-top:3px">过滤掉宽高均小于该值的小图</p>
      </section>
      <div class="status-bar" id="imgrab-status-bar">
        <div class="spinner" id="imgrab-spinner" style="display:none"></div>
        <span id="imgrab-status-text">输入网址后点击获取</span>
      </div>
      <p id="imgrab-error-msg" class="error-msg" style="display:none"></p>
    </div>
    <div class="imgrab-main">
      <div class="imgrab-toolbar" id="imgrab-toolbar" style="display:none">
        <button class="btn-sm" id="imgrab-sel-all">全选</button>
        <button class="btn-sm" id="imgrab-sel-none">取消全选</button>
        <button class="btn-sm" id="imgrab-sel-invert">反选</button>
        <span class="toolbar-info" id="imgrab-sel-info" style="flex:1"></span>
        <button class="imgrab-dl-btn" id="imgrab-dl-btn" disabled>⬇ 下载选中</button>
      </div>
      <div id="imgrab-grid" class="imgrab-grid">
        <div class="imgrab-placeholder" id="imgrab-placeholder">
          <div class="imgrab-placeholder-icon">🖼️</div>
          <p>输入网址后点击「获取图片」</p>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Aria2 Manager Module -->
<div id="module-aria2" class="module">
  <div class="module-header">
    <h2>⬇️ Aria2 下载管理器</h2>
    <p class="module-desc">内置 aria2c RPC 守护进程，支持 HTTP/FTP/磁力链接/BT 下载</p>
  </div>
  <div class="aria2-container">
    <div class="aria2-sidebar">
      <section>
        <p class="sec-title">服务状态</p>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span class="aria2-status-dot off" id="aria2-dot"></span>
          <span id="aria2-status-text" style="font-size:.82rem;color:var(--muted)">未运行</span>
        </div>
        <button class="aria2-power-btn off" id="aria2-power-btn">启动 Aria2</button>
        <p id="aria2-not-installed-hint" style="display:none;margin-top:8px" class="aria2-not-installed">
          未找到 <strong>aria2c</strong>，请先安装 aria2：<br>
          <code>Windows: winget install aria2</code><br>
          <code>macOS:   brew install aria2</code><br>
          <code>Linux:   apt install aria2</code>
        </p>
      </section>
      <section>
        <p class="sec-title">添加下载</p>
        <textarea class="aria2-url-input" id="aria2-url-input"
          placeholder="输入下载链接（HTTP/FTP/磁力/BT），每行一个&#10;支持批量添加"></textarea>
        <div style="display:flex;gap:6px;margin-top:6px">
          <input type="text" id="aria2-savedir-input"
            style="flex:1;padding:6px 10px;border-radius:var(--r);background:var(--surface);color:var(--text);border:1px solid var(--border);font-size:.78rem;outline:none"
            placeholder="保存目录（可选）"/>
          <button class="aria2-browse-btn" id="aria2-savedir-browse" title="浏览目录">📂</button>
        </div>
        <button class="convert-btn" id="aria2-add-btn" disabled style="margin-top:8px">添加到队列</button>
        <p id="aria2-add-msg" style="font-size:.75rem;margin-top:5px;display:none"></p>
      </section>
      <section>
        <p class="sec-title">连接信息</p>
        <div style="font-size:.75rem;color:var(--muted);line-height:1.8">
          <div>端口：<code style="color:var(--accent)">6800</code></div>
          <div>RPC 密钥：<code style="color:var(--accent)">ascii_art_tool</code></div>
        </div>
      </section>
    </div>
    <div class="aria2-main">
      <!-- Main tab bar -->
      <div class="aria2-main-tabs">
        <button class="aria2-main-tab active" data-panel="queue">📋 下载队列</button>
        <button class="aria2-main-tab" data-panel="config">⚙️ 参数设置</button>
        <span id="aria2-count" class="aria2-tab-count"></span>
        <button class="refresh-btn" id="aria2-refresh-btn" style="margin-left:auto">刷新</button>
      </div>

      <!-- Queue panel -->
      <div class="aria2-panel active" id="aria2-panel-queue">
        <div class="aria2-list" id="aria2-list">
          <div class="aria2-placeholder" id="aria2-placeholder">
            <div class="aria2-placeholder-icon">⬇️</div>
            <p>启动 Aria2 后即可管理下载任务</p>
          </div>
        </div>
      </div>

      <!-- Config panel -->
      <div class="aria2-panel" id="aria2-panel-config">
        <div class="aria2-config-scroll">

          <div class="aria2-cfg-group">
            <div class="aria2-cfg-group-title">🔌 RPC 连接</div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">监听端口</label>
              <input class="aria2-cfg-input" id="cfg-rpc-port" type="number" min="1024" max="65535" value="6800" placeholder="6800"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">RPC 密钥</label>
              <input class="aria2-cfg-input" id="cfg-rpc-secret" type="text" value="ascii_art_tool" placeholder="留空则不鉴权"/>
            </div>
          </div>

          <div class="aria2-cfg-group">
            <div class="aria2-cfg-group-title">📁 存储</div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">默认保存目录</label>
              <div style="display:flex;gap:5px;align-items:center">
                <input class="aria2-cfg-input" id="cfg-save-dir" type="text" placeholder="留空使用 aria2 默认目录"/>
                <button class="aria2-browse-btn" id="cfg-save-dir-browse" title="浏览目录">📂</button>
              </div>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">覆盖已有文件</label>
              <label class="toggle" style="margin-left:auto">
                <input type="checkbox" id="cfg-allow-overwrite" checked/>
                <div class="toggle-track"></div><div class="toggle-thumb"></div>
              </label>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">断点续传</label>
              <label class="toggle" style="margin-left:auto">
                <input type="checkbox" id="cfg-continue" checked/>
                <div class="toggle-track"></div><div class="toggle-thumb"></div>
              </label>
            </div>
          </div>

          <div class="aria2-cfg-group">
            <div class="aria2-cfg-group-title">⚡ 速度限制</div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">下载限速</label>
              <input class="aria2-cfg-input" id="cfg-dl-limit" type="text" value="0" placeholder="0=不限，支持 K/M，如 10M"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">上传限速</label>
              <input class="aria2-cfg-input" id="cfg-ul-limit" type="text" value="0" placeholder="0=不限，如 512K"/>
            </div>
          </div>

          <div class="aria2-cfg-group">
            <div class="aria2-cfg-group-title">🔗 连接与分段</div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">最大并发下载数</label>
              <input class="aria2-cfg-input aria2-cfg-input-sm" id="cfg-max-concurrent" type="number" min="1" max="20" value="5"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">单文件分段数</label>
              <input class="aria2-cfg-input aria2-cfg-input-sm" id="cfg-split" type="number" min="1" max="64" value="16"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">单服务器最大连接</label>
              <input class="aria2-cfg-input aria2-cfg-input-sm" id="cfg-max-conn" type="number" min="1" max="16" value="16"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">最小分段大小</label>
              <input class="aria2-cfg-input aria2-cfg-input-sm" id="cfg-min-split" type="text" value="1M" placeholder="如 1M、512K"/>
            </div>
          </div>

          <div class="aria2-cfg-group">
            <div class="aria2-cfg-group-title">🧲 BT / 磁力</div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">最大做种时间（分钟）</label>
              <input class="aria2-cfg-input aria2-cfg-input-sm" id="cfg-seed-time" type="number" min="0" value="0" placeholder="0=关闭做种"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">做种比例</label>
              <input class="aria2-cfg-input aria2-cfg-input-sm" id="cfg-seed-ratio" type="text" value="1.0" placeholder="如 1.0"/>
            </div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">自定义 Tracker</label>
              <textarea class="aria2-cfg-input" id="cfg-bt-tracker" rows="2"
                style="resize:vertical;min-height:50px"
                placeholder="多个 Tracker 用逗号分隔"></textarea>
            </div>
          </div>

          <div class="aria2-cfg-group">
            <div class="aria2-cfg-group-title">🌐 代理</div>
            <div class="aria2-cfg-row">
              <label class="aria2-cfg-label">HTTP/SOCKS 代理</label>
              <input class="aria2-cfg-input" id="cfg-proxy" type="text" placeholder="如 http://127.0.0.1:7890"/>
            </div>
          </div>

          <div style="padding:0 4px 16px">
            <p id="aria2-cfg-save-msg" style="font-size:.75rem;margin-bottom:8px;display:none"></p>
            <button class="convert-btn" id="aria2-cfg-save-btn" style="width:100%">
              保存并重启 Aria2
            </button>
            <p style="font-size:.7rem;color:var(--muted);margin-top:6px;text-align:center">
              仅在 Aria2 运行时重启生效；未运行时保存供下次启动使用
            </p>
          </div>

        </div>
      </div>
    </div>
  </div>
</div>


<!-- Bing Wallpaper Module -->
<div id="module-bing" class="module">
  <div class="module-header">
    <h2>🌄 必应每日壁纸</h2>
    <p class="module-desc">获取必应每日精选壁纸，支持预览和下载高清原图</p>
  </div>
  <div class="bing-container">
    <div class="bing-toolbar">
      <select id="bing-mkt" class="bing-select" title="语言/地区">
        <option value="zh-CN">中文（简体）</option>
        <option value="zh-TW">中文（繁体）</option>
        <option value="en-US">English (US)</option>
        <option value="ja-JP">日本語</option>
        <option value="de-DE">Deutsch</option>
        <option value="fr-FR">Français</option>
      </select>
      <select id="bing-count" class="bing-select" title="获取数量">
        <option value="4">4 张</option>
        <option value="8" selected>8 张</option>
        <option value="16">16 张</option>
      </select>
      <button class="convert-btn" id="bing-fetch-btn" style="width:auto;padding:7px 18px;font-size:.82rem">获取壁纸</button>
      <span id="bing-status" style="font-size:.78rem;color:var(--muted)"></span>
    </div>
    <div class="bing-grid" id="bing-grid">
      <div class="bing-placeholder" id="bing-placeholder">
        <div style="font-size:3rem;opacity:.3">🌄</div>
        <p>点击「获取壁纸」加载必应每日壁纸</p>
      </div>
    </div>
  </div>
</div>

<!-- Bing Lightbox -->
<div class="bing-lb" id="bing-lb">
  <button class="bing-lb-close" id="bing-lb-close">✕</button>
  <img class="bing-lb-img" id="bing-lb-img" src="" alt=""/>
  <div class="bing-lb-title" id="bing-lb-title"></div>
  <div class="bing-lb-copy" id="bing-lb-copy"></div>
  <div class="bing-lb-actions">
    <button class="convert-btn" id="bing-lb-dl" style="width:auto;padding:8px 20px">⬇ 下载原图</button>
    <button class="btn-sm" id="bing-lb-copy-url">复制链接</button>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

// ── Theme Toggle ─────────────────────────────────────
(function(){
  const saved = localStorage.getItem('theme') || 'dark';
  if(saved === 'light') document.documentElement.setAttribute('data-theme','light');
  const btn = document.getElementById('theme-toggle');
  function updateBtn(){
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.textContent = isLight ? '☀️' : '🌙';
    btn.title = isLight ? '切换到深色主题' : '切换到浅色主题';
  }
  updateBtn();
  btn.addEventListener('click',()=>{
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    if(isLight){
      document.documentElement.removeAttribute('data-theme');
      localStorage.setItem('theme','dark');
    } else {
      document.documentElement.setAttribute('data-theme','light');
      localStorage.setItem('theme','light');
    }
    updateBtn();
  });
})();

// ── Navigation ───────────────────────────────────────
document.querySelectorAll('.nav-tab').forEach(tab=>{
  tab.addEventListener('click',()=>{
    const target=tab.dataset.tab;
    console.log('Switching to tab:', target);
    document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.module').forEach(m=>m.classList.remove('active'));
    const targetModule = document.getElementById('module-' + target);
    if(targetModule){
      targetModule.classList.add('active');
      console.log('Activated module:', 'module-' + target);
    }
  });
});

// ═════════════════════════════════════════════════════
// ASCII ART MODULE
// ═════════════════════════════════════════════════════
const fileInput   = $('file-input'),  dropZone   = $('drop-zone');
const previewWrap = $('preview-wrap'),previewImg = $('preview-img');
const fileNameEl  = $('file-name'),   artOutput  = $('art-output');
const placeholder = $('placeholder'), errorMsg   = $('error-msg');
const toolbarInfo = $('toolbar-info');
const widthRange  = $('width-range'), widthVal   = $('width-val');
const threshRange = $('thresh-range'),threshVal  = $('thresh-val');
const fontRange   = $('font-size-range'), fsDisplay = $('fs-display');
const threshSec   = $('thresh-sec'), rampSec    = $('ramp-sec');
const statusBar   = $('status-bar'), statusText = $('status-text');
const panelImage  = $('panel-image'),panelText  = $('panel-text');
const textInput   = $('text-input');
const fontSizeInput=$('font-size-input'),fontSizeVal=$('font-size-val');

let currentFile = null, lastPlainText = '', lastAnsiText = '';

// ── source tab ───────────────────────────────────────
function currentSource(){return document.querySelector('input[name=source]:checked').value}

document.querySelectorAll('input[name=source]').forEach(r=>{
  r.addEventListener('change',()=>{
    const src=currentSource();
    const isText=src==='text';
    panelImage.style.display=isText?'none':'';
    panelText.style.display =isText?'':'none';
    if(isText&&textInput.value.trim()) convert();
    else if(!isText&&currentFile) convert();
  });
});

// ── image upload ─────────────────────────────────────
dropZone.addEventListener('dragover', e=>{e.preventDefault();dropZone.classList.add('over')});
dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('over'));
dropZone.addEventListener('drop', e=>{
  e.preventDefault(); dropZone.classList.remove('over');
  const f=e.dataTransfer.files[0];
  if(f&&f.type.startsWith('image/')) setFile(f);
});
fileInput.addEventListener('change',()=>{if(fileInput.files[0])setFile(fileInput.files[0])});

function setFile(file){
  currentFile=file;
  previewImg.src=URL.createObjectURL(file);
  fileNameEl.textContent=file.name;
  previewWrap.style.display='block';
  dropZone.querySelector('.drop-icon').style.display='none';
  dropZone.querySelector('.drop-label').style.display='none';
  showError('');
  convert();
}

// ── text input ───────────────────────────────────────
textInput.addEventListener('input', debouncedConvert);
$('font-select').addEventListener('change', convert);
fontSizeInput.addEventListener('input',()=>{
  fontSizeVal.textContent=fontSizeInput.value;
  debouncedConvert();
});

// ── mode / controls ──────────────────────────────────
const COLOR_MODES=new Set(['color','color-quarter']);
document.querySelectorAll('input[name=mode]').forEach(r=>{
  r.addEventListener('change',()=>{updateControls();convert()});
});
function updateControls(){
  const m=document.querySelector('input[name=mode]:checked').value;
  threshSec.style.display=m==='color'?'none':'';
  rampSec.style.display  =m==='text' ?''    :'none';
}

widthRange.addEventListener('input',()=>{widthVal.textContent=widthRange.value;debouncedConvert()});
threshRange.addEventListener('input',()=>{threshVal.textContent=threshRange.value;debouncedConvert()});
fontRange.addEventListener('input',()=>{
  fsDisplay.textContent=fontRange.value;
  artOutput.style.fontSize=fontRange.value+'px';
});
$('ramp-select').addEventListener('change',convert);
$('invert-check').addEventListener('change',convert);

let debounceTimer=null;
function debouncedConvert(){clearTimeout(debounceTimer);debounceTimer=setTimeout(convert,400)}

let inflight=false, pending=false;
async function convert(){
  const src=currentSource();
  if(src==='image'&&!currentFile) return;
  if(src==='text'&&!textInput.value.trim()) return;
  if(inflight){pending=true;return}
  inflight=true;
  statusBar.classList.add('loading');
  statusText.textContent='生成中…';
  showError('');
  const fd=new FormData();
  fd.append('source',    src);
  fd.append('mode',      document.querySelector('input[name=mode]:checked').value);
  fd.append('width',     widthRange.value);
  fd.append('threshold', threshRange.value);
  fd.append('ramp',      $('ramp-select').value);
  fd.append('invert',    $('invert-check').checked);
  fd.append('auto_thresh',false);
  if(src==='image'){
    fd.append('image', currentFile);
  }else{
    fd.append('text',      textInput.value);
    fd.append('font_name', $('font-select').value);
    fd.append('font_size', fontSizeInput.value);
  }
  try{
    const res=await fetch('/convert',{method:'POST',body:fd});
    const data=await res.json();
    if(data.error){showError(data.error);return}
    placeholder.style.display='none';
    artOutput.style.display='inline-block';
    if(data.is_color){
      artOutput.innerHTML=data.art;
      lastPlainText=artOutput.textContent;
      lastAnsiText=data.ansi||'';
    }else{
      artOutput.textContent=data.art;
      lastPlainText=data.art;
      lastAnsiText='';
    }
    const lines=lastPlainText.split('\n');
    const threshInfo=data.mode!=='color'?`  ·  阈值 ${data.threshold}`:'';
    toolbarInfo.textContent=`${lines.length} 行 × ${lines[0]?.length??0} 列  ·  ${data.mode}  ·  宽度 ${widthRange.value}${threshInfo}`;
    statusText.textContent='就绪';
  }catch(e){
    showError('转换失败');
    statusText.textContent='出错';
  }finally{
    inflight=false;
    statusBar.classList.remove('loading');
    if(pending){pending=false;convert()}
  }
}

$('copy-btn').addEventListener('click',async()=>{
  const text=lastAnsiText||lastPlainText;
  if(!text)return;
  await navigator.clipboard.writeText(text);
  const btn=$('copy-btn');
  btn.textContent=lastAnsiText?'✓ 已复制 ANSI':'✓ 已复制';
  setTimeout(()=>btn.textContent='复制文本',1500);
});

$('save-btn').addEventListener('click',()=>{
  const text=lastAnsiText||lastPlainText;
  if(!text)return;
  Object.assign(document.createElement('a'),{
    href:URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'})),
    download:'ascii_art.txt'
  }).click();
});

$('export-btn').addEventListener('click',exportImage);
function exportImage(){
  if(!lastPlainText)return;
  const btn=$('export-btn');
  btn.textContent='渲染中…'; btn.disabled=true;
  const probe=document.createElement('span');
  const ps=getComputedStyle(artOutput);
  probe.style.cssText=[`font-family:${ps.fontFamily}`,`font-size:${ps.fontSize}`,
    `line-height:${ps.lineHeight}`,'position:absolute','visibility:hidden',
    'white-space:pre','top:0','left:0'].join(';');
  probe.textContent='█'.repeat(20)+'\n'+'█'.repeat(20);
  document.body.appendChild(probe);
  const pr=probe.getBoundingClientRect();
  const charW=pr.width/20, charH=pr.height/2;
  document.body.removeChild(probe);
  const lines=lastPlainText.split('\n');
  const rows=lines.length, cols=Math.max(...lines.map(l=>[...l].length));
  const canvas=document.createElement('canvas');
  canvas.width=Math.ceil(cols*charW); canvas.height=Math.ceil(rows*charH);
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.font=`${ps.fontSize} ${ps.fontFamily}`; ctx.textBaseline='top';
  let row=0,col=0;
  function drawChar(c,fg,bg){
    if(c==='\n'){row++;col=0;return}
    const x=col*charW,y=row*charH;
    if(bg){ctx.fillStyle=bg;ctx.fillRect(x,y,charW,charH)}
    ctx.fillStyle=fg||'#e6edf3'; ctx.fillText(c,x,y); col++;
  }
  function walkNode(node){
    if(node.nodeType===Node.TEXT_NODE){for(const c of node.textContent)drawChar(c,'#e6edf3',null)}
    else if(node.tagName==='SPAN'){
      const s=node.getAttribute('style')||'';
      const fg=(s.match(/(?:^|;)\s*color\s*:\s*([^;]+)/)||[])[1]?.trim()||'#e6edf3';
      const bg=(s.match(/background\s*:\s*([^;]+)/)||[])[1]?.trim()||null;
      for(const c of node.textContent)drawChar(c,fg,bg);
    }else node.childNodes.forEach(walkNode);
  }
  artOutput.childNodes.forEach(walkNode);
  setTimeout(()=>{
    canvas.toBlob(blob=>{
      Object.assign(document.createElement('a'),{
        href:URL.createObjectURL(blob),download:'ascii_art.png'
      }).click();
      btn.textContent='导出图片'; btn.disabled=false;
    },'image/png');
  },0);
}

function showError(msg){
  errorMsg.textContent=msg;
  errorMsg.style.display=msg?'block':'none';
}

// ═════════════════════════════════════════════════════
// COLOR PICKER MODULE
// ═════════════════════════════════════════════════════
const pickerDropZone=$('picker-drop-zone'),pickerFileInput=$('picker-file-input');
const pickerDropArea=$('picker-drop-area'),pickerCanvasWrap=$('picker-canvas-wrap');
const pickerCanvas=$('picker-canvas'),pickerFileInfo=$('picker-file-info');
const colorDisplay=$('color-display'),colorSwatch=$('color-swatch');
const pickerColorHex=$('picker-color-hex'),pickerColorRgb=$('picker-color-rgb');
let pickerImg=null, pickerImgFile=null;

// File upload
dropZoneAddListeners(pickerDropZone,loadPickerImage);
dropZoneAddListeners(pickerDropArea,loadPickerImage);

pickerFileInput.addEventListener('change',()=>{
  if(pickerFileInput.files[0])loadPickerImage(pickerFileInput.files[0]);
});

function dropZoneAddListeners(zone,callback){
  zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('over')});
  zone.addEventListener('dragleave',()=>zone.classList.remove('over'));
  zone.addEventListener('drop',e=>{
    e.preventDefault();zone.classList.remove('over');
    const f=e.dataTransfer.files[0];
    if(f&&f.type.startsWith('image/'))callback(f);
  });
}

// Paste from clipboard
document.addEventListener('paste',e=>{
  if(!document.getElementById('module-picker').classList.contains('active'))return;
  const items=e.clipboardData.items;
  for(let i=0;i<items.length;i++){
    if(items[i].type.indexOf('image')!==-1){
      const blob=items[i].getAsFile();
      if(blob)loadPickerImage(blob);
      break;
    }
  }
});

function loadPickerImage(file){
  pickerImgFile=file;
  const url=URL.createObjectURL(file);
  const img=new Image();
  img.onload=()=>{
    pickerImg=img;
    pickerCanvas.width=img.width;
    pickerCanvas.height=img.height;
    const ctx=pickerCanvas.getContext('2d');
    ctx.drawImage(img,0,0);
    
    pickerDropArea.style.display='none';
    pickerCanvasWrap.style.display='block';
    pickerFileInfo.style.display='block';
    pickerFileInfo.textContent=`${file.name} · ${img.width}×${img.height}px`;
    colorDisplay.style.display='flex';
  };
  img.src=url;
}

// Canvas click to pick color
pickerCanvas.addEventListener('click',e=>{
  if(!pickerImg)return;
  const rect=pickerCanvas.getBoundingClientRect();
  const scaleX=pickerCanvas.width/rect.width;
  const scaleY=pickerCanvas.height/rect.height;
  const x=Math.floor((e.clientX-rect.left)*scaleX);
  const y=Math.floor((e.clientY-rect.top)*scaleY);
  
  const ctx=pickerCanvas.getContext('2d');
  const pixel=ctx.getImageData(x,y,1,1).data;
  const r=pixel[0],g=pixel[1],b=pixel[2];
  const hex='#'+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1).toUpperCase();
  
  colorSwatch.style.backgroundColor=hex;
  pickerColorHex.value=hex;
  pickerColorRgb.value=`rgb(${r}, ${g}, ${b})`;
  
  // Redraw image and show cursor
  ctx.drawImage(pickerImg,0,0);
  ctx.strokeStyle='#fff';
  ctx.lineWidth=2;
  ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.stroke();
  ctx.strokeStyle='#000';
  ctx.lineWidth=1;
  ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.stroke();
});

// Copy button
$('picker-copy-btn').addEventListener('click',async()=>{
  const hex=pickerColorHex.value;
  if(!hex)return;
  await navigator.clipboard.writeText(hex);
  const btn=$('picker-copy-btn');
  btn.textContent='✓ 已复制';
  setTimeout(()=>btn.textContent='复制 HEX',1500);
});

// Select all on click
pickerColorHex.addEventListener('click',()=>pickerColorHex.select());
pickerColorRgb.addEventListener('click',()=>pickerColorRgb.select());

// ═════════════════════════════════════════════════════
// AUDIO CONVERTER MODULE
// ═════════════════════════════════════════════════════
(function(){
  const audioDropZone=$('audio-drop-zone');
  const audioFileInput=$('audio-file-input');
  const audioConvertBtn=$('audio-convert-btn');
  const audioStatusBar=$('audio-status-bar');
  const audioStatusText=$('audio-status-text');
  const audioSpinner=$('audio-spinner');
  const audioErrorMsg=$('audio-error-msg');
  const audioPlaceholder=$('audio-placeholder');
  const audioInfoPanel=$('audio-info-panel');
  const audioPlayer=$('audio-player');
  const audioBitrateSec=$('audio-bitrate-sec');
  const audioDepWarn=$('audio-dep-warn');
  let currentAudioFile=null;

  // Check deps on tab switch
  document.querySelector('[data-tab="audio"]').addEventListener('click',checkDeps);

  async function checkDeps(){
    try{
      const res=await fetch('/audio-check');
      const data=await res.json();
      if(!data.ok){
        audioDepWarn.style.display='block';
        audioDepWarn.innerHTML=`<span class="audio-warn">⚠️ ${data.reason}</span>`;
        audioConvertBtn.disabled=true;
      }else{
        audioDepWarn.style.display='none';
        if(currentAudioFile) audioConvertBtn.disabled=false;
      }
    }catch(e){}
  }

  // Drop zone
  audioDropZone.addEventListener('dragover',e=>{e.preventDefault();audioDropZone.classList.add('over')});
  audioDropZone.addEventListener('dragleave',()=>audioDropZone.classList.remove('over'));
  audioDropZone.addEventListener('drop',e=>{
    e.preventDefault();audioDropZone.classList.remove('over');
    const f=e.dataTransfer.files[0];
    if(f)loadAudioFile(f);
  });
  audioFileInput.addEventListener('change',()=>{
    if(audioFileInput.files[0])loadAudioFile(audioFileInput.files[0]);
  });

  function fmtBytes(b){
    if(b<1024)return b+' B';
    if(b<1048576)return (b/1024).toFixed(1)+' KB';
    return (b/1048576).toFixed(2)+' MB';
  }

  function loadAudioFile(file){
    currentAudioFile=file;
    // Show info panel
    audioPlaceholder.style.display='none';
    audioInfoPanel.style.display='block';
    // Audio player preview
    const url=URL.createObjectURL(file);
    audioPlayer.src=url;
    // File details
    $('ai-name').textContent=file.name;
    $('ai-fmt').textContent=(file.name.split('.').pop()||'?').toUpperCase();
    $('ai-size').textContent=fmtBytes(file.size);
    $('ai-dur').textContent='加载中…';
    audioPlayer.onloadedmetadata=()=>{
      const s=Math.round(audioPlayer.duration);
      const m=Math.floor(s/60),sec=s%60;
      $('ai-dur').textContent=`${m}:${String(sec).padStart(2,'0')}`;
    };
    // Update drop zone label
    audioDropZone.querySelector('.drop-icon').textContent='✅';
    audioDropZone.querySelector('.drop-label').innerHTML=`已选择 <strong>${file.name}</strong><br><span style="font-size:.7rem;color:var(--muted)">点击重新选择</span>`;
    audioConvertBtn.disabled=false;
    audioStatusText.textContent='选择输出格式后点击转换';
    showAudioError('');
  }

  // Format selection — show/hide bitrate
  document.querySelectorAll('input[name="audio-fmt"]').forEach(r=>{
    r.addEventListener('change',()=>{
      audioBitrateSec.style.display=r.value==='mp3'?'':'none';
    });
  });

  // Convert button
  audioConvertBtn.addEventListener('click',async()=>{
    if(!currentAudioFile)return;
    const fmt=document.querySelector('input[name="audio-fmt"]:checked').value;
    const bitrate=$('audio-bitrate-select').value;
    audioConvertBtn.disabled=true;
    audioSpinner.style.display='block';
    audioStatusText.textContent='转换中，请稍候…';
    audioStatusBar.classList.add('loading');
    showAudioError('');
    const fd=new FormData();
    fd.append('audio',currentAudioFile);
    fd.append('format',fmt);
    fd.append('bitrate',bitrate);
    try{
      const res=await fetch('/audio-convert',{method:'POST',body:fd});
      if(!res.ok){
        const err=await res.json().catch(()=>({error:'转换失败'}));
        showAudioError(err.error||'转换失败');
        audioStatusText.textContent='出错';
        return;
      }
      // Trigger download
      const blob=await res.blob();
      const stem=currentAudioFile.name.replace(/\.[^.]+$/,'');
      const url=URL.createObjectURL(blob);
      Object.assign(document.createElement('a'),{
        href:url,download:`${stem}.${fmt}`
      }).click();
      audioStatusText.textContent=`✓ 已转换为 ${fmt.toUpperCase()} 并下载`;
    }catch(e){
      showAudioError('请求失败：'+e.message);
      audioStatusText.textContent='出错';
    }finally{
      audioSpinner.style.display='none';
      audioStatusBar.classList.remove('loading');
      audioConvertBtn.disabled=false;
    }
  });

  function showAudioError(msg){
    audioErrorMsg.textContent=msg;
    audioErrorMsg.style.display=msg?'block':'none';
  }
})();

// ═════════════════════════════════════════════════════
// BILIBILI DOWNLOADER MODULE
// ═════════════════════════════════════════════════════
(function(){
  const urlInput=$('bili-url-input');
  const fetchBtn=$('bili-fetch-btn');
  const dlBtn=$('bili-dl-btn');
  const statusBar=$('bili-status-bar');
  const statusText=$('bili-status-text');
  const spinner=$('bili-spinner');
  const errorMsg=$('bili-error-msg');
  const placeholder=$('bili-placeholder');
  const infoPanel=$('bili-info-panel');
  const depWarn=$('bili-dep-warn');
  const qualitySec=$('bili-quality-sec');
  const audioFmtSec=$('bili-audiofmt-sec');
  const audioQSec=$('bili-audioq-sec');
  const partSec=$('bili-part-sec');
  const partSelect=$('bili-part-select');
  let videoInfo=null;

  document.querySelector('[data-tab="bili"]').addEventListener('click',checkDeps);

  async function checkDeps(){
    try{
      const res=await fetch('/bilibili-check');
      const data=await res.json();
      if(!data.ok){
        depWarn.style.display='block';
        depWarn.innerHTML=`<span class="audio-warn">⚠️ ${data.reason}</span>`;
        fetchBtn.disabled=true;
      }else{
        depWarn.style.display='none';
        fetchBtn.disabled=false;
      }
    }catch(e){}
  }

  function updateBiliMode(){
    const isAudio=document.querySelector('input[name="bili-mode"]:checked').value==='audio';
    qualitySec.style.display=isAudio?'none':'';
    audioFmtSec.style.display=isAudio?'':'none';
    updateAudioFmt();
  }
  function updateAudioFmt(){
    const fmt=$('bili-audiofmt-select').value;
    const lossy=fmt==='mp3'||fmt==='aac'||fmt==='ogg';
    audioQSec.style.display=(document.querySelector('input[name="bili-mode"]:checked').value==='audio'&&lossy)?'':'none';
  }
  document.querySelectorAll('input[name="bili-mode"]').forEach(r=>r.addEventListener('change',updateBiliMode));
  $('bili-audiofmt-select').addEventListener('change',updateAudioFmt);

  fetchBtn.addEventListener('click',async()=>{
    const url=urlInput.value.trim();
    if(!url)return;
    fetchBtn.disabled=true;
    spinner.style.display='block';
    statusText.textContent='获取视频信息…';
    statusBar.classList.add('loading');
    showBiliError('');
    try{
      const res=await fetch('/bilibili-info',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url})
      });
      const data=await res.json();
      if(data.error){showBiliError(data.error);statusText.textContent='出错';return}
      videoInfo=data;
      showInfo(data);
      dlBtn.disabled=false;
      statusText.textContent='就绪，点击下载';
    }catch(e){
      showBiliError('请求失败：'+e.message);
      statusText.textContent='出错';
    }finally{
      fetchBtn.disabled=false;
      spinner.style.display='none';
      statusBar.classList.remove('loading');
    }
  });

  function clearBiliPreview(){
    $('bili-preview-wrap').style.display='none';
    const v=$('bili-video-preview'),a=$('bili-audio-preview');
    v.pause();v.src='';v.style.display='none';
    a.pause();a.src='';a.style.display='none';
    $('bili-preview-name').textContent='';
    $('bili-player-wrap').style.display='none';
    $('bili-player-iframe').src='';
  }

  function showInfo(info){
    clearBiliPreview();
    placeholder.style.display='none';
    infoPanel.style.display='block';
    $('bili-title').textContent=info.title||'未知标题';
    const dur=info.duration?formatDur(info.duration):'';
    const partCount=info.is_playlist?`${info.parts.length} P`:'';
    $('bili-meta').textContent=[info.uploader,partCount,dur].filter(Boolean).join(' · ');
    // 嵌入 B站播放器
    const vid=info.video_id||'';
    const playerWrap=$('bili-player-wrap');
    const playerIframe=$('bili-player-iframe');
    if(vid&&!info.is_playlist){
      const isBV=/^BV/i.test(vid);
      const embedSrc=isBV
        ?`https://player.bilibili.com/player.html?bvid=${vid}&autoplay=0`
        :`https://player.bilibili.com/player.html?aid=${vid.replace(/^av/i,'')}&autoplay=0`;
      playerIframe.src=embedSrc;
      playerWrap.style.display='block';
    }else{
      playerWrap.style.display='none';
    }
    // part selector
    if(info.is_playlist&&info.parts.length>0){
      partSec.style.display='';
      partSelect.innerHTML='<option value="all">全部分P（打包 zip）</option>';
      info.parts.forEach(p=>{
        const opt=document.createElement('option');
        opt.value=String(p.index);
        opt.textContent=`P${p.index}：${p.title}`;
        partSelect.appendChild(opt);
      });
    }else{
      partSec.style.display='none';
      partSelect.innerHTML='<option value="all">全部</option>';
    }
  }

  function formatDur(s){
    s=Math.round(s);
    const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;
    if(h)return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    return `${m}:${String(sec).padStart(2,'0')}`;
  }

  dlBtn.addEventListener('click',async()=>{
    if(!videoInfo)return;
    const url=urlInput.value.trim();
    const mode=document.querySelector('input[name="bili-mode"]:checked').value;
    const quality=$('bili-quality-select').value;
    const audioFmt=$('bili-audiofmt-select').value;
    const audioQuality=$('bili-audioq-select').value;
    const part=partSelect.value;
    dlBtn.disabled=true;
    fetchBtn.disabled=true;
    spinner.style.display='block';
    const allParts=part==='all'&&videoInfo&&videoInfo.is_playlist;
    statusText.textContent=allParts?'下载全部分P并打包，请耐心等候…':'下载中，请稍候…';
    statusBar.classList.add('loading');
    showBiliError('');
    try{
      const res=await fetch('/bilibili-download',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url,mode,quality,audio_fmt:audioFmt,audio_quality:audioQuality,part})
      });
      if(!res.ok){
        const err=await res.json().catch(()=>({error:'下载失败'}));
        showBiliError(err.error||'下载失败');
        statusText.textContent='出错';
        return;
      }
      const blob=await res.blob();
      const cd=res.headers.get('Content-Disposition')||'';
      const match=cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
      const fname=match?decodeURIComponent(match[1].replace(/['"]/g,'')):(allParts?'bilibili_videos.zip':mode==='audio'?`audio.${audioFmt}`:'video.mp4');
      const blobUrl=URL.createObjectURL(blob);
      Object.assign(document.createElement('a'),{href:blobUrl,download:fname}).click();
      // 预览（zip 不预览）
      const previewWrap=$('bili-preview-wrap');
      const videoEl=$('bili-video-preview');
      const audioEl=$('bili-audio-preview');
      const previewName=$('bili-preview-name');
      if(!allParts&&blob.size>0){
        if(mode==='audio'){
          videoEl.style.display='none';
          videoEl.src='';
          audioEl.src=blobUrl;
          audioEl.style.display='block';
        }else{
          audioEl.style.display='none';
          audioEl.src='';
          videoEl.src=blobUrl;
          videoEl.style.display='block';
        }
        previewName.textContent=fname;
        previewWrap.style.display='block';
      }else{
        previewWrap.style.display='none';
      }
      statusText.textContent='✓ 下载完成';
    }catch(e){
      showBiliError('请求失败：'+e.message);
      statusText.textContent='出错';
    }finally{
      dlBtn.disabled=false;
      fetchBtn.disabled=false;
      spinner.style.display='none';
      statusBar.classList.remove('loading');
    }
  });

  urlInput.addEventListener('keydown',e=>{if(e.key==='Enter')fetchBtn.click()});

  function showBiliError(msg){
    errorMsg.textContent=msg;
    errorMsg.style.display=msg?'block':'none';
  }
})();

// ═════════════════════════════════════════════════════
// QR CODE MODULE
// ═════════════════════════════════════════════════════
(()=>{
  const scanPanel=$('qr-scan-panel'), genPanel=$('qr-gen-panel');
  const dropZone=$('qr-drop-zone'), fileInput=$('qr-file-input');
  const previewWrap=$('qr-preview-wrap'), previewImg=$('qr-preview-img');
  const fileNameEl=$('qr-file-name');
  const statusBar=$('qr-status-bar'), statusText=$('qr-status-text');
  const spinner=$('qr-spinner'), errorMsg=$('qr-error-msg');
  const placeholder=$('qr-placeholder'), placeholderText=$('qr-placeholder-text');
  const scanResult=$('qr-scan-result'), genResult=$('qr-gen-result');
  const genInput=$('qr-gen-input'), genBtn=$('qr-gen-btn');

  // mode switch
  document.querySelectorAll('input[name="qr-mode"]').forEach(r=>{
    r.addEventListener('change',()=>{
      const isScan=r.value==='scan';
      scanPanel.style.display=isScan?'':'none';
      genPanel.style.display=isScan?'none':'';
      scanResult.style.display='none';
      genResult.style.display='none';
      placeholder.style.display='flex';
      placeholderText.textContent=isScan?'上传含二维码的图片以识别':'输入内容后点击「生成二维码」';
      showQrError('');
      statusText.textContent='就绪';
    });
  });

  // dep check
  fetch('/qr-check').then(r=>r.json()).then(d=>{
    if(!d.pyzbar) console.warn('[QR] pyzbar not available — scan may fail');
    if(!d.qrcode) console.warn('[QR] qrcode not available — generate may fail');
  }).catch(()=>{});

  // ── Scan ─────────────────────────────────────────────
  dropZone.addEventListener('dragover',e=>{e.preventDefault();dropZone.classList.add('over')});
  dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('over'));
  dropZone.addEventListener('drop',e=>{
    e.preventDefault();dropZone.classList.remove('over');
    const f=e.dataTransfer.files[0];
    if(f&&f.type.startsWith('image/'))doScan(f);
  });
  fileInput.addEventListener('change',()=>{if(fileInput.files[0])doScan(fileInput.files[0])});

  async function doScan(file){
    previewImg.src=URL.createObjectURL(file);
    fileNameEl.textContent=file.name;
    previewWrap.style.display='block';
    dropZone.querySelector('.drop-icon').style.display='none';
    dropZone.querySelector('.drop-label').style.display='none';
    showQrError('');
    scanResult.style.display='none';
    placeholder.style.display='flex';
    placeholderText.textContent='识别中…';
    setLoading(true,'识别中…');
    try{
      const fd=new FormData();fd.append('file',file);
      const res=await fetch('/qr-scan',{method:'POST',body:fd});
      const data=await res.json();
      if(data.error){showQrError(data.error);placeholderText.textContent='识别失败';return}
      renderScanResults(data.results);
      statusText.textContent=`✓ 发现 ${data.results.length} 个码`;
    }catch(e){
      showQrError('请求失败：'+e.message);
      placeholderText.textContent='识别失败';
    }finally{setLoading(false,'就绪')}
  }

  function renderScanResults(results){
    placeholder.style.display='none';
    scanResult.innerHTML='';
    const card=document.createElement('div');
    card.className='qr-result-card';
    card.innerHTML=`<h3>识别结果（${results.length} 个）</h3>`;
    results.forEach((r,i)=>{
      const isUrl=/^https?:\/\//i.test(r.data);
      const dataHtml=isUrl
        ?`<a href="${escHtml(r.data)}" target="_blank" rel="noopener noreferrer">${escHtml(r.data)}</a>`
        :escHtml(r.data);
      const item=document.createElement('div');
      item.className='qr-result-item';
      item.innerHTML=`<div class="qr-type-badge">${escHtml(r.type)}</div>
        <div class="qr-data">${dataHtml}</div>
        <div class="qr-copy-row">
          <button class="btn-sm" onclick="navigator.clipboard.writeText(${JSON.stringify(r.data)}).then(()=>{this.textContent='✓ 已复制';setTimeout(()=>this.textContent='复制',1500)})">复制</button>
          ${isUrl?`<button class="btn-sm" onclick="window.open(${JSON.stringify(r.data)},'_blank')">打开链接</button>`:''}
        </div>`;
      card.appendChild(item);
    });
    scanResult.appendChild(card);
    scanResult.style.display='block';
  }

  function escHtml(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

  // ── Generate ──────────────────────────────────────────
  genBtn.addEventListener('click',doGenerate);
  genInput.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter')doGenerate()});

  async function doGenerate(){
    const text=genInput.value.trim();
    if(!text){showQrError('请输入内容');return}
    showQrError('');
    genResult.style.display='none';
    placeholder.style.display='flex';
    placeholderText.textContent='生成中…';
    setLoading(true,'生成中…');
    try{
      const res=await fetch('/qr-generate',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text})
      });
      const data=await res.json();
      if(data.error){showQrError(data.error);placeholderText.textContent='生成失败';return}
      placeholder.style.display='none';
      genResult.innerHTML=`<div class="qr-img-wrap">
        <img src="${data.image}" alt="QR Code"/>
        <div style="margin-top:12px;display:flex;gap:8px;justify-content:center">
          <button class="btn-sm" onclick="(()=>{const a=document.createElement('a');a.href='${data.image}';a.download='qrcode.png';a.click()})()">下载 PNG</button>
          <button class="btn-sm" onclick="navigator.clipboard.write([new ClipboardItem({'image/png':fetch('${data.image}').then(r=>r.blob())})]).then(()=>{this.textContent='✓ 已复制';setTimeout(()=>this.textContent='复制图片',1500)}).catch(()=>alert('浏览器不支持复制图片'))">复制图片</button>
        </div>
      </div>`;
      genResult.style.display='block';
      statusText.textContent='✓ 生成成功';
    }catch(e){
      showQrError('请求失败：'+e.message);
      placeholderText.textContent='生成失败';
    }finally{setLoading(false,'就绪')}
  }

  function setLoading(on,msg){
    if(on){statusBar.classList.add('loading');spinner.style.display='block'}
    else{statusBar.classList.remove('loading');spinner.style.display='none'}
    statusText.textContent=msg;
  }
  function showQrError(msg){
    errorMsg.textContent=msg;
    errorMsg.style.display=msg?'block':'none';
  }
})();

// ═════════════════════════════════════════════════════
// IMAGE GRABBER MODULE
// ═════════════════════════════════════════════════════
(()=>{
  const urlInput=$('imgrab-url-input');
  const fetchBtn=$('imgrab-fetch-btn');
  const statusBar=$('imgrab-status-bar');
  const statusText=$('imgrab-status-text');
  const spinner=$('imgrab-spinner');
  const errorMsg=$('imgrab-error-msg');
  const toolbar=$('imgrab-toolbar');
  const grid=$('imgrab-grid');
  const selInfo=$('imgrab-sel-info');
  const dlBtn=$('imgrab-dl-btn');
  const minSizeRange=$('imgrab-minsize');
  const minSizeVal=$('imgrab-minsize-val');
  const browserToggle=$('imgrab-browser-toggle');
  const playwrightWarn=$('imgrab-playwright-warn');

  let allImages=[];
  let minSize=50;
  let playwrightOk=false;

  // Check env on tab activation
  document.querySelector('[data-tab="imgrab"]').addEventListener('click',()=>{
    fetch('/imgrab-check').then(r=>r.json()).then(d=>{
      playwrightOk=d.playwright_ok;
      if(!d.requests_ok){
        showError('requests/beautifulsoup4 未安装，请重启应用自动安装');
      }
      updateBrowserHint();
    }).catch(()=>{});
  });

  browserToggle.addEventListener('change', updateBrowserHint);

  function updateBrowserHint(){
    if(browserToggle.checked&&!playwrightOk){
      playwrightWarn.style.display='block';
      playwrightWarn.textContent='⚠️ Playwright 未安装，开启后抓取时会提示错误';
    }else{
      playwrightWarn.style.display='none';
    }
  }

  minSizeRange.addEventListener('input',()=>{
    minSize=parseInt(minSizeRange.value);
    minSizeVal.textContent=minSize;
    applyFilter();
  });

  function applyFilter(){
    let visCount=0, selCount=0;
    allImages.forEach(item=>{
      const w=item.naturalW, h=item.naturalH;
      // show if not yet loaded (w===0), or if passes size filter
      const pass=(w===0&&h===0)||minSize===0||(w>=minSize||h>=minSize);
      item.card.style.display=pass?'':'none';
      if(pass){
        visCount++;
        if(item.check.checked) selCount++;
      }
    });
    selInfo.textContent=`已选 ${selCount} / ${visCount} 张（共解析 ${allImages.length} 个）`;
    dlBtn.disabled=selCount===0;
  }

  function updateSelInfo(){ applyFilter(); }

  fetchBtn.addEventListener('click', doFetch);
  urlInput.addEventListener('keydown',e=>{if(e.key==='Enter')doFetch()});

  async function doFetch(){
    const url=urlInput.value.trim();
    if(!url){showError('请输入网址');return}
    showError('');
    const useBrowser=browserToggle.checked;
    const modeLabel=useBrowser?'浏览器渲染模式':'普通抓取模式';
    setLoading(true,`${modeLabel}，正在获取页面…`);
    fetchBtn.disabled=true;
    allImages=[];
    // keep placeholder visible until we have results
    grid.innerHTML='<div class="imgrab-placeholder" id="imgrab-placeholder"><div class="imgrab-placeholder-icon" style="animation:spin 1s linear infinite">⏳</div><p>正在抓取，请稍候…</p></div>';
    toolbar.style.display='none';
    try{
      const res=await fetch('/img-grab',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url, browser:useBrowser})
      });
      const data=await res.json();
      if(data.error){
        showError(data.error);
        grid.innerHTML='<div class="imgrab-placeholder"><div class="imgrab-placeholder-icon">❌</div><p>抓取失败</p></div>';
        return;
      }
      if(!data.images||data.images.length===0){
        grid.innerHTML='<div class="imgrab-placeholder"><div class="imgrab-placeholder-icon">🔍</div><p>未在该页面找到图片</p><p style="font-size:.75rem;margin-top:6px;color:var(--muted)">可尝试开启「浏览器渲染」模式</p></div>';
        setLoading(false,'未找到图片');
        return;
      }
      grid.innerHTML='';
      toolbar.style.display='flex';
      renderImages(data.images);
      const methodTag=data.method==='playwright'?'[Playwright]':'[requests]';
      setLoading(false,`${methodTag} 解析到 ${data.images.length} 个图片，加载中…`);
    }catch(e){
      showError('请求失败：'+e.message);
      setLoading(false,'出错');
    }finally{
      fetchBtn.disabled=false;
    }
  }

  function renderImages(images){
    images.forEach((img)=>{
      const idx=allImages.length;
      const card=document.createElement('div');
      card.className='imgrab-card';

      const check=document.createElement('input');
      check.type='checkbox';
      check.className='imgrab-check';
      check.addEventListener('change', updateSelInfo);

      const thumbEl=document.createElement('img');
      thumbEl.className='imgrab-thumb';
      thumbEl.loading='lazy';
      thumbEl.alt=img.alt||'';

      const info=document.createElement('div');
      info.className='imgrab-card-info';
      const basename=decodeURIComponent(img.url.split('/').pop().split('?')[0])||'图片';
      info.textContent=img.alt||basename;

      const entry={url:img.url, alt:img.alt, card, check, naturalW:0, naturalH:0};
      allImages.push(entry);

      thumbEl.onload=()=>{
        entry.naturalW=thumbEl.naturalWidth;
        entry.naturalH=thumbEl.naturalHeight;
        if(entry.naturalW&&entry.naturalH){
          info.textContent=`${entry.naturalW}×${entry.naturalH}` + (img.alt?`  ${img.alt}`:'');
        }
        applyFilter();
        // Update status once all loaded
        const loaded=allImages.filter(i=>i.naturalW>0||i.naturalH>0).length;
        if(loaded===allImages.length){
          const visible=allImages.filter(i=>i.card.style.display!=='none').length;
          setLoading(false,`加载完成，显示 ${visible} 张`);
        }
      };
      thumbEl.onerror=()=>{
        entry.naturalW=-1; entry.naturalH=-1;
        card.style.opacity='.3';
        card.title='图片加载失败';
        applyFilter();
      };

      // Use proxy to bypass CORS
      thumbEl.src=`/img-proxy?url=${encodeURIComponent(img.url)}`;

      card.addEventListener('click',e=>{
        if(e.target===check)return;
        check.checked=!check.checked;
        updateSelInfo();
      });

      card.appendChild(check);
      card.appendChild(thumbEl);
      card.appendChild(info);
      grid.appendChild(card);
    });
    applyFilter();
  }

  // Toolbar buttons
  $('imgrab-sel-all').addEventListener('click',()=>{
    allImages.forEach(i=>{if(i.card.style.display!=='none')i.check.checked=true});
    updateSelInfo();
  });
  $('imgrab-sel-none').addEventListener('click',()=>{
    allImages.forEach(i=>i.check.checked=false);
    updateSelInfo();
  });
  $('imgrab-sel-invert').addEventListener('click',()=>{
    allImages.forEach(i=>{if(i.card.style.display!=='none')i.check.checked=!i.check.checked});
    updateSelInfo();
  });

  dlBtn.addEventListener('click',async()=>{
    const selected=allImages.filter(i=>i.card.style.display!=='none'&&i.check.checked);
    if(!selected.length)return;
    dlBtn.disabled=true;
    setLoading(true,`正在下载 ${selected.length} 张图片…`);
    showError('');
    try{
      const res=await fetch('/img-download-zip',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({urls:selected.map(i=>i.url)})
      });
      if(!res.ok){
        const err=await res.json().catch(()=>({error:'下载失败'}));
        showError(err.error||'下载失败');
        return;
      }
      const blob=await res.blob();
      Object.assign(document.createElement('a'),{
        href:URL.createObjectURL(blob), download:'images.zip'
      }).click();
      setLoading(false,`✓ 已下载 ${selected.length} 张图片`);
    }catch(e){
      showError('下载失败：'+e.message);
      setLoading(false,'出错');
    }finally{
      dlBtn.disabled=false;
    }
  });

  function setLoading(on, msg){
    if(on){statusBar.classList.add('loading');spinner.style.display='block'}
    else{statusBar.classList.remove('loading');spinner.style.display='none'}
    if(msg!==undefined) statusText.textContent=msg;
  }
  function showError(msg){
    errorMsg.textContent=msg;
    errorMsg.style.display=msg?'block':'none';
  }
})();

// ═════════════════════════════════════════════════════
// ARIA2 MANAGER MODULE
// ═════════════════════════════════════════════════════
(()=>{
  const powerBtn   = $('aria2-power-btn');
  const dot        = $('aria2-dot');
  const statusTxt  = $('aria2-status-text');
  const notInstall = $('aria2-not-installed-hint');
  const urlInput   = $('aria2-url-input');
  const dirInput   = $('aria2-savedir-input');
  const addBtn     = $('aria2-add-btn');
  const addMsg     = $('aria2-add-msg');
  const list       = $('aria2-list');
  const placeholder= $('aria2-placeholder');
  const countEl    = $('aria2-count');
  const refreshBtn = $('aria2-refresh-btn');
  const cfgSaveBtn     = $('aria2-cfg-save-btn');
  const cfgSaveMsg     = $('aria2-cfg-save-msg');
  const saveirdBrowse  = $('aria2-savedir-browse');
  const cfgDirBrowse   = $('cfg-save-dir-browse');

  let running = false;
  let installed = false;
  let pollTimer = null;

  // ── Directory browser ────────────────────────────────
  async function pickDir(targetInput){
    const btn = targetInput === $('aria2-savedir-input') ? saveirdBrowse : cfgDirBrowse;
    const orig = btn.textContent;
    btn.textContent = '…';
    btn.disabled = true;
    try{
      const d = await fetch('/aria2-pick-dir',{method:'POST'}).then(r=>r.json());
      if(d.path) targetInput.value = d.path;
      else if(d.error) console.warn('pick-dir error:', d.error);
    }catch(e){}
    btn.textContent = orig;
    btn.disabled = false;
  }

  saveirdBrowse.addEventListener('click', ()=> pickDir($('aria2-savedir-input')));
  cfgDirBrowse.addEventListener('click',  ()=> pickDir($('cfg-save-dir')));

  // ── Main panel tab switching ──────────────────────────
  document.querySelectorAll('.aria2-main-tab').forEach(tab=>{
    tab.addEventListener('click',()=>{
      document.querySelectorAll('.aria2-main-tab').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('.aria2-panel').forEach(p=>p.classList.remove('active'));
      tab.classList.add('active');
      $('aria2-panel-'+tab.dataset.panel).classList.add('active');
    });
  });

  // ── Config fields helpers ─────────────────────────────
  const cfgFields = {
    rpc_port:          ()=>parseInt($('cfg-rpc-port').value)||6800,
    rpc_secret:        ()=>$('cfg-rpc-secret').value.trim(),
    save_dir:          ()=>$('cfg-save-dir').value.trim(),
    allow_overwrite:   ()=>$('cfg-allow-overwrite').checked,
    continue_download: ()=>$('cfg-continue').checked,
    max_download_limit:()=>$('cfg-dl-limit').value.trim()||'0',
    max_upload_limit:  ()=>$('cfg-ul-limit').value.trim()||'0',
    max_concurrent:    ()=>parseInt($('cfg-max-concurrent').value)||5,
    split:             ()=>parseInt($('cfg-split').value)||16,
    max_conn_per_server:()=>parseInt($('cfg-max-conn').value)||16,
    min_split_size:    ()=>$('cfg-min-split').value.trim()||'1M',
    seed_time:         ()=>parseInt($('cfg-seed-time').value)||0,
    seed_ratio:        ()=>$('cfg-seed-ratio').value.trim()||'1.0',
    bt_tracker:        ()=>$('cfg-bt-tracker').value.trim(),
    all_proxy:         ()=>$('cfg-proxy').value.trim(),
  };

  function readConfig(){
    const cfg={};
    for(const[k,fn] of Object.entries(cfgFields)) cfg[k]=fn();
    return cfg;
  }

  function applyConfig(cfg){
    if(!cfg)return;
    if(cfg.rpc_port)    $('cfg-rpc-port').value=cfg.rpc_port;
    if(cfg.rpc_secret!=null) $('cfg-rpc-secret').value=cfg.rpc_secret;
    if(cfg.save_dir!=null)   $('cfg-save-dir').value=cfg.save_dir;
    $('cfg-allow-overwrite').checked=cfg.allow_overwrite!==false;
    $('cfg-continue').checked=cfg.continue_download!==false;
    if(cfg.max_download_limit!=null) $('cfg-dl-limit').value=cfg.max_download_limit;
    if(cfg.max_upload_limit!=null)   $('cfg-ul-limit').value=cfg.max_upload_limit;
    if(cfg.max_concurrent)   $('cfg-max-concurrent').value=cfg.max_concurrent;
    if(cfg.split)            $('cfg-split').value=cfg.split;
    if(cfg.max_conn_per_server) $('cfg-max-conn').value=cfg.max_conn_per_server;
    if(cfg.min_split_size)   $('cfg-min-split').value=cfg.min_split_size;
    if(cfg.seed_time!=null)  $('cfg-seed-time').value=cfg.seed_time;
    if(cfg.seed_ratio!=null) $('cfg-seed-ratio').value=cfg.seed_ratio;
    if(cfg.bt_tracker!=null) $('cfg-bt-tracker').value=cfg.bt_tracker;
    if(cfg.all_proxy!=null)  $('cfg-proxy').value=cfg.all_proxy;
  }

  // Persist config to localStorage
  function saveLocalConfig(cfg){
    try{localStorage.setItem('aria2_config',JSON.stringify(cfg));}catch(e){}
  }
  function loadLocalConfig(){
    try{return JSON.parse(localStorage.getItem('aria2_config')||'null');}catch(e){return null;}
  }

  // ── Check status when tab clicked ─────────────────────
  document.querySelector('[data-tab="aria2"]').addEventListener('click', checkStatus);

  async function checkStatus(){
    try{
      const d = await fetch('/aria2-status').then(r=>r.json());
      installed = d.installed;
      running = d.running;
      // Apply server config to UI (or fall back to localStorage)
      const serverCfg = d.config || {};
      const localCfg  = loadLocalConfig() || {};
      applyConfig({...localCfg, ...serverCfg});
      updateUI();
      if(running) loadList();
    }catch(e){}
  }

  function updateUI(){
    if(!installed){
      dot.className='aria2-status-dot off';
      statusTxt.textContent='未安装 aria2c';
      powerBtn.textContent='未安装';
      powerBtn.className='aria2-power-btn off';
      powerBtn.disabled=true;
      notInstall.style.display='block';
      addBtn.disabled=true;
      return;
    }
    notInstall.style.display='none';
    powerBtn.disabled=false;
    if(running){
      const port=$('cfg-rpc-port').value||6800;
      dot.className='aria2-status-dot on';
      statusTxt.textContent=`运行中（端口 ${port}）`;
      powerBtn.textContent='停止 Aria2';
      powerBtn.className='aria2-power-btn on';
      addBtn.disabled=false;
    }else{
      dot.className='aria2-status-dot off';
      statusTxt.textContent='未运行';
      powerBtn.textContent='启动 Aria2';
      powerBtn.className='aria2-power-btn off';
      addBtn.disabled=true;
    }
  }

  // ── Start / Stop ──────────────────────────────────────
  async function doStart(){
    statusTxt.textContent='正在启动…';
    const cfg=readConfig();
    saveLocalConfig(cfg);
    try{
      const d=await fetch('/aria2-start',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({config:cfg})
      }).then(r=>r.json());
      if(d.error){
        statusTxt.textContent='启动失败：'+d.error;
        running=false;
      }else{
        running=true;
        loadList();
        startPoll();
        // Switch to queue panel after start
        document.querySelector('.aria2-main-tab[data-panel="queue"]').click();
      }
    }catch(e){
      statusTxt.textContent='启动失败';
      running=false;
    }
  }

  powerBtn.addEventListener('click', async()=>{
    powerBtn.disabled=true;
    if(running){
      statusTxt.textContent='正在停止…';
      await fetch('/aria2-stop',{method:'POST'});
      running=false;
      stopPoll();
      list.innerHTML='';
      list.appendChild(placeholder);
      placeholder.style.display='flex';
      countEl.textContent='';
    }else{
      await doStart();
    }
    updateUI();
  });

  // ── Config save & restart ─────────────────────────────
  cfgSaveBtn.addEventListener('click', async()=>{
    const cfg=readConfig();
    saveLocalConfig(cfg);
    if(!running){
      showCfgMsg('设置已保存，下次启动生效','var(--green)');
      return;
    }
    cfgSaveBtn.disabled=true;
    showCfgMsg('重启中…','var(--muted)');
    await fetch('/aria2-stop',{method:'POST'});
    running=false;
    stopPoll();
    await doStart();
    updateUI();
    cfgSaveBtn.disabled=false;
    if(running) showCfgMsg('已重启，新配置生效','var(--green)');
    else showCfgMsg('重启失败，请检查参数','var(--danger)');
  });

  function showCfgMsg(msg,color){
    cfgSaveMsg.textContent=msg;
    cfgSaveMsg.style.color=color;
    cfgSaveMsg.style.display='block';
    setTimeout(()=>{cfgSaveMsg.style.display='none';},4000);
  }

  // ── Add download ──────────────────────────────────────
  addBtn.addEventListener('click', async()=>{
    const raw=urlInput.value.trim();
    if(!raw){showAddMsg('请输入下载链接','var(--danger)');return;}
    const urls=raw.split('\n').map(u=>u.trim()).filter(Boolean);
    showAddMsg('添加中…','var(--muted)');
    addBtn.disabled=true;
    let ok=0, fail=0;
    for(const url of urls){
      try{
        const d=await fetch('/aria2-add',{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({url,dir:dirInput.value.trim()||undefined})
        }).then(r=>r.json());
        if(d.ok) ok++;
        else fail++;
      }catch(e){fail++;}
    }
    showAddMsg(`已添加 ${ok} 个${fail?'，失败 '+fail+' 个':''}`,ok?'var(--green)':'var(--danger)');
    if(ok>0){urlInput.value='';loadList();}
    addBtn.disabled=false;
  });

  refreshBtn.addEventListener('click',()=>loadList());

  // ── Download list ─────────────────────────────────────
  async function loadList(){
    if(!running)return;
    try{
      const d=await fetch('/aria2-list').then(r=>r.json());
      if(d.error)return;
      renderList(d);
    }catch(e){}
  }

  function renderList(data){
    const all=[
      ...data.active.map(i=>({...i,_cat:'active'})),
      ...data.waiting.map(i=>({...i,_cat:'waiting'})),
      ...data.stopped.map(i=>({...i,_cat:'stopped'})),
    ];
    if(all.length===0){
      list.innerHTML='';
      list.appendChild(placeholder);
      placeholder.style.display='flex';
      placeholder.querySelector('p').textContent='暂无下载任务';
      countEl.textContent='';
      return;
    }
    placeholder.style.display='none';
    countEl.textContent=`${data.active.length} 活动 / ${all.length} 总计`;
    const frag=document.createDocumentFragment();
    all.forEach(item=>{
      const el=buildItem(item);
      frag.appendChild(el);
    });
    list.innerHTML='';
    list.appendChild(frag);
  }

  function buildItem(item){
    const total=parseInt(item.totalLength)||0;
    const done=parseInt(item.completedLength)||0;
    const pct=total>0?Math.round(done/total*100):0;
    const speed=parseInt(item.downloadSpeed)||0;
    const cat=item._cat;
    const name=getFilename(item)||item.gid;
    const barClass=cat==='active'?'active':(cat==='stopped'&&!item.errorMessage)?'complete':'error';

    const el=document.createElement('div');
    el.className='aria2-item';
    el.dataset.gid=item.gid;
    el.innerHTML=`
      <div class="aria2-item-name" title="${escH(name)}">${escH(name)}</div>
      <div class="aria2-progress-wrap"><div class="aria2-progress-bar ${barClass}" style="width:${pct}%"></div></div>
      <div class="aria2-item-meta">
        <span>${statusLabel(cat,item)}</span>
        <span>${pct}%</span>
        <span>${fmtSize(done)} / ${fmtSize(total)}</span>
        ${cat==='active'?`<span>↓ ${fmtSpeed(speed)}</span>`:''}
        ${item.errorMessage?`<span style="color:var(--danger);flex:1;overflow:hidden;text-overflow:ellipsis" title="${escH(item.errorMessage)}">${escH(item.errorMessage)}</span>`:''}
      </div>
      <div class="aria2-item-actions">
        ${cat==='active'?`<button class="aria2-act-btn" data-act="pause">暂停</button>`:''}
        ${cat==='waiting'?`<button class="aria2-act-btn" data-act="resume">继续</button>`:''}
        <button class="aria2-act-btn danger" data-act="remove">删除</button>
      </div>`;
    el.querySelectorAll('[data-act]').forEach(btn=>{
      btn.addEventListener('click',()=>itemAction(item.gid,btn.dataset.act));
    });
    return el;
  }

  async function itemAction(gid,act){
    const ep=act==='remove'?'/aria2-remove':act==='pause'?'/aria2-pause':'/aria2-resume';
    await fetch(ep,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gid})});
    loadList();
  }

  function startPoll(){
    if(pollTimer)return;
    pollTimer=setInterval(()=>{if(running)loadList();},3000);
  }
  function stopPoll(){clearInterval(pollTimer);pollTimer=null;}

  function statusLabel(cat,item){
    if(cat==='active')return '⬇ 下载中';
    if(cat==='waiting')return '⏸ 等待';
    if(item.errorMessage)return '❌ 错误';
    return '✅ 完成';
  }

  function getFilename(item){
    try{
      const f=item.files&&item.files[0];
      if(!f)return '';
      const p=f.path||f.uris?.[0]?.uri||'';
      return p.split(/[/\\]/).pop()||p;
    }catch(e){return '';}
  }

  function fmtSize(bytes){
    if(!bytes||bytes<0)return '0 B';
    const u=['B','KB','MB','GB'];
    let i=0;
    while(bytes>=1024&&i<u.length-1){bytes/=1024;i++;}
    return bytes.toFixed(i>0?1:0)+' '+u[i];
  }
  function fmtSpeed(bps){return fmtSize(bps)+'/s'}
  function escH(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
  function showAddMsg(msg,color){
    addMsg.textContent=msg;addMsg.style.color=color;addMsg.style.display='block';
    setTimeout(()=>{addMsg.style.display='none';},4000);
  }

  // Init: load local config first, then check server
  const localCfg=loadLocalConfig();
  if(localCfg) applyConfig(localCfg);
  checkStatus().then(()=>{if(running)startPoll();});
})();

// ═════════════════════════════════════════════════════

// ── Bing Wallpaper ────────────────────────────────────
(function(){
  const grid       = $('bing-grid');
  const placeholder= $('bing-placeholder');
  const fetchBtn   = $('bing-fetch-btn');
  const statusEl   = $('bing-status');
  const mktSel     = $('bing-mkt');
  const countSel   = $('bing-count');
  const lb         = $('bing-lb');
  const lbImg      = $('bing-lb-img');
  const lbTitle    = $('bing-lb-title');
  const lbCopy     = $('bing-lb-copy');
  const lbDl       = $('bing-lb-dl');
  const lbCopyUrl  = $('bing-lb-copy-url');
  const lbClose    = $('bing-lb-close');

  let currentUrl = '';

  fetchBtn.addEventListener('click', fetchWallpapers);

  function fetchWallpapers() {
    fetchBtn.disabled = true;
    fetchBtn.textContent = '加载中…';
    statusEl.textContent = '';
    const mkt   = mktSel.value;
    const n     = countSel.value;
    fetch(`/bing-wallpaper?n=${n}&mkt=${encodeURIComponent(mkt)}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) { statusEl.textContent = '错误：' + data.error; return; }
        renderCards(data.images || []);
        statusEl.textContent = `已加载 ${(data.images||[]).length} 张`;
      })
      .catch(err => { statusEl.textContent = '请求失败：' + err; })
      .finally(() => { fetchBtn.disabled = false; fetchBtn.textContent = '获取壁纸'; });
  }

  function renderCards(images) {
    // clear everything except placeholder
    Array.from(grid.children).forEach(el => { if(el !== placeholder) el.remove(); });
    if (!images.length) { placeholder.style.display = 'flex'; return; }
    placeholder.style.display = 'none';
    images.forEach(img => {
      const card = document.createElement('div');
      card.className = 'bing-card';
      const dateStr = img.date ? img.date.replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3') : '';
      card.innerHTML =
        `<a class="bing-thumb-link" href="${escAttr(img.url)}" target="_blank">` +
          `<img class="bing-thumb" src="${escAttr(img.thumb)}" alt="${escAttr(img.title)}" loading="lazy"/>` +
        `</a>` +
        `<div class="bing-card-body">` +
          `<div class="bing-card-title">${escH(img.title || '(无标题)')}</div>` +
          `<div class="bing-card-copy">${escH(img.copyright || '')}</div>` +
          `<div class="bing-card-date">${dateStr}</div>` +
          `<div class="bing-card-actions">` +
            `<button class="convert-btn" style="flex:1;padding:5px;font-size:.75rem" data-action="preview">预览</button>` +
            `<button class="btn-sm" data-action="dl">下载</button>` +
          `</div>` +
        `</div>`;
      card.querySelector('[data-action="preview"]').addEventListener('click', () => openLb(img));
      card.querySelector('[data-action="dl"]').addEventListener('click', () => downloadImg(img.url, img.title));
      card.querySelector('.bing-thumb-link').addEventListener('click', e => {
        if (e.ctrlKey) return; // 让浏览器原生处理：在新标签页打开原图
        e.preventDefault();
        openLb(img);
      });
      grid.appendChild(card);
    });
  }

  function openLb(img) {
    currentUrl = img.url;
    lbImg.src = img.url;
    lbTitle.textContent = img.title || '';
    lbCopy.textContent  = img.copyright || '';
    lb.classList.add('open');
  }

  lbClose.addEventListener('click', () => lb.classList.remove('open'));
  lb.addEventListener('click', e => { if (e.target === lb) lb.classList.remove('open'); });

  lbDl.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = currentUrl;
    a.download = (lbTitle.textContent || 'bing-wallpaper') + '.jpg';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  });

  lbCopyUrl.addEventListener('click', () => {
    navigator.clipboard.writeText(currentUrl).then(() => {
      const old = lbCopyUrl.textContent;
      lbCopyUrl.textContent = '已复制！';
      setTimeout(() => { lbCopyUrl.textContent = old; }, 1500);
    });
  });

  function downloadImg(url, title) {
    const a = document.createElement('a');
    a.href = url;
    a.download = (title || 'bing-wallpaper') + '.jpg';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function escH(s)    { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function escAttr(s) { return String(s).replace(/"/g,'&quot;'); }
})();
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def _open_when_ready(url: str, port: int) -> None:
    """Wait until Flask is accepting connections, then open the browser."""
    import socket, time
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    webbrowser.open(url)


if __name__ == "__main__":
    PORT = 8899
    url  = f"http://localhost:{PORT}"
    print(f"ASCII Art  ->  {url}")
    threading.Thread(target=_open_when_ready, args=(url, PORT), daemon=True).start()
    app.run(port=PORT, debug=False)
