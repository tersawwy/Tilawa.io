"""
Module 3 — Video Renderer
Composes the final 9:16 TikTok video:
  - Nature background (blurred + darkened)
  - Playwright-rendered Arabic text (browser handles OpenType shaping → perfect Uthmani font)
  - Word-by-word gold highlight via CSS <span> classes
  - English translation (bottom, fade per ayah)
  - Subtle audio waveform bar
  - Vignette overlay
"""

import base64
import io
import json
import os
import textwrap
from pathlib import Path
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


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
  .ayah {{
    font-family: 'QuranicFont', 'Amiri Quran', 'Noto Naskh Arabic', serif;
    font-size: {font_size}px;
    color: #F0EAD6;
    text-align: center;
    direction: rtl;
    unicode-bidi: plaintext;
    line-height: 2.5;
    padding: 28px 56px;
    width: 100%;
    word-spacing: 10px;
    font-weight: 600;
    -webkit-text-stroke: 1.2px #F0EAD6;
    font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
    text-shadow:
      2px 0 0 rgba(0, 0, 0, 0.95),
      -2px 0 0 rgba(0, 0, 0, 0.95),
      0 2px 0 rgba(0, 0, 0, 0.95),
      0 -2px 0 rgba(0, 0, 0, 0.95),
      0 3px 12px rgba(0, 0, 0, 0.95),
      0 0 24px rgba(0, 0, 0, 0.65);
  }}
  .aya-num {{
    font-size: 0.75em;
    margin-right: 6px;
    -webkit-text-stroke: 0.8px #F0EAD6;
  }}
</style>
</head>
<body>
  <div class="ayah" id="text">PLACEHOLDER</div>
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
) -> str:
    """
    Render the final video and save to output_path.

    Args:
        style:  'mushaf' (full ayah per screen) or 'word-pop' (one word at a time).
        clips:  For word-pop mode, list of B-roll clip paths (from fetch_cinematic_clips).
                In mushaf mode a single background_path is used.

    Returns the absolute path of the output file.
    """
    from moviepy.editor import AudioFileClip, VideoClip, VideoFileClip

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    vc = config["video"]
    FPS      = vc["fps"]
    DURATION = vc["duration"]

    # Resolve canvas dimensions from output.aspect
    aspect = config.get("output", {}).get("aspect", "portrait")
    if aspect == "landscape":
        W, H = 1920, 1080
    else:
        W, H = vc.get("width", 1080), vc.get("height", 1920)

    # Load timed words
    with open(timed_words_path, "r", encoding="utf-8") as f:
        timed_words: List[dict] = json.load(f)

    # Load fonts (for English subtitle only — Arabic via Playwright)
    english_font = _load_english_font(config)

    # Pre-render all Arabic text PNGs via Playwright
    print(f"    Pre-rendering Arabic text frames via browser (mode={style})...")
    arabic_cache = _prerender_arabic(timed_words, config, W, mode=style)
    print(f"    Pre-rendered {len(arabic_cache)} unique Arabic state(s).")

    # Precompute waveform amplitudes (one value per frame) — mushaf mode only
    waveform_amps = _compute_waveform(audio_path, FPS, DURATION, config)

    # Build vignette overlay (static)
    vignette = _make_vignette(W, H)

    brightness = config["background"]["brightness"]

    print(f"    Rendering {int(DURATION * FPS)} frames at {W}×{H} (style={style})...")

    # ── Dispatch to appropriate make_frame factory ──────────────

    if style == "word-pop":
        make_frame = _make_frame_word_pop(
            timed_words, arabic_cache, english_font,
            clips or [background_path],
            vignette, config, W, H, FPS, DURATION,
        )
    else:
        # Mushaf: single looping background
        bg_clip = _prepare_background(background_path, W, H, DURATION, config)

        def make_frame(t: float) -> np.ndarray:
            bg_frame = bg_clip.get_frame(t)
            frame    = Image.fromarray(bg_frame).resize((W, H), Image.LANCZOS)

            dark = Image.new("RGB", (W, H), (0, 0, 0))
            frame = Image.blend(frame, dark, 1.0 - brightness)

            draw = ImageDraw.Draw(frame)

            ayah_num = _get_active_ayah(timed_words, t)
            if ayah_num is not None and ayah_num in arabic_cache:
                arabic_img = arabic_cache[ayah_num]
                x = (W - arabic_img.width) // 2
                y = config["arabic"]["position_y"] - arabic_img.height // 2
                frame.paste(arabic_img, (x, max(0, y)), arabic_img)

            _draw_english(draw, timed_words, t, english_font, config, W, H)

            if config["waveform"]["enabled"]:
                fi = min(int(t * FPS), len(waveform_amps) - 1)
                _draw_waveform(draw, waveform_amps[fi], config, W, H)

            frame.paste(vignette, (0, 0), vignette)
            return np.array(frame)

    # ── Render (identical for both modes) ────────────────────────
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


