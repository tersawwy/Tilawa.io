"""
Tests for _compute_display_windows() in modules/sync.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.sync import _compute_display_windows


def _make_words(starts_ends):
    """Build minimal timed-word dicts from (start, end) tuples."""
    return [
        {
            "ayah_number": 1,
            "word_index": i,
            "text": f"word{i}",
            "english_text": "",
            "start": s,
            "end": e,
        }
        for i, (s, e) in enumerate(starts_ends)
    ]


def test_basic_window_fill():
    """display_end of word N == display_start of word N+1 (gaps are filled)."""
    words = _make_words([(0.0, 0.5), (0.8, 1.3), (1.6, 2.0)])
    result = _compute_display_windows(words, min_word_display=0.0)
    assert len(result) == 3
    # Word 0: display_end should equal word 1's start (0.8), not word 0's end (0.5)
    assert result[0]["display_end"] == result[1]["display_start"], (
        "Gap between word 0 and word 1 should be filled"
    )
    assert result[1]["display_end"] == result[2]["display_start"], (
        "Gap between word 1 and word 2 should be filled"
    )
    # Last word: display_end == word.end
    assert result[-1]["display_end"] == result[-1]["end"]


def test_last_word_display_end():
    """Last word's display_end equals its own acoustic end."""
    words = _make_words([(0.0, 0.4), (0.5, 0.9)])
    result = _compute_display_windows(words, min_word_display=0.0)
    assert result[-1]["display_end"] == 0.9


def test_no_blank_frames():
    """Sum of all display windows ≈ total audio duration (no gaps)."""
    words = _make_words([(0.0, 0.3), (0.6, 0.9), (1.2, 1.8)])
    result = _compute_display_windows(words, min_word_display=0.0)
    total = sum(w["display_end"] - w["display_start"] for w in result)
    audio_dur = words[-1]["end"] - words[0]["start"]
    assert abs(total - audio_dur) < 0.01, (
        f"Display coverage {total:.3f}s != audio duration {audio_dur:.3f}s"
    )


def test_short_word_merged():
    """A word shorter than min_word_display is merged into the next word."""
    # Word 1 has a 0.05s display window (below 0.18 threshold)
    words = _make_words([(0.0, 0.4), (0.4, 0.45), (0.45, 1.0)])
    result = _compute_display_windows(words, min_word_display=0.18)
    # Short word should be merged: result has 2 entries, not 3
    assert len(result) == 2, f"Expected 2 after merge, got {len(result)}"
    # Merged word's text should contain both originals
    merged_texts = result[1]["text"]
    assert "word1" in merged_texts and "word2" in merged_texts, (
        f"Merged word text should contain both words: {merged_texts!r}"
    )
    # Merged word's display_start should be the short word's display_start
    assert result[1]["display_start"] == result[0]["display_end"]


def test_minimum_display_invariant():
    """After windowing + merging, no display window is shorter than min_word_display."""
    min_t = 0.18
    words = _make_words([(0.0, 0.1), (0.12, 0.15), (0.15, 0.9), (0.9, 1.5)])
    result = _compute_display_windows(words, min_word_display=min_t)
    for w in result:
        dur = w["display_end"] - w["display_start"]
        assert dur >= min_t or w is result[-1], (
            f"Word at index {result.index(w)} has duration {dur:.3f}s < {min_t}s"
        )


def test_empty_input():
    """Empty list returns empty list without error."""
    assert _compute_display_windows([]) == []


def test_single_word():
    """Single word: display_start == start, display_end == end."""
    words = _make_words([(1.0, 2.5)])
    result = _compute_display_windows(words)
    assert len(result) == 1
    assert result[0]["display_start"] == 1.0
    assert result[0]["display_end"]   == 2.5


def test_original_start_end_unchanged():
    """The original start/end fields must not be modified (mushaf mode relies on them)."""
    words = _make_words([(0.0, 0.4), (0.8, 1.2)])
    result = _compute_display_windows(words, min_word_display=0.0)
    assert result[0]["start"] == 0.0 and result[0]["end"] == 0.4
    assert result[1]["start"] == 0.8 and result[1]["end"] == 1.2


if __name__ == "__main__":
    tests = [
        test_basic_window_fill,
        test_last_word_display_end,
        test_no_blank_frames,
        test_short_word_merged,
        test_minimum_display_invariant,
        test_empty_input,
        test_single_word,
        test_original_start_end_unchanged,
    ]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗  {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ✗  {fn.__name__}: UNEXPECTED {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
