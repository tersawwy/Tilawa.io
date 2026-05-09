"""
Tests for modules/scene_manager.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.scene_manager import build_scenes, load_scenes, _detect_phrase_boundaries


def _make_words(entries):
    """
    Build minimal word dicts from list of (start, end) tuples.
    display_start/display_end equal start/end for simplicity in these tests.
    """
    return [
        {
            "ayah_number": 1,
            "word_index": i,
            "text": f"w{i}",
            "english_text": "",
            "start": s,
            "end": e,
            "display_start": s,
            "display_end": e,
        }
        for i, (s, e) in enumerate(entries)
    ]


FAKE_CLIPS = ["/fake/clip_a.mp4", "/fake/clip_b.mp4", "/fake/clip_c.mp4"]


def test_phrase_boundaries_no_gaps():
    """No inter-word gaps → one scene covering all words."""
    words = _make_words([(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 3.0)])
    boundaries = _detect_phrase_boundaries(words, gap_threshold=0.4, min_scene_duration=2.5)
    assert len(boundaries) == 1
    assert boundaries[0][0] == 0.0
    assert abs(boundaries[0][1] - 3.0) < 0.01


def test_phrase_boundaries_cuts_on_large_gap():
    """A gap > 0.4s should trigger a cut (if resulting scenes meet min duration)."""
    # 0-3s phrase, 1.0s gap, 4-7s phrase
    words = _make_words([(0.0, 0.5), (0.5, 3.0), (4.0, 5.0), (5.0, 7.0)])
    boundaries = _detect_phrase_boundaries(words, gap_threshold=0.4, min_scene_duration=2.5)
    assert len(boundaries) == 2, f"Expected 2 scenes, got {len(boundaries)}: {boundaries}"
    assert boundaries[0][1] < 4.0  # cut happens in the gap
    assert boundaries[1][0] < 4.0


def test_short_scenes_merged():
    """Scenes shorter than min_scene_duration are merged into the previous."""
    # Three phrases: 0-2.5s, 0.5s gap, 3-3.2s (short!), 0.5s gap, 4-7s
    words = _make_words([
        (0.0, 2.5),    # phrase 1 end
        (3.0, 3.2),    # short phrase (0.2s)
        (4.0, 7.0),    # phrase 3
    ])
    boundaries = _detect_phrase_boundaries(words, gap_threshold=0.4, min_scene_duration=2.5)
    # The 0.2s phrase is too short → merged with previous or next
    for start, end in boundaries:
        assert (end - start) >= 2.5 or (start, end) == boundaries[-1], (
            f"Scene {start:.2f}-{end:.2f} is shorter than 2.5s"
        )


def test_build_scenes_total_coverage():
    """All word indices appear in exactly one scene."""
    words = _make_words([(0.0, 0.5), (0.5, 3.0), (4.0, 5.0), (5.0, 7.0)])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        scenes_path = tf.name

    try:
        scenes = build_scenes(words, FAKE_CLIPS, scenes_path,
                              gap_threshold=0.4, min_scene_duration=2.0)
        all_indices = sorted(wi for s in scenes for wi in s.word_indices)
        assert all_indices == list(range(len(words))), (
            f"Not all words covered: got {all_indices}"
        )
    finally:
        os.unlink(scenes_path)


def test_build_scenes_no_overlap():
    """Scene time ranges must not overlap."""
    words = _make_words([(0.0, 0.5), (0.5, 3.0), (4.0, 5.0), (5.0, 7.0)])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        scenes_path = tf.name

    try:
        scenes = build_scenes(words, FAKE_CLIPS, scenes_path)
        for i in range(len(scenes) - 1):
            assert scenes[i].scene_end <= scenes[i + 1].scene_start, (
                f"Scenes {i} and {i+1} overlap"
            )
    finally:
        os.unlink(scenes_path)


def test_no_consecutive_same_clip():
    """No two consecutive scenes should share the same clip (when >1 clip available)."""
    # Need enough words to produce at least 3 scenes
    words = _make_words([
        (0.0, 3.0), (4.0, 7.0), (8.0, 11.0), (12.0, 15.0)
    ])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        scenes_path = tf.name

    try:
        scenes = build_scenes(words, FAKE_CLIPS, scenes_path,
                              gap_threshold=0.4, min_scene_duration=2.5)
        for i in range(len(scenes) - 1):
            if len(FAKE_CLIPS) > 1:
                assert scenes[i].clip_path != scenes[i + 1].clip_path, (
                    f"Scenes {i} and {i+1} use the same clip: {scenes[i].clip_path}"
                )
    finally:
        os.unlink(scenes_path)


def test_scenes_json_round_trip():
    """build_scenes writes JSON; load_scenes reads it back identically."""
    words = _make_words([(0.0, 3.0), (4.0, 7.0)])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        scenes_path = tf.name

    try:
        original = build_scenes(words, FAKE_CLIPS, scenes_path)
        loaded   = load_scenes(scenes_path)
        assert len(original) == len(loaded)
        for o, l in zip(original, loaded):
            assert o.clip_path    == l.clip_path
            assert o.scene_start  == l.scene_start
            assert o.scene_end    == l.scene_end
            assert o.word_indices == l.word_indices
    finally:
        os.unlink(scenes_path)


def test_minimum_one_scene():
    """Even with no gaps, at least one scene is produced."""
    words = _make_words([(0.0, 3.5)])
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        scenes_path = tf.name

    try:
        scenes = build_scenes(words, FAKE_CLIPS[:1], scenes_path)
        assert len(scenes) >= 1
        assert scenes[0].word_indices == [0]
    finally:
        os.unlink(scenes_path)


if __name__ == "__main__":
    tests = [
        test_phrase_boundaries_no_gaps,
        test_phrase_boundaries_cuts_on_large_gap,
        test_short_scenes_merged,
        test_build_scenes_total_coverage,
        test_build_scenes_no_overlap,
        test_no_consecutive_same_clip,
        test_scenes_json_round_trip,
        test_minimum_one_scene,
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
