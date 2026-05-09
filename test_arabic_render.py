#!/usr/bin/env python3
"""
Quick offline test — renders a single Pillow frame with Arabic text.
Run this BEFORE the full pipeline to verify fonts and Arabic shaping work.

Usage:
    python test_arabic_render.py
Output:
    output/test_arabic_frame.png
"""

import os
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

os.makedirs("output", exist_ok=True)

# Test verse: Al-Fatiha 1:1
ARABIC = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
ENGLISH = "In the name of Allah, the Entirely Merciful, the Especially Merciful."

W, H = 1080, 1920
img = Image.new("RGB", (W, H), color=(10, 10, 30))  # dark navy bg
draw = ImageDraw.Draw(img)

# ── Load fonts (falls back to default if not installed) ──────
ARABIC_FONT_PATH = "assets/fonts/KFGQPC-Uthmanic-HAFS.otf"
ENGLISH_FONT_PATH = "assets/fonts/Montserrat-Regular.ttf"

try:
    arabic_font = ImageFont.truetype(ARABIC_FONT_PATH, 72)
    print(f"Arabic font loaded: {ARABIC_FONT_PATH}")
except (IOError, OSError):
    arabic_font = ImageFont.load_default()
    print(f"WARNING: Arabic font not found at '{ARABIC_FONT_PATH}'. Using default.")
    print("Download from: https://fonts.qurancomplex.gov.sa/")

try:
    english_font = ImageFont.truetype(ENGLISH_FONT_PATH, 38)
    print(f"English font loaded: {ENGLISH_FONT_PATH}")
except (IOError, OSError):
    english_font = ImageFont.load_default()
    print(f"WARNING: English font not found at '{ENGLISH_FONT_PATH}'. Using default.")
    print("Download Montserrat from: https://fonts.google.com/specimen/Montserrat")

# ── Reshape & render Arabic ───────────────────────────────────
reshaped = arabic_reshaper.reshape(ARABIC)
bidi_text = get_display(reshaped)

bbox = arabic_font.getbbox(bidi_text)
text_w = bbox[2] - bbox[0]
text_h = bbox[3] - bbox[1]
x = (W - text_w) // 2
y = 800

# Draw glow (gold shadow offset)
for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
    draw.text((x + dx, y + dy), bidi_text, font=arabic_font, fill=(245, 197, 24, 100))

# Draw main Arabic text (white)
draw.text((x, y), bidi_text, font=arabic_font, fill="#FFFFFF")

# ── English translation ───────────────────────────────────────
import textwrap
wrapped = textwrap.fill(ENGLISH, width=40)
ey = 1550
for line in wrapped.split("\n"):
    ebbox = english_font.getbbox(line)
    ew = ebbox[2] - ebbox[0]
    draw.text(((W - ew) // 2, ey), line, font=english_font, fill="#E0E0E0")
    ey += (ebbox[3] - ebbox[1]) + 8

# ── Save ─────────────────────────────────────────────────────
out_path = "output/test_arabic_frame.png"
img.save(out_path)
print(f"\nTest frame saved to: {out_path}")
print("Open it and verify:")
print("  - Arabic letters are connected (not isolated)")
print("  - Text reads right-to-left")
print("  - Vowel marks (harakat) are visible")
