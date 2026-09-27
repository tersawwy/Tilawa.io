"""
Ayah text layer for the mushaf renderer.

- Renders each ayah (or page of a long ayah) once, as a tightly-cropped
  transparent PNG, with the browser (HarfBuzz shapes the Uthmani font
  correctly — Pillow cannot).
- Turns the timed words into display intervals (when each ayah image is on
  screen), handling repeats, short breaths and long pauses.
- Builds the single ffmpeg filter graph that composes background, vignette,
  surah title and the ayah images (fade + gentle upward drift) with the audio.
"""

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from modules.backgrounds import encoder_args


# ─────────────────────────────────────────────────────────────
# Look
# ─────────────────────────────────────────────────────────────

DEFAULT_TEXT = {
    "color": "#F4EEDD",        # warm ivory
    "accent": "#D8B46A",       # muted gold (ayah marker, surah title)
    "max_size": 108,
    "min_size": 64,
    "line_height": 2.05,
    "zone_center": 0.45,       # vertical centre of the text zone (fraction of H)
    "zone_height": 0.44,       # max text block height (fraction of H)
    "zone_width": 0.83,        # max text block width (fraction of W)
    "title_y": 0.14,           # surah title centre (fraction of H), below TikTok's top UI
}


def _font_uri(font_path: str) -> str:
    data = base64.b64encode(Path(font_path).resolve().read_bytes()).decode("ascii")
    mime = "font/otf" if font_path.lower().endswith(".otf") else "font/ttf"
    return f"data:{mime};base64,{data}"


def _template(font_path: str, text: dict, W: int) -> str:
    return f"""<!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><style>
@font-face {{ font-family: 'Q'; src: url('{_font_uri(font_path)}'); font-display: block; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ background: transparent; width: {W}px; }}
.wrap {{ display: flex; justify-content: center; padding: 60px 0; }}
#ayah {{
  font-family: 'Q', serif;
  width: {int(W * text['zone_width'])}px;
  padding: 40px 56px;                 /* room for the soft glow */
  color: {text['color']};
  text-align: center; direction: rtl;
  line-height: {text['line_height']};
  word-spacing: 4px;
  font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.35),
    0 6px 28px rgba(0, 0, 0, 0.55),
    0 0 46px rgba(255, 214, 150, 0.14);
}}
.nw {{ white-space: nowrap; }}
.mark {{ color: {text['accent']}; font-size: 0.82em; padding-right: 0.15em; white-space: nowrap; }}
#title {{
  font-family: 'Q', serif; color: {text['accent']};
  display: inline-flex; align-items: center; gap: 26px;
  font-size: 50px; padding: 20px 40px; letter-spacing: 1px;
  text-shadow: 0 2px 18px rgba(0, 0, 0, 0.6);
}}
#title .rule {{ width: 120px; height: 1.5px; }}
#title .rule.r {{ background: linear-gradient(90deg, transparent, {text['accent']}); }}
#title .rule.l {{ background: linear-gradient(90deg, {text['accent']}, transparent); }}
#title .dot {{ font-size: 20px; opacity: 0.8; }}
</style></head><body>
<div class="wrap"><div id="ayah"></div></div>
<div class="wrap"><div id="title"></div></div>
<script>
window.setAyah = (html, px) => {{
  const el = document.getElementById('ayah');
  el.innerHTML = html; el.style.fontSize = px + 'px';
  return el.getBoundingClientRect().height;
}};
</script></body></html>"""


def arabic_indic(n: int) -> str:
    return "".join("٠١٢٣٤٥٦٧٨٩"[int(d)] for d in str(n))


def _ayah_html(words: Sequence[str], ayah: int, with_mark: bool) -> str:
    if not with_mark or not words:
        return " ".join(words)
    # keep the ayah marker glued to the last word so it never wraps alone
    tail = (f'<span class="nw">{words[-1]} '
            f'<span class="mark">۝{arabic_indic(ayah)}</span></span>')
    return " ".join(list(words[:-1]) + [tail])


# ─────────────────────────────────────────────────────────────
# Rendering ayah images (with auto-fit and pagination)
# ─────────────────────────────────────────────────────────────

@dataclass
class TextImage:
    path: str
    w: int
    h: int


