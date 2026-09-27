"""
Offline tests for the Viterbi locator in modules/quran_align.py.

ASR word streams are synthesised from known Quran text, then perturbed the way
real recitations/ASR output are: repeats (full + partial), dropped words,
misspelled words, junk speech, starting mid-ayah.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.quran_align import (
    BASMALA_AYAH,
    ISTIADHA_AYAH,
    build_positions,
    locate,
    merge_fragments,
    normalize,
    segments_from_path,
    similarity,
)

# Surah 75 (Al-Qiyamah) ayahs 6–9, simple script.
AYAHS = {
    6: "يسأل أيان يوم القيامة",
    7: "فإذا برق البصر",
    8: "وخسف القمر",
    9: "وجمع الشمس والقمر",
}


def _quran_words():
    words = []
    for a, text in AYAHS.items():
        for i, w in enumerate(text.split()):
            words.append({"ayah_number": a, "word_index": i, "text": w})
    return words


POS = build_positions(_quran_words(), surah=75)


def _key(p):
    return (POS[p].ayah, POS[p].word_index) if p is not None else None


def _run(asr):
    path = locate(asr, POS)
    times = [(float(i), float(i) + 0.5) for i in range(len(asr))]
    return path, segments_from_path(path, times, POS)


def _seg_spans(segs):
    return [(_key(s.first), _key(s.last)) for s in segs]


def _words(*ayahs):
    return [w for a in ayahs for w in AYAHS[a].split()]


def test_normalize_unifies_uthmani_and_simple():
    assert normalize("ٱلْقِيَٰمَةِ") == normalize("القيامة".replace("ا", "", 0)) or \
        similarity(normalize("ٱلْقِيَٰمَةِ"), normalize("القيامة")) > 0.8
    assert normalize("أَيَّانَ") == normalize("ايان")


def test_straight_recitation_single_segment():
    path, segs = _run(_words(6, 7, 8, 9))
    assert None not in path
    assert _seg_spans(segs) == [((6, 0), (9, 2))]


def test_full_ayah_repeat_makes_new_segment():
    path, segs = _run(_words(6, 7, 7, 8))
    assert _seg_spans(segs) == [((6, 0), (7, 2)), ((7, 0), (8, 1))]


def test_partial_repeat_back_two_words():
    # "... يوم القيامة" repeated before continuing to ayah 7
    asr = _words(6) + ["يوم", "القيامة"] + _words(7)
    _, segs = _run(asr)
    assert _seg_spans(segs) == [((6, 0), (6, 3)), ((6, 2), (7, 2))]


def test_dropped_word_stays_in_segment():
    asr = _words(6, 7)
    del asr[5]  # drop "برق"
    _, segs = _run(asr)
    assert _seg_spans(segs) == [((6, 0), (7, 2))]


def test_misspelled_asr_words_still_located():
    asr = ["يسال", "ايان", "يوم", "القيامه", "فاذا", "برك", "البصر"]
    path, segs = _run(asr)
    assert None not in path
    assert _seg_spans(segs) == [((6, 0), (7, 2))]


def test_junk_speech_is_not_mapped():
    asr = ["صدق", "الله", "العظيم", "شكرا"] + _words(8, 9)
    path, segs = _run(asr)
    assert segs[-1].first == POS.index(next(p for p in POS if p.ayah == 8))
    assert _seg_spans(segs)[-1] == ((8, 0), (9, 2))
    # the unrelated words must not be placed inside ayahs 8/9
    assert all(p is None or POS[p].ayah not in (8, 9) for p in path[:4])


def test_start_mid_ayah():
    asr = ["يوم", "القيامة"] + _words(7)
    _, segs = _run(asr)
    assert _seg_spans(segs) == [((6, 2), (7, 2))]


def test_istiadha_prefix_detected():
    asr = "اعوذ بالله من الشيطان الرجيم".split() + "بسم الله الرحمن الرحيم".split() + _words(6)
    path, segs = _run(asr)
    ayahs = [POS[p].ayah for p in path]
    assert ayahs[:5] == [ISTIADHA_AYAH] * 5
    assert ayahs[5:9] == [BASMALA_AYAH] * 4
    assert _seg_spans(segs)[-1] == ((6, 0), (6, 3))


def test_repeat_of_two_ayahs():
    path, segs = _run(_words(6, 7, 8, 7, 8, 9))
    assert _seg_spans(segs) == [((6, 0), (8, 1)), ((7, 0), (9, 2))]


def test_fragmented_slow_word_does_not_split_segment():
    # Long madd: CTC splits "أيان" into fragments that match nothing alone
    asr = ["يسأل", "ا", "يا", "ن", "يوم", "القيامة"] + _words(7)
    _, segs = _run(asr)
    assert len(segs) == 1
    assert _seg_spans(segs)[0][1] == (7, 2)


def test_one_word_repeat_after_misheard_words_is_a_repeat():
    # "فإذا <garbled> <garbled> فإذا برق البصر" — restart of ayah 7
    asr = _words(6) + ["فإذا", "بريال", "الباساض", "فإذا", "برق", "البصر"] + _words(8)
    _, segs = _run(asr)
    assert len(segs) == 2
    assert _seg_spans(segs)[1] == ((7, 0), (8, 1))


def _timed(words):
    return [(w, float(i), float(i) + 0.5) for i, w in enumerate(words)]


def test_merge_fragments_joins_split_word():
    merged = merge_fragments(_timed(["يسأل", "ا", "يا", "ن", "يوم"]), POS)
    assert [w for w, _, _ in merged] == [normalize("يسأل"), "ايان", "يوم"]
    assert merged[1][1:] == (1.0, 3.5)


def test_merge_fragments_keeps_real_words_apart():
    words = _words(6, 7, 8, 9)
    merged = merge_fragments(_timed(words), POS)
    assert [w for w, _, _ in merged] == [normalize(w) for w in words]
