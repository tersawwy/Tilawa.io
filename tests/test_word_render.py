"""
Tests for word-pop Arabic word PNG rendering via Playwright.
Renders a set of difficult Uthmani words and writes PNGs to tests/output/ for
manual visual inspection (correct harakat positioning, ligatures, etc.)

Run:
    python tests/test_word_render.py
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# Hard cases for Uthmani font rendering:
# hamza-on-yaa, lam-alif, compound lam-lam-ha (jalalah), madd letters
SAMPLE_WORDS = [
    ("basmala_word1",    "بِسْمِ"),
    ("lam_alif",         "اللَّهِ"),
    ("jalalah",          "ٱللَّهِ"),
    ("hamza_on_yaa",     "يَأْتِيَ"),
    ("madd_alif",        "آمَنُوا"),
    ("fatiha_alhamd",    "ٱلْحَمْدُ"),
    ("fatiha_rabb",      "رَبِّ"),
    ("shadda_kasra",     "ٱلرَّحِيمِ"),
    ("sukoon_lam",       "مَلِكِ"),
    ("mixed_harakat",    "إِيَّاكَ"),
]


def test_word_render_visual():
    """
    Render 10 difficult Uthmani words via Playwright and save to tests/output/.
    This is a visual test — inspect the PNGs manually to verify:
      - Harakat (diacritics) are correctly positioned above/below consonants
      - Lam-alif ligature renders as a single connected glyph
      - Jalalah (ﷲ) special glyph renders intact
      - No chopped ascenders or descenders
    """
    import yaml
    from PIL import Image

    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Inject word-pop mode settings
    config.setdefault("render", {}).setdefault("word_pop", {})
    config.setdefault("output", {})["aspect"] = "portrait"

    W = config["video"]["width"]

    from modules.renderer import _prerender_arabic

    # Fake a minimal timed_words list
    fake_words = [
        {
            "ayah_number": 1,
            "word_index": i,
            "text": text,
            "english_text": "",
            "start": float(i),
            "end": float(i) + 0.9,
            "display_start": float(i),
            "display_end": float(i) + 0.9,
        }
        for i, (_, text) in enumerate(SAMPLE_WORDS)
    ]

    print("  Rendering words via Playwright (this takes ~15s)...")
    cache = _prerender_arabic(fake_words, config, W, mode="word-pop")

    errors = []
    for i, (label, text) in enumerate(SAMPLE_WORDS):
        key = (1, i)
        img = cache.get(key)
        if img is None:
            errors.append(f"No PNG for '{text}' (key={key})")
            continue

        assert img.mode == "RGBA", f"Expected RGBA, got {img.mode} for {label}"

        # Image must have non-transparent pixels (the word is visible)
        pixels = list(img.getdata())
        non_transparent = sum(1 for (_, _, _, a) in pixels if a > 10)
        assert non_transparent > 100, (
            f"Word '{text}' ({label}) rendered with too few visible pixels: {non_transparent}"
        )

        out_path = os.path.join(OUTPUT_DIR, f"word_{i:02d}_{label}.png")
        img.save(out_path)
        print(f"    ✓ {label:20s}  {img.width}×{img.height}px → {os.path.basename(out_path)}")

    if errors:
        for e in errors:
            print(f"    ✗ {e}")
        raise AssertionError(f"{len(errors)} word(s) failed to render")

    print(f"\n  All {len(SAMPLE_WORDS)} words rendered. "
          f"Inspect {OUTPUT_DIR}/ for visual quality.")


if __name__ == "__main__":
    try:
        test_word_render_visual()
        print("\n✓ test_word_render_visual PASSED")
    except Exception as e:
        print(f"\n✗ test_word_render_visual FAILED: {e}")
        sys.exit(1)