# ─────────────────────────────────────────────────────────────
# Arabic Pre-rendering via Playwright
# ─────────────────────────────────────────────────────────────

def _arabic_indic(n: int) -> str:
    """Convert an integer to Arabic-Indic digit string (e.g. 41 → '٤١')."""
    digits = "٠١٢٣٤٥٦٧٨٩"
    return "".join(digits[int(d)] for d in str(n))


def _prerender_arabic(timed_words: List[dict], config: dict, W: int, mode: str = "mushaf") -> dict:
    """
    Pre-render Arabic text to RGBA PNGs via Playwright/HarfBuzz.

    mode='mushaf'   — one PNG per ayah (full verse + rosette).
                      Returns dict[ayah_number → PIL RGBA Image].

    mode='word-pop' — one PNG per word (single word, larger font, no rosette).
                      Returns dict[(ayah_number, word_index) → PIL RGBA Image].

    Uses Playwright with the Quranic font loaded in-browser so the HarfBuzz
    OpenType engine handles all Uthmani ligatures and harakat correctly — NOT Pillow.
    """
    from playwright.sync_api import sync_playwright

    ac = config["arabic"]
    font_path = ac["font"]
    font_size = ac["size"]

    if mode == "word-pop":
        # Scale up font for single-word display — one word must fill the frame
        wp_cfg = config.get("render", {}).get("word_pop", {})
        font_scale = config.get("render", {}).get("word_pop", {}).get("font_scale_landscape", 1.0)
        # Portrait word-pop uses ~1.4× the mushaf font size for impact
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

        if mode == "mushaf":
            # Group words by ayah and render full verses
            ayah_groups: dict = {}
            for w in timed_words:
                ayah_groups.setdefault(w["ayah_number"], []).append(w)

            for ayah_num, words in ayah_groups.items():
                verse_text = " ".join(w["text"] for w in words)
                rosette = f'۝{_arabic_indic(ayah_num)}'
                body = f'{verse_text} <span class="aya-num">{rosette}</span>'
                _set_and_wait(html_template.replace("PLACEHOLDER", body))

                try:
                    png_bytes = page.locator("#text").screenshot(omit_background=True)
                    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                except Exception as e:
                    print(f"    Warning: Playwright screenshot failed for ayah {ayah_num}: {e}")
                    img = Image.new("RGBA", (W, 200), (0, 0, 0, 0))

                cache[ayah_num] = img

        else:  # word-pop — one PNG per word
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


def _get_active_ayah(timed_words: List[dict], t: float):
    """Return the ayah_number currently being recited at time t, or None."""
    active_ayah = None
    for w in timed_words:
        if w["start"] <= t:
            active_ayah = w["ayah_number"]
        else:
            break
    return active_ayah


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
    english_font,
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
    frame is sampled, the active word PNG is composited with fade alpha,
    and the English translation strip is drawn at reduced opacity.
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
    eng_alpha_scale = float(wp_cfg.get("english_opacity", 0.6))

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

        # ── English translation — reduced opacity ───────────────
        _draw_english(draw, timed_words, t, english_font, config, W, H,
                      alpha_scale=eng_alpha_scale)

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
# Background Preparation
# ─────────────────────────────────────────────────────────────

