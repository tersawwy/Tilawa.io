"""Mushaf renderer timeline: repeats, breaths, long pauses, pages, compose graph."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.ayah_layer import (
    Interval,
    TextImage,
    _ayah_html,
    build_compose_cmd,
    build_intervals,
    page_of,
)
from modules.renderer import _unique_ayah_words


def _w(ayah, idx, start, end, seg=0):
    return {"ayah_number": ayah, "word_index": idx, "text": f"a{ayah}w{idx}",
            "start": start, "end": end, "segment_id": seg}


# ayah 7 recited, its last two words repeated (partial repeat), a long pause,
# then ayah 8
WORDS = [
    _w(7, 0, 1.0, 1.5), _w(7, 1, 1.5, 2.0), _w(7, 2, 2.0, 2.5),
    _w(7, 1, 3.0, 3.5, seg=1), _w(7, 2, 3.5, 4.0, seg=1),
    _w(8, 0, 10.0, 10.5, seg=1), _w(8, 1, 10.5, 11.0, seg=1),
]
PAGES = {7: [0], 8: [0]}


def test_repeated_ayah_text_appears_once():
    groups = _unique_ayah_words(WORDS)
    assert [w["word_index"] for w in groups[7]] == [0, 1, 2]
    assert [w["word_index"] for w in groups[8]] == [0, 1]


def test_repeat_keeps_ayah_on_screen_without_flicker():
    iv = build_intervals(WORDS, PAGES, duration=20.0)
    assert [i.key for i in iv] == [(7, 0), (8, 0)]
    assert iv[0].start == 0.65                      # appears just before the first word


def test_long_pause_fades_out_then_back_in():
    iv = build_intervals(WORDS, PAGES, duration=20.0)
    assert iv[0].end < iv[1].start                  # screen clears during the 6 s pause
    assert iv[0].end == 4.0 + 1.6                   # held a moment after the last word


def test_breath_between_ayahs_is_a_short_dissolve():
    words = [_w(1, 0, 0.0, 1.0), _w(1, 1, 1.0, 2.0), _w(2, 0, 3.0, 4.0)]
    iv = build_intervals(words, {1: [0], 2: [0]}, duration=10.0)
    assert iv[0].end > iv[1].start                  # slight overlap: no empty flash
    assert iv[0].end - iv[1].start <= 0.2           # but never two ayahs at full opacity


def test_repeat_after_other_ayah_gets_a_new_interval():
    words = [_w(1, 0, 0.0, 1.0), _w(2, 0, 1.5, 2.5), _w(1, 0, 3.0, 4.0)]
    iv = build_intervals(words, {1: [0], 2: [0]}, duration=10.0)
    assert [i.key for i in iv] == [(1, 0), (2, 0), (1, 0)]


def test_pages_follow_word_index():
    assert page_of(0, [0, 12, 24]) == 0
    assert page_of(13, [0, 12, 24]) == 1
    assert page_of(30, [0, 12, 24]) == 2
    words = [_w(255, i, i * 1.0, i * 1.0 + 0.9) for i in range(20)]
    iv = build_intervals(words, {255: [0, 10]}, duration=30.0)
    assert [i.key for i in iv] == [(255, 0), (255, 1)]


def test_ayah_marker_is_glued_to_last_word():
    html = _ayah_html(["بسم", "الله"], 1, True)
    assert html.startswith("بسم ")
    assert '<span class="nw">الله <span class="mark">۝١</span></span>' in html
    assert _ayah_html(["بسم", "الله"], 1, False) == "بسم الله"


def test_compose_cmd_splits_reused_image_and_maps_audio():
    images = {(1, 0): TextImage("a1.png", 800, 300), (2, 0): TextImage("a2.png", 800, 300)}
    iv = [Interval((1, 0), 0.0, 3.0), Interval((2, 0), 2.9, 6.0), Interval((1, 0), 5.9, 9.0)]
    cmd = build_compose_cmd("bg.mp4", "ov.png", "a.mp3", images, iv, None, "out.mp4",
                            1080, 1920, 30, 10.0)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd.count("a1.png") == 1 and cmd.count("a2.png") == 1   # each image loaded once
    assert "split=2[s0][s2]" in graph                              # reused for the repeat
    assert "setpts=PTS-STARTPTS+5.900/TB" in graph                 # only processed while visible
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "4:a"  # bg, overlay, 2 imgs, audio
