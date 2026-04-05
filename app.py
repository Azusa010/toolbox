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

try:
    # audioop-lts provides the audioop module removed in Python 3.13+
    import audioop  # noqa: F401
except ModuleNotFoundError:
    try:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "audioop-lts", "-q"])
        import audioop  # noqa: F401
    except Exception:
        pass

try:
    from pydub import AudioSegment
    PYDUB_OK = True
except ImportError:
    try:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "pydub", "audioop-lts", "-q"])
        from pydub import AudioSegment
        PYDUB_OK = True
    except Exception:
        PYDUB_OK = False

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
    if not PYDUB_OK:
        return jsonify({"ok": False, "reason": "pydub 未安装"})
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return jsonify({"ok": False, "reason": "未检测到 ffmpeg，请先安装 ffmpeg 并添加到 PATH"})
    return jsonify({"ok": True})


@app.route("/audio-convert", methods=["POST"])
def audio_convert():
    if not PYDUB_OK:
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

        return jsonify({
            "title":       info.get("title", "未知标题"),
            "duration":    duration,
            "uploader":    uploader,
            "thumbnail":   thumbnail,
            "is_playlist": is_playlist,
            "parts":       parts,
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
    </nav>
  </div>
  <span class="badge">v2.3</span>
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
      <div id="bili-info-panel" style="display:none;width:100%;max-width:520px">
        <div class="bili-info-card">
          <img id="bili-thumb" class="bili-thumb" src="" alt="封面" style="display:none"/>
          <div class="bili-title" id="bili-title"></div>
          <div class="bili-meta" id="bili-meta"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

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

  function showInfo(info){
    placeholder.style.display='none';
    infoPanel.style.display='block';
    $('bili-title').textContent=info.title||'未知标题';
    const dur=info.duration?formatDur(info.duration):'';
    const partCount=info.is_playlist?`${info.parts.length} P`:'';
    $('bili-meta').textContent=[info.uploader,partCount,dur].filter(Boolean).join(' · ');
    const thumb=$('bili-thumb');
    if(info.thumbnail){thumb.src=info.thumbnail;thumb.style.display='block'}
    else{thumb.style.display='none'}
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
      Object.assign(document.createElement('a'),{href:URL.createObjectURL(blob),download:fname}).click();
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
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PORT = 8899
    url  = f"http://localhost:{PORT}"
    print(f"ASCII Art  ->  {url}")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(port=PORT, debug=False)