def _prepare_background(path: str, W: int, H: int, duration: float, _config: dict):
    from moviepy.editor import VideoFileClip, concatenate_videoclips

    clip = VideoFileClip(path, audio=False)

    if clip.duration < duration:
        loops = int(duration / clip.duration) + 1
        clip = concatenate_videoclips([clip] * loops)

    clip = clip.subclip(0, duration)

    target_ratio = W / H
    clip_ratio   = clip.w / clip.h

    if clip_ratio > target_ratio:
        new_w = int(clip.h * target_ratio)
        x_center = clip.w // 2
        clip = clip.crop(x1=x_center - new_w // 2, x2=x_center + new_w // 2)
    else:
        new_h = int(clip.w / target_ratio)
        y_center = clip.h // 2
        clip = clip.crop(y1=y_center - new_h // 2, y2=y_center + new_h // 2)

    return clip.resize((W, H))


# ─────────────────────────────────────────────────────────────
# English Subtitle Rendering
# ─────────────────────────────────────────────────────────────

def _load_english_font(config: dict):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(config["english"]["font"], config["english"]["size"])
    except (IOError, OSError):
        print(f"    Warning: English font not found. Using default.")
        return ImageFont.load_default()


def _draw_english(
    draw: ImageDraw.Draw,
    timed_words: List[dict],
    t: float,
    font,
    config: dict,
    W: int,
    _H: int,
    alpha_scale: float = 1.0,
):
    ec = config["english"]

    # Find active ayah
    active_ayah = None
    ayah_start = 0.0
    for w in timed_words:
        if w["start"] <= t:
            if active_ayah != w["ayah_number"]:
                active_ayah = w["ayah_number"]
                ayah_start = w["start"]
        else:
            break

    if active_ayah is None:
        return

    # Get translation text
    text = ""
    for w in timed_words:
        if w["ayah_number"] == active_ayah:
            text = w.get("english_text", "")
            break
    if not text:
        return

    # Fade in over 0.4s from ayah start
    fade_duration = 0.4
    alpha = min(1.0, (t - ayah_start) / fade_duration)
    if alpha <= 0:
        return

    # Measure and wrap
    sample_bb = font.getbbox("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    avg_char_w = (sample_bb[2] - sample_bb[0]) / 52
    max_chars = max(20, int((W - 80) / avg_char_w))
    wrapped = textwrap.fill(text, width=max_chars)

    alpha = alpha * alpha_scale
    shadow_color = _hex_to_rgba("#000000", alpha=int(160 * alpha))
    text_color   = _hex_to_rgba(ec["color"],  alpha=int(255 * alpha))

    y = ec["position_y"]
    for line in wrapped.split("\n"):
        bbox = font.getbbox(line)
        text_w = bbox[2] - bbox[0]
        x = (W - text_w) // 2
        draw.text((x + 1, y + 1), line, font=font, fill=shadow_color)
        draw.text((x, y),         line, font=font, fill=text_color)
        y += (bbox[3] - bbox[1]) + 8


# ─────────────────────────────────────────────────────────────
# Waveform Visualizer
# ─────────────────────────────────────────────────────────────

def _compute_waveform(audio_path: str, fps: int, duration: float, _config: dict) -> List[float]:
    try:
        from moviepy.editor import AudioFileClip
        clip = AudioFileClip(audio_path).subclip(0, duration)
        samples = clip.to_soundarray(fps=fps * 10)
        n_frames = int(duration * fps)
        samples_per_frame = len(samples) // max(n_frames, 1)
        amps = []
        for i in range(n_frames):
            chunk = samples[i * samples_per_frame: (i + 1) * samples_per_frame]
            if len(chunk) == 0:
                amps.append(0.0)
            else:
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                amps.append(min(rms * 6, 1.0))
        return amps
    except Exception:
        return [0.5] * int(duration * fps)


def _draw_waveform(draw: ImageDraw.Draw, amplitude: float, config: dict, W: int, _H: int):
    wc = config["waveform"]
    bar_color = _hex_to_rgb(wc["color"])
    bar_max_h = wc["height"]
    center_y  = wc["position_y"]
    bar_h     = max(4, int(bar_max_h * amplitude))
    num_bars  = 40
    bar_w     = 6
    bar_gap   = 8
    total_bar_width = num_bars * (bar_w + bar_gap) - bar_gap
    start_x   = (W - total_bar_width) // 2

    for i in range(num_bars):
        factor = 0.4 + 0.6 * abs(np.sin(i * np.pi / num_bars))
        h  = max(4, int(bar_h * factor))
        x  = start_x + i * (bar_w + bar_gap)
        y1 = center_y - h // 2
        y2 = center_y + h // 2
        alpha = int(180 + 75 * amplitude)
        draw.rectangle([x, y1, x + bar_w, y2], fill=(*bar_color, alpha))


# ─────────────────────────────────────────────────────────────
# Vignette Overlay
# ─────────────────────────────────────────────────────────────

def _make_vignette(W: int, H: int) -> Image.Image:
    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pixels   = vignette.load()
    cx, cy   = W / 2, H / 2
    max_dist = (cx ** 2 + cy ** 2) ** 0.5

    for y in range(H):
        for x in range(W):
            dist  = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            norm  = dist / max_dist
            alpha = int(max(0, (norm - 0.55) / 0.45) * 200)
            pixels[x, y] = (0, 0, 0, alpha)

    return vignette


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    r, g, b = _hex_to_rgb(hex_color)
    return (r, g, b, alpha)
