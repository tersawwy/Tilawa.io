"""
Module 3 — Video Renderer

mushaf (default): a moving background (the sheikh's own video, softly
  blurred — or a calm ambient scene) with each ayah fading in as a whole,
  composed in a single ffmpeg pass. See modules/backgrounds.py and
  modules/ayah_layer.py.
word-pop (legacy): one word at a time over B-roll scenes, frame-by-frame
  with MoviePy.
"""

import base64
import io
import json
import os
from pathlib import Path
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw


# ─────────────────────────────────────────────────────────────
# HTML Template for Arabic text rendering
# ─────────────────────────────────────────────────────────────

def _build_html_template(font_path: str, font_size: int, canvas_w: int) -> str:
    """
    Build the HTML template used by Playwright to render Arabic text.
    The browser handles OpenType shaping (HarfBuzz) — renders any Quranic font correctly.

    The font is embedded as a base64 data URI so it loads regardless of Chromium's
    file:// cross-origin restrictions (which block file:// resource loading when the
    page is created via set_content rather than navigated to from a file:// URL).
    """
    font_bytes = Path(font_path).resolve().read_bytes()
    font_b64   = base64.b64encode(font_bytes).decode("ascii")
    ext        = Path(font_path).suffix.lower()
    mime       = "font/otf" if ext == ".otf" else "font/ttf"
    font_uri   = f"data:{mime};base64,{font_b64}"

    return f"""<!DOCTYPE html>
<html dir="rtl">
<head>
<meta charset="UTF-8">
<style>
  @font-face {{
    font-family: 'QuranicFont';
    src: url('{font_uri}');
    font-display: block;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: transparent;
    width: {canvas_w}px;
    overflow: hidden;
  }}
  .panel {{
    display: inline-block;
    background: rgba(10, 16, 24, 0.38);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 28px;
    padding: 32px 48px;
  }}
  .ayah {{
    font-family: 'QuranicFont', 'Amiri Quran', 'Noto Naskh Arabic', serif;
    font-size: {font_size}px;
    color: #F5F0E4;
    text-align: center;
    direction: rtl;
    unicode-bidi: plaintext;
    line-height: 2.3;
    word-spacing: 4px;
    font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
    text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
  }}
  .aya-num {{
    font-size: 0.7em;
    margin-right: 6px;
    color: #E8C87A;
  }}
</style>
</head>
<body>
  <div class="panel" id="text-panel"><div class="ayah" id="text">PLACEHOLDER</div></div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────────────────────

def render_video(
    background_path: str,
    audio_path: str,
    timed_words_path: str,
    config: dict,
    output_path: str,
    style: str = "mushaf",
    clips: list = None,
    background_mode: str = "ambient",
    video_path: str = None,
) -> str:
    """
    Render the final video and save to output_path.

    Args:
        style:           'mushaf' (whole ayah per screen) or 'word-pop' (legacy).
        clips:           word-pop only — B-roll clip paths.
        background_mode: mushaf only — 'video' (sheikh's footage) or 'ambient'.
        video_path:      mushaf + 'video' — the downloaded source video.

    Returns the absolute path of the output file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    vc = config["video"]
    FPS      = vc["fps"]
    DURATION = vc["duration"]

    aspect = config.get("output", {}).get("aspect", "portrait")
    if aspect == "landscape":
        W, H = 1920, 1080
    else:
        W, H = vc.get("width", 1080), vc.get("height", 1920)

    with open(timed_words_path, "r", encoding="utf-8") as f:
        timed_words: List[dict] = json.load(f)

    if style != "word-pop":
        return _render_mushaf(
            timed_words, audio_path, config, output_path, W, H, FPS, DURATION,
            background_mode, video_path,
        )

    from moviepy.editor import AudioFileClip, VideoClip

    print("    Pre-rendering Arabic words via browser (mode=word-pop)...")
    arabic_cache = _prerender_arabic(timed_words, config, W, mode=style)
    print(f"    Pre-rendered {len(arabic_cache)} unique Arabic state(s).")

    vignette = _make_vignette(W, H)
    print(f"    Rendering {int(DURATION * FPS)} frames at {W}×{H} (style={style})...")
    make_frame = _make_frame_word_pop(
        timed_words, arabic_cache,
        clips or [background_path],
        vignette, config, W, H, FPS, DURATION,
    )

    video = VideoClip(make_frame, duration=DURATION)
    audio = AudioFileClip(audio_path).subclip(0, DURATION)
    video = video.set_audio(audio)

    print(f"    Exporting to {output_path}...")
    video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        ffmpeg_params=["-crf", "23"],
        logger=None,
    )

    return os.path.abspath(output_path)


def _surah_title(surah) -> str:
    if not surah:
        return ""
    try:
        from modules.sync import get_surah_name

        name = get_surah_name(int(surah))[0].strip()
    except Exception:
        return ""
    return name if name.startswith("سورة") else f"سورة {name}"


