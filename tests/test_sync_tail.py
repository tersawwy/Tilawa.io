"""Tests for _drop_crammed_tail() in modules/sync.py."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.sync import _drop_crammed_tail


def _make_run(ayah, start, dur, n_words=3):
    """Build a contiguous run of n words for one ayah, evenly spaced."""
    per = dur / n_words
    return [
        {
            "ayah_number": ayah, "word_index": i, "text": f"a{ayah}w{i}",
            "english_text": "", "start": start + i * per, "end": start + (i + 1) * per,
        }
        for i in range(n_words)
    ]


def test_drop_crammed_tail_removes_millisecond_words():
    """Words squeezed into <0.12s at the very end are inaudible — drop them."""
    words = _make_run(13, 49.6, 7.0, n_words=4)
    words += [
        {"ayah_number": 13, "word_index": 4, "text": "x",
         "english_text": "", "start": 56.66, "end": 56.70},
        {"ayah_number": 13, "word_index": 5, "text": "y",
         "english_text": "", "start": 56.70, "end": 56.74},
    ]
    out = _drop_crammed_tail(words)
    assert len(out) == 4
    assert out[-1]["word_index"] == 3


def test_drop_crammed_tail_keeps_normal_words():
    words = _make_run(1, 0.0, 5.0, n_words=4)
    assert _drop_crammed_tail(words) == words