def render_text_images(
    ayah_words: Dict[int, List[dict]],
    font_path: str,
    W: int,
    H: int,
    out_dir: str,
    text: Optional[dict] = None,
    surah_title: Optional[str] = None,
) -> Tuple[Dict[Tuple[int, int], TextImage], Dict[int, List[int]], Optional[TextImage]]:
    """Render every ayah as a cropped transparent PNG.

    Font size shrinks step-wise from max_size to min_size until the ayah fits
    the text zone; an ayah still too tall at min_size is split into pages at
    word boundaries. Returns ({(ayah, page): image}, {ayah: [first word_index
    of each page]}, title image)."""
    from playwright.sync_api import sync_playwright

    text = {**DEFAULT_TEXT, **(text or {})}
    max_h = text["zone_height"] * H + 80   # +padding around the glyphs
    os.makedirs(out_dir, exist_ok=True)
    images: Dict[Tuple[int, int], TextImage] = {}
    pages: Dict[int, List[int]] = {}
    title_img = None

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        page.set_content(_template(font_path, text, W))
        page.evaluate("() => document.fonts.ready")

        def height(html: str, px: int) -> float:
            return page.evaluate("([h, px]) => setAyah(h, px)", [html, px])

        def shoot(key: Tuple[int, int]) -> TextImage:
            path = os.path.join(out_dir, f"ayah_{key[0]}_{key[1]}.png")
            page.locator("#ayah").screenshot(path=path, omit_background=True)
            box = page.locator("#ayah").bounding_box()
            return TextImage(path, int(round(box["width"])), int(round(box["height"])))

        sizes = list(range(text["max_size"], text["min_size"] - 1, -4))
        for ayah, words in ayah_words.items():
            texts = [w["text"] for w in words]
            full = _ayah_html(texts, ayah, ayah > 0)   # basmala (0): no marker
            px = next((s for s in sizes if height(full, s) <= max_h), None)
            if px is not None:
                height(full, px)
                images[(ayah, 0)] = shoot((ayah, 0))
                pages[ayah] = [words[0]["word_index"]]
                continue

            # Too long even at the smallest size: greedy split into pages,
            # then even out the page lengths so no page is a lonely word.
            px = text["min_size"]
            chunks, cur = [], []
            for i, t in enumerate(texts):
                if cur and height(_ayah_html(texts[cur[0]:i + 1], ayah, ayah > 0 and i == len(texts) - 1), px) > max_h:
                    chunks.append(cur)
                    cur = []
                cur.append(i)
            chunks.append(cur)
            per = -(-len(texts) // len(chunks))
            bounds = list(range(0, len(texts), per))
            pages[ayah] = [words[b]["word_index"] for b in bounds]
            for k, b in enumerate(bounds):
                seg = texts[b: b + per]
                last = b + per >= len(texts)
                height(_ayah_html(seg, ayah, ayah > 0 and last), px)
                images[(ayah, k)] = shoot((ayah, k))

        if surah_title:
            page.evaluate(
                """(t) => { document.getElementById('title').innerHTML =
                   '<span class="rule r"></span><span class="dot">◆</span>' + t +
                   '<span class="dot">◆</span><span class="rule l"></span>'; }""",
                surah_title,
            )
            path = os.path.join(out_dir, "title.png")
            page.locator("#title").screenshot(path=path, omit_background=True)
            box = page.locator("#title").bounding_box()
            title_img = TextImage(path, int(round(box["width"])), int(round(box["height"])))

        browser.close()
    return images, pages, title_img


# ─────────────────────────────────────────────────────────────
# Timeline — when each ayah image is on screen
# ─────────────────────────────────────────────────────────────

@dataclass
class Interval:
    key: Tuple[int, int]   # (ayah, page)
    start: float           # fade-in begins
    end: float             # fade-out complete


def page_of(word_index: int, starts: Sequence[int]) -> int:
    k = 0
    for i, s in enumerate(starts):
        if word_index >= s:
            k = i
    return k


def build_intervals(
    timed_words: Sequence[dict],
    pages: Dict[int, List[int]],
    duration: float,
    lead: float = 0.35,
    max_gap: float = 4.0,
    hold: float = 1.6,
    overlap: float = 0.15,
) -> List[Interval]:
    """Display intervals for the ayah images.

    Consecutive words showing the same image form one run (a repeat of the
    ayah just shown keeps it on screen — no flicker). Each run appears
    ``lead`` s before its first word. It stays until the next run starts
    (a short dissolve: the old ayah is nearly gone as the new one rises —
    two ayahs never sit on top of each other) when the gap is a breath (≤ max_gap);
    after a longer pause it holds ``hold`` s past its last word, then fades,
    and the next run fades back in when recitation resumes."""
    runs: List[List] = []  # [key, first_start, last_end]
    for w in timed_words:
        key = (w["ayah_number"], page_of(w["word_index"], pages.get(w["ayah_number"], [0])))
        if runs and runs[-1][0] == key and w["start"] - runs[-1][2] <= max_gap:
            runs[-1][2] = max(runs[-1][2], w["end"])
        else:
            runs.append([key, w["start"], w["end"]])

    out: List[Interval] = []
    for i, (key, s, e) in enumerate(runs):
        start = max(0.0, s - lead)
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        if nxt is not None and nxt[1] - e <= max_gap:
            end = max(nxt[1] - lead + overlap, e)
        else:
            end = e + hold
        out.append(Interval(key, round(start, 3), round(min(end, duration), 3)))
    return out


# ─────────────────────────────────────────────────────────────
# Compose — ffmpeg command
# ─────────────────────────────────────────────────────────────

def build_compose_cmd(
    bg_path: str,
    overlay_path: str,
    audio_path: str,
    images: Dict[Tuple[int, int], TextImage],
    intervals: Sequence[Interval],
    title: Optional[TextImage],
    out_path: str,
    W: int,
    H: int,
    fps: int,
    duration: float,
    text: Optional[dict] = None,
    fade_in: float = 0.8,
    fade_out: float = 0.4,
    drift: int = 16,
) -> List[str]:
    """One ffmpeg invocation: background → vignette → title → each ayah
    interval (trimmed to its own time span, so only the frames where it is
    visible are processed) → audio."""
    text = {**DEFAULT_TEXT, **(text or {})}
    cy = int(text["zone_center"] * H)

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", bg_path,
           "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", overlay_path]
    idx = 2
    title_idx = None
    if title is not None:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", title.path]
        title_idx, idx = idx, idx + 1

    used = [k for k in images if any(iv.key == k for iv in intervals)]
    img_idx = {}
    for k in used:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", images[k].path]
        img_idx[k], idx = idx, idx + 1
    audio_idx = idx
    cmd += ["-i", audio_path]

    f: List[str] = [
        f"[0:v]scale={W}:{H},setsar=1,fps={fps},trim=duration={duration:.3f}[bg]",
        "[bg][1:v]overlay=0:0:format=auto[b0]",
    ]
    last = "b0"
    if title_idx is not None and intervals:
        t0 = intervals[0].start
        f.append(f"[{title_idx}:v]format=rgba,fade=t=in:st={t0:.3f}:d=1.5:alpha=1[title]")
        f.append(f"[{last}][title]overlay=x=(W-w)/2:y={int(text['title_y'] * H)}-h/2:format=auto[bt]")
        last = "bt"

    # split each image stream by how many intervals use it
    uses: Dict[Tuple[int, int], List[int]] = {}
    for i, iv in enumerate(intervals):
        uses.setdefault(iv.key, []).append(i)
    label: Dict[int, str] = {}
    for k, ivs in uses.items():
        if k not in img_idx:
            continue
        if len(ivs) == 1:
            label[ivs[0]] = f"{img_idx[k]}:v"
        else:
            outs = "".join(f"[s{i}]" for i in ivs)
            f.append(f"[{img_idx[k]}:v]split={len(ivs)}{outs}")
            for i in ivs:
                label[i] = f"s{i}"

    for i, iv in enumerate(intervals):
        if i not in label:
            continue
        d = max(iv.end - iv.start, 0.1)
        fi = min(fade_in, d / 2)
        fo = min(fade_out, d / 2)
        img = images[iv.key]
        f.append(
            f"[{label[i]}]format=rgba,trim=duration={d:.3f},setpts=PTS-STARTPTS+{iv.start:.3f}/TB,"
            f"fade=t=in:st={iv.start:.3f}:d={fi:.3f}:alpha=1,"
            f"fade=t=out:st={iv.end - fo:.3f}:d={fo:.3f}:alpha=1[a{i}]"
        )
        # gentle upward drift while fading in (ease-out)
        y = f"{cy - img.h // 2}+{drift}*pow(max(0\\,1-(t-{iv.start:.3f})/{fi * 1.4:.3f})\\,2)"
        f.append(f"[{last}][a{i}]overlay=x=(W-w)/2:y='{y}':eof_action=pass:format=auto[o{i}]")
        last = f"o{i}"

    f.append(f"[{last}]format=yuv420p[v]")
    cmd += [
        "-filter_complex", ";".join(f),
        "-map", "[v]", "-map", f"{audio_idx}:a",
        "-t", f"{duration:.3f}",
        *encoder_args("final"),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        out_path,
    ]
    return cmd


def make_overlay(W: int, H: int, path: str, text: Optional[dict] = None) -> str:
    """Static vignette + soft scrim behind the text zone (numpy, instant)."""
    import numpy as np
    from PIL import Image

    text = {**DEFAULT_TEXT, **(text or {})}
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nx, ny = (xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2)
    vig = np.clip((np.sqrt(nx ** 2 * 0.9 + ny ** 2 * 0.55) - 0.55) / 0.6, 0, 1) ** 1.6 * 0.75
    zc = text["zone_center"] * H
    zh = text["zone_height"] * H
    scrim = np.exp(-(((yy - zc) / (zh * 0.75)) ** 2)) * 0.28
    alpha = np.clip(1 - (1 - vig) * (1 - scrim), 0, 1)
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(path)
    return path