def _render_mushaf(
    timed_words: List[dict], audio_path: str, config: dict, output_path: str,
    W: int, H: int, FPS: int, DURATION: float, background_mode: str, video_path: str,
) -> str:
    import subprocess

    from modules.ayah_layer import (
        build_compose_cmd, build_intervals, make_overlay, render_text_images,
    )
    from modules.backgrounds import build_background

    surah = None
    if os.path.exists("tmp/sync_meta.json"):
        with open("tmp/sync_meta.json", encoding="utf-8") as f:
            surah = json.load(f).get("surah")

    bg_path = build_background(
        background_mode, "tmp/bg.mp4", DURATION, W, H, FPS, audio_path, config,
        video_path=video_path, surah=surah,
        recitation_start=timed_words[0]["start"] if timed_words else None,
    )

    text_cfg = config.get("render", {}).get("text", {}) or {}
    print("    Rendering ayah text (browser shaping)...")
    images, pages, title = render_text_images(
        _unique_ayah_words(timed_words), config["arabic"]["font"], W, H, "tmp/text",
        text=text_cfg, surah_title=_surah_title(surah) or None,
    )
    intervals = build_intervals(timed_words, pages, DURATION)
    overlay = make_overlay(W, H, "tmp/overlay.png", text_cfg)

    print(f"    Composing {len(intervals)} ayah appearance(s) over the background...")
    cmd = build_compose_cmd(
        bg_path, overlay, audio_path, images, intervals, title, output_path,
        W, H, FPS, DURATION, text=text_cfg,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg compose failed:\n{result.stderr[-2000:]}")
    return os.path.abspath(output_path)


# ─────────────────────────────────────────────────────────────
# Arabic Pre-rendering via Playwright
# ─────────────────────────────────────────────────────────────

def _prerender_arabic(timed_words: List[dict], config: dict, W: int, mode: str = "word-pop") -> dict:
    """
    Pre-render each word (word-pop) to an RGBA PNG via Playwright/HarfBuzz.
    Returns dict[(ayah_number, word_index) → PIL RGBA Image].

    Uses Playwright with the Quranic font loaded in-browser so the HarfBuzz
    OpenType engine handles all Uthmani ligatures and harakat correctly — NOT Pillow.
    """
    from playwright.sync_api import sync_playwright

    ac = config["arabic"]
    font_path = ac["font"]
    font_size = ac["size"]

    # Scale up font for single-word display — one word must fill the frame
    font_scale = config.get("render", {}).get("word_pop", {}).get("font_scale_landscape", 1.0)
    aspect = config.get("output", {}).get("aspect", "portrait")
    if aspect == "landscape":
        font_size = int(font_size * font_scale * 1.4)
    else:
        font_size = int(font_size * 1.4)

    html_template = _build_html_template(font_path, font_size, W)
    cache = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": 600})

        # Font is embedded as base64 so it's available immediately, but we still
        # wait for document.fonts.ready on the first set_content to be safe.
        _font_ready_waited = False

        def _set_and_wait(html: str):
            nonlocal _font_ready_waited
            page.set_content(html)
            if not _font_ready_waited:
                # Wait for the embedded font to finish parsing (once per browser session)
                page.evaluate("() => document.fonts.ready")
                _font_ready_waited = True

        for w in timed_words:
            key = (w["ayah_number"], w["word_index"])
            if key in cache:
                continue  # already rendered (handles merged words gracefully)

            _set_and_wait(html_template.replace("PLACEHOLDER", w["text"]))

            try:
                png_bytes = page.locator("#text").screenshot(omit_background=True)
                img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
            except Exception as e:
                print(f"    Warning: Playwright screenshot failed for word {key}: {e}")
                img = Image.new("RGBA", (W, 200), (0, 0, 0, 0))

            cache[key] = img

        browser.close()

    return cache


def _unique_ayah_words(timed_words: List[dict]) -> dict:
    """ayah_number → that ayah's words once each, in order.

    Repeated recitations put the same words in timed_words several times;
    the on-screen verse must still show the ayah text only once."""
    ayahs: dict = {}
    for w in timed_words:
        ayahs.setdefault(w["ayah_number"], {}).setdefault(w["word_index"], w)
    return {a: [ws[i] for i in sorted(ws)] for a, ws in ayahs.items()}


def _get_active_word_with_alpha(
    timed_words: List[dict],
    t: float,
    config: dict,
) -> Tuple[object, float]:
    """
    Return the cache key and fade alpha for the word currently displayed at time t.

    Uses display_start/display_end windows (filled by _compute_display_windows).
    Returns (None, 0.0) if no word is active.

    alpha ramps 0→1 in the first fade_duration seconds after display_start,
    and 1→0 in the last fade_duration seconds before display_end.
    """
    wp  = config.get("render", {}).get("word_pop", {})
    fade = float(wp.get("fade_duration", 0.08))

    for w in timed_words:
        ds = w.get("display_start", w["start"])
        de = w.get("display_end",   w["end"])
        if ds <= t < de:
            elapsed   = t - ds
            remaining = de - t
            dur       = de - ds
            if dur <= 0:
                return None, 0.0
            if elapsed < fade:
                alpha = elapsed / fade
            elif remaining < fade:
                alpha = remaining / fade
            else:
                alpha = 1.0
            key = (w["ayah_number"], w["word_index"])
            return key, min(1.0, max(0.0, alpha))

    return None, 0.0


def _make_frame_word_pop(
    timed_words: List[dict],
    arabic_cache: dict,
    clip_paths: list,
    vignette: "Image.Image",
    config: dict,
    W: int,
    H: int,
    FPS: int,
    DURATION: float,
):
    """
    Factory that returns a make_frame(t) closure for word-pop render mode.

    Scene backgrounds are loaded once upfront and cached per clip_path.
    Within make_frame(t), the active scene is looked up, the correct background
    frame is sampled, and the active word PNG is composited with fade alpha.
    """
    from moviepy.editor import VideoFileClip, concatenate_videoclips
    from modules.scene_manager import build_scenes, load_scenes

    brightness = config["background"]["brightness"]
    wp_cfg     = config.get("render", {}).get("word_pop", {})
    aspect     = config.get("output", {}).get("aspect", "portrait")

    y_ratio = (
        wp_cfg.get("word_y_ratio_portrait", 0.45)
        if aspect == "portrait"
        else wp_cfg.get("word_y_ratio_landscape", 0.50)
    )

    # Build or reload scenes
    scenes_path = "tmp/scenes.json"
    if not os.path.exists(scenes_path):
        build_scenes(timed_words, clip_paths, scenes_path)
    scenes = load_scenes(scenes_path)

    # Pre-load + center-crop each unique clip
    clip_cache: dict = {}
    for scene in scenes:
        cp = scene.clip_path
        if cp not in clip_cache:
            raw = VideoFileClip(cp, audio=False)
            # Loop to cover max scene duration
            max_scene_dur = max(s.duration() for s in scenes if s.clip_path == cp)
            if raw.duration < max_scene_dur:
                loops = int(max_scene_dur / raw.duration) + 1
                raw   = concatenate_videoclips([raw] * loops)
            # Center-crop to target aspect
            target_ratio = W / H
            clip_ratio   = raw.w / raw.h
            if clip_ratio > target_ratio:
                nw = int(raw.h * target_ratio)
                cx = raw.w // 2
                raw = raw.crop(x1=cx - nw // 2, x2=cx + nw // 2)
            else:
                nh = int(raw.w / target_ratio)
                cy = raw.h // 2
                raw = raw.crop(y1=cy - nh // 2, y2=cy + nh // 2)
            clip_cache[cp] = raw.resize((W, H))

    def make_frame(t: float) -> np.ndarray:
        # ── Background from active scene ────────────────────────
        scene = _get_active_scene(scenes, t)
        if scene is not None:
            sc   = clip_cache[scene.clip_path]
            t_in = (t - scene.scene_start) % sc.duration
            bg_frame = sc.get_frame(t_in)
        else:
            bg_frame = np.zeros((H, W, 3), dtype=np.uint8)

        frame = Image.fromarray(bg_frame).resize((W, H), Image.LANCZOS)
        dark  = Image.new("RGB", (W, H), (0, 0, 0))
        frame = Image.blend(frame, dark, 1.0 - brightness)

        draw = ImageDraw.Draw(frame)

        # ── Arabic word with fade alpha ─────────────────────────
        word_key, alpha = _get_active_word_with_alpha(timed_words, t, config)
        if word_key is not None and word_key in arabic_cache and alpha > 0:
            word_img = arabic_cache[word_key]
            cx = (W - word_img.width) // 2
            cy = int(H * y_ratio) - word_img.height // 2

            if alpha < 1.0:
                r, g, b, a = word_img.split()
                a = a.point(lambda x: int(x * alpha))
                faded = Image.merge("RGBA", (r, g, b, a))
                frame.paste(faded, (cx, max(0, cy)), faded)
            else:
                frame.paste(word_img, (cx, max(0, cy)), word_img)

        # ── Vignette ────────────────────────────────────────────
        frame.paste(vignette, (0, 0), vignette)
        return np.array(frame)

    return make_frame


def _get_active_scene(scenes: list, t: float):
    """Return the Scene whose time range covers t, or None."""
    for scene in scenes:
        if scene.scene_start <= t < scene.scene_end:
            return scene
    # Edge: t exactly at the final scene_end
    if scenes and t >= scenes[-1].scene_end:
        return scenes[-1]
    return None


# ─────────────────────────────────────────────────────────────
# Vignette Overlay (word-pop)
# ─────────────────────────────────────────────────────────────

def _make_vignette(W: int, H: int) -> Image.Image:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    norm = np.sqrt((xx - W / 2) ** 2 + (yy - H / 2) ** 2) / np.sqrt((W / 2) ** 2 + (H / 2) ** 2)
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[..., 3] = (np.clip((norm - 0.55) / 0.45, 0, 1) * 200).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")
