"""
Module 2 — Text & Synchronization

Pipeline ("listen → locate → align", see modules/quran_align.py):
1. wav2vec2 Arabic CTC listens to the whole clip → frame-level emissions and
   the words actually heard, with exact times. Unlike Whisper it does not
   collapse repeated phrases, so repeats and restarts stay visible.
2. Surah/ayah auto-detection from a Quran-tuned Whisper transcript (bigram
   search over the full Quran index).
3. A Viterbi search places every heard word at its Quran position, allowing
   repeats (whole or partial), skips, isti'adha/basmala and non-Quran speech.
4. Each run of consecutive Quran words (a "segment") is CTC force-aligned on
   its own audio window → precise per-word start/end.
5. Output: per-word timed list; repeated recitations appear as repeated words
   with their own segment_id.
"""

import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

import requests

from modules import quran_align as qa


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

QURANCOM_API  = "https://api.quran.com/api/v4"
ALQURAN_API   = "https://api.alquran.cloud/v1"
QURAN_CACHE   = "tmp/quran_clean.json"

# Sahih International translation ID on Quran.com
TRANSLATION_ID = 131


# ─────────────────────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────────────────────

def build_timed_words(
    audio_path: str,
    config: dict,
    output_path: str,
    surah: int = None,
    start_ayah: int = None,
    end_ayah: int = None,
) -> str:
    """
    Main entry point. Auto-detects surah/ayah unless overrides are given.
    Returns absolute path of the saved timed_words.json.
    """
    sync_cfg = config.get("sync", {}) or {}

    # ── 1. Listen ─────────────────────────────────────────────
    # Two independent word sources, both with times:
    #  - CTC words: exact times, never collapses repeats, but shatters words
    #    under heavy melisma/reverb (mujawwad, live mosque audio)
    #  - Quran-Whisper per utterance: clean words even in mujawwad; run per
    #    breath group so repeats (separated by a breath) are not collapsed
    audio = qa.load_audio(audio_path)
    print("    Listening (wav2vec2 CTC)...")
    em = qa.ctc_emissions(audio, sync_cfg.get("ctc_model", qa.CTC_MODEL))
    ctc_words = qa.greedy_words(em)
    utts = qa.speech_utterances(audio)
    print(f"    Transcribing {len(utts)} utterance(s) (Quran Whisper)...")
    try:
        asr_words = qa.transcribe_utterances(audio, utts, sync_cfg.get("asr_model", qa.ASR_MODEL))
    except Exception as e:
        print(f"    Quran ASR unavailable ({e}) — using CTC words only.")
        asr_words = []
    print(f"    Heard {len(ctc_words)} CTC / {len(asr_words)} Whisper words "
          f"in {len(audio) / qa.SAMPLE_RATE:.1f}s of audio")
    if not ctc_words and not asr_words:
        print("    Warning: no speech recognised — check the audio.")

    # ── 2. Detect surah / ayah range ──────────────────────────
    manual = bool(surah and start_ayah and end_ayah)
    if manual:
        print(f"    Manual override: Surah {surah}, Ayahs {start_ayah}–{end_ayah}")
    else:
        print("    Auto-detecting Surah & Ayah...")
        surah, start_ayah, end_ayah = detect_ayahs(
            " ".join(w for w, _, _ in asr_words), " ".join(w for w, _, _ in ctc_words)
        )
        print(f"    Detected: Surah {surah}, Ayahs {start_ayah}–{end_ayah}")

    # Search space: detection is only a hint of where to look. Neighbouring
    # ayahs cost nothing here — the locator only uses text it actually hears.
    margin = 0 if manual else int(sync_cfg.get("search_margin_ayahs", 2))
    n_ayahs = _surah_length(surah)
    lo = max(1, start_ayah - margin)
    hi = min(n_ayahs or end_ayah + margin, end_ayah + margin)

    print(f"    Fetching word-by-word Quranic text (ayahs {lo}–{hi})...")
    quran_words = fetch_quran_words(surah, lo, hi)
    positions = qa.build_positions(quran_words, surah)

    # ── 3+4. Locate + align with each source, keep the better result ──
    loc_cfg = qa.LocateConfig(**{
        k: sync_cfg[k] for k in qa.LocateConfig.__dataclass_fields__ if k in sync_cfg
    })
    min_conf = float(sync_cfg.get("min_segment_confidence", -2.5))
    results = []
    if ctc_words:
        heard = qa.merge_fragments(ctc_words, positions)
        results.append(("CTC", *_locate_and_align(em, heard, positions, loc_cfg, min_conf)))
    if asr_words:
        # Whisper words carry estimated times: wider windows. Whisper doesn't
        # fragment words, so "same word again" is only a continuation across
        # a forced chunk cut (no pause) — after a real breath it's a repeat.
        results.append(("Whisper", *_locate_and_align(
            em, asr_words, positions, loc_cfg, min_conf,
            max_stay_gap=0.25, max_stay_gap_clean=0.25, pad=1.0,
        )))
    if results:
        source, timed_words, segs = max(results, key=lambda r: len(r[1]))
        if len(results) > 1:
            counts = ", ".join(f"{n}: {len(t)}" for n, t, _ in results)
            print(f"    Aligned words per source ({counts}) → using {source}")
    else:
        timed_words, segs = [], []
    _print_recitation_map(timed_words, segs, positions)

    # Safety net: words crammed into near-zero durations at the very end are
    # not audible (audio cut mid-word).
    timed_words = _drop_crammed_tail(timed_words)

    # Add display windows for word-pop mode (harmless extra fields for mushaf mode)
    timed_words = _compute_display_windows(timed_words)

    # ── Save ──────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(timed_words, f, ensure_ascii=False, indent=2)

    # Persist what was recited so later stages (e.g. the upload caption)
    # work without manual --surah flags.
    recited = [w["ayah_number"] for w in timed_words if w["ayah_number"] > 0] or [start_ayah]
    meta = {"surah": surah, "start_ayah": min(recited), "end_ayah": max(recited)}
    os.makedirs("tmp", exist_ok=True)
    with open("tmp/sync_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    print(f"    Saved {len(timed_words)} timed words → {output_path}")
    return os.path.abspath(output_path)


def _locate_and_align(
    em: "qa.Emissions",
    heard: List[Tuple[str, float, float]],
    positions: List[qa.Position],
    loc_cfg: "qa.LocateConfig",
    min_conf: float,
    max_stay_gap: float = 1.0,
    max_stay_gap_clean: float = 2.5,
    pad: Optional[float] = None,
) -> Tuple[List[dict], List[qa.Segment]]:
    """Place heard words in the Quran text, cut segments, CTC-align them."""
    path = qa.locate([w for w, _, _ in heard], positions, loc_cfg)
    segs = qa.segments_from_path(
        path, [(s, e) for _, s, e in heard], positions, loc_cfg.max_skip,
        max_stay_gap=max_stay_gap, max_stay_gap_clean=max_stay_gap_clean,
    )
    segs = _drop_spurious_segments(segs, heard, positions)
    pads = {} if pad is None else {"pad_before": pad, "pad_after": pad}
    return _align_segments(em, segs, positions, min_conf, **pads)


def _drop_spurious_segments(
    segs: List[qa.Segment], heard: List[Tuple[str, float, float]], positions: List[qa.Position],
) -> List[qa.Segment]:
    """A segment backed by a single, weakly-matching heard word is almost
    always a coincidence (a short common word like من / الله inside noise or
    speech that is not in the searched text). Keep one-word segments only when
    the match is strong and the word is long enough to be distinctive."""
    kept = []
    for s in segs:
        if len(s.asr_idx) == 1:
            w = heard[s.asr_idx[0]][0]
            sim = qa.similarity(qa.normalize(w), positions[s.first].norm)
            if sim < 0.8 or len(w) < 4:
                continue
        kept.append(s)
    return kept


def _align_segments(
    em: "qa.Emissions",
    segs: List[qa.Segment],
    positions: List[qa.Position],
    min_confidence: float,
    pad_before: float = 0.35,
    pad_after: float = 0.6,
    max_extend_sec: float = 8.0,
    extend_tolerance: float = 0.3,
) -> Tuple[List[dict], List[qa.Segment]]:
    """CTC force-align each segment's exact Quran text inside its own window.

    Edge repair: the ASR often misses the first/last word(s) of an ayah, so a
    segment may start or end mid-ayah. Each edge is tentatively extended to the
    ayah boundary (window widened by up to ``max_extend_sec`` — one word with
    a long madd/ghunna can last 4 s, and blanks absorb any silence); it is kept
    only if the CTC confidence stays within ``extend_tolerance`` — real
    speech aligns well, silence or another recitation does not (so genuine
    partial repeats keep their mid-ayah start).

    Segments that still align badly and rest on little evidence are dropped.
    Prefix segments (isti'adha / basmala) claim their audio but are not
    emitted as subtitles. Returns (timed words, kept segments)."""
    audio_end = em.log_probs.shape[0] * em.frame_sec
    ayah_first, ayah_last = {}, {}
    for idx, p in enumerate(positions):
        ayah_first.setdefault(p.ayah, idx)
        ayah_last[p.ayah] = idx

    def align(first, last, t0, t1):
        return qa.align_words_ctc(em, [p.text for p in positions[first: last + 1]], t0, t1)

    timed: List[dict] = []
    kept: List[qa.Segment] = []
    prev_end = 0.0
    for k, seg in enumerate(segs):
        nxt_start = segs[k + 1].start if k + 1 < len(segs) else audio_end
        t0 = max(prev_end, seg.start - pad_before)
        t1 = min(nxt_start, seg.end + pad_after)
        times, conf = align(seg.first, seg.last, t0, t1)

        if times and positions[seg.first].word is not None:
            # Extend the start back to the ayah's first word
            a_first = ayah_first[positions[seg.first].ayah]
            if a_first < seg.first:
                t0x = max(prev_end, seg.start - pad_before - max_extend_sec)
                tx, cx = align(a_first, seg.last, t0x, t1)
                if tx and cx >= conf - extend_tolerance:
                    seg.first, t0, times, conf = a_first, t0x, tx, cx
            # Extend the end forward to the ayah's last word
            a_last = ayah_last[positions[seg.last].ayah]
            if a_last > seg.last:
                t1x = min(nxt_start, seg.end + pad_after + max_extend_sec)
                tx, cx = align(seg.first, a_last, t0, t1x)
                if tx and cx >= conf - extend_tolerance:
                    seg.last, t1, times, conf = a_last, t1x, tx, cx

        if not times or conf < min_confidence:
            if len(seg.asr_idx) < 4:
                print(f"    Dropping weak segment at {seg.start:.1f}s (confidence {conf:.2f}).")
                continue
            # Plenty of heard words but poor alignment: spread over heard span
            print(f"    Low alignment confidence ({conf:.2f}) for segment at "
                  f"{seg.start:.1f}s — using heard timing.")
            n = seg.last - seg.first + 1
            per = max(seg.end - seg.start, 0.05 * n) / n
            times = [(round(seg.start + i * per, 3), round(seg.start + (i + 1) * per, 3))
                     for i in range(n)]
        prev_end = max(prev_end, times[-1][1])

        seg_id = len(kept)
        kept.append(seg)
        for p, (s, e) in zip(positions[seg.first: seg.last + 1], times):
            if p.word is None:  # isti'adha — claims its audio, not subtitled
                continue
            entry = dict(p.word)
            entry["start"] = s
            entry["end"] = max(e, s + 0.05)
            entry["segment_id"] = seg_id
            entry["confidence"] = round(conf, 3)
            timed.append(entry)
    return timed, kept


def _print_recitation_map(timed: List[dict], segs: List[qa.Segment], positions: List[qa.Position]):
    """One line per segment, e.g. `3.4–9.8s  75:6:1 → 75:7:3 (repeat)`."""
    print("    Recitation map:")
    seen = set()
    for k, seg in enumerate(segs):
        a, b = positions[seg.first], positions[seg.last]
        if a.ayah == qa.ISTIADHA_AYAH:
            label, note = "isti'adha", ""
        elif a.ayah == qa.BASMALA_AYAH:
            label, note = "basmala", ""
        else:
            words = [w for w in timed if w.get("segment_id") == k]
            label = f"{a.ayah}:{a.word_index + 1} → {b.ayah}:{b.word_index + 1}"
            span = set(range(seg.first, seg.last + 1))
            note = " (repeat)" if span & seen else ""
            seen |= span
            if words:
                print(f"      {words[0]['start']:6.2f}–{words[-1]['end']:6.2f}s  {label}{note}")
                continue
        print(f"      {seg.start:6.2f}–{seg.end:6.2f}s  {label}{note}")


def _surah_length(surah: int) -> Optional[int]:
    quran = _load_quran_index()
    data = next((s for s in quran if s["number"] == surah), None)
    return len(data["ayahs"]) if data else None


def get_surah_name(surah: int) -> Tuple[str, str]:
    """Return (arabic_name, english_name) for a surah number."""
    try:
        resp = requests.get(
            f"{QURANCOM_API}/chapters/{surah}",
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        ch = resp.json()["chapter"]
        return ch.get("name_arabic", ""), ch.get("name_simple", "")
    except Exception:
        data = _alquran_api_get(f"surah/{surah}")
        return data["data"].get("name", ""), data["data"].get("englishName", "")



# ─────────────────────────────────────────────────────────────
# Surah / Ayah Auto-Detection via Character Bigram Overlap
# ─────────────────────────────────────────────────────────────

def detect_ayahs(*transcripts: str) -> Tuple[int, int, int]:
    """
    Find the best matching Surah/Ayah range for one or more transcriptions
    of the same audio (e.g. Quran-Whisper and CTC); the best-scoring one wins.
    Uses character bigram overlap — more tolerant of Tajweed phonetic variation
    than word-level Jaccard. Searches a window of up to 15 ayahs.
    Returns (surah_number, start_ayah, end_ayah).
    """
    quran = _load_quran_index()
    queries = [q for q in (_clean_arabic(t) for t in transcripts) if q.strip()]

    if not queries:
        print("    Warning: no Arabic text recognised — check audio quality.")
        print("    Please re-run with --surah / --start-ayah / --end-ayah.")
        sys.exit(1)

    best_score = -1.0
    best_match = (1, 1, 1)

    for query_str in queries:
        for surah_data in quran:
            surah_num = surah_data["number"]
            ayahs = surah_data["ayahs"]
            n = len(ayahs)

            for start_idx in range(n):
                window_text = ""
                for end_idx in range(start_idx, min(start_idx + 15, n)):
                    window_text += " " + ayahs[end_idx]["clean"]
                    score = _bigram_overlap(query_str, window_text.strip())
                    if score > best_score:
                        best_score = score
                        best_match = (surah_num, ayahs[start_idx]["number"], ayahs[end_idx]["number"])

    print(f"    Match confidence: {best_score:.0%}")

    if best_score < 0.25:
        print(f"    Low confidence ({best_score:.2f}) — audio may be noisy or not Quranic.")
        print("    Re-run with --surah / --start-ayah / --end-ayah to override.")
        sys.exit(1)

    # build_timed_words widens this range by sync.search_margin_ayahs; the
    # locator only emits ayahs it actually hears, so the margin is safe.
    return best_match


def _bigram_overlap(a: str, b: str) -> float:
    """Character bigram Jaccard similarity. More tolerant than word Jaccard."""
    def bigrams(s):
        s = s.replace(" ", "")
        return set(s[i:i+2] for i in range(len(s) - 1))
    bg_a, bg_b = bigrams(a), bigrams(b)
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


# ─────────────────────────────────────────────────────────────
# Quran index (diacritic-stripped, cached)
# ─────────────────────────────────────────────────────────────

def _load_quran_index() -> list:
    """Load (or build) the diacritic-stripped Quran index, cached locally."""
    os.makedirs("tmp", exist_ok=True)
    if os.path.exists(QURAN_CACHE):
        with open(QURAN_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)

    print("    Building Quran search index (one-time download ~2 MB)...")
    resp = requests.get(f"{ALQURAN_API}/quran/ar.simple", timeout=30)
    resp.raise_for_status()
    data = resp.json()

    quran = []
    for surah in data["data"]["surahs"]:
        quran.append({
            "number": surah["number"],
            "englishName": surah["englishName"],
            "ayahs": [
                {
                    "number": a["numberInSurah"],
                    "clean": _clean_arabic(a["text"]),
                }
                for a in surah["ayahs"]
            ],
        })

    with open(QURAN_CACHE, "w", encoding="utf-8") as f:
        json.dump(quran, f, ensure_ascii=False)

    print(f"    Index cached → {QURAN_CACHE}")
    return quran


def _clean_arabic(text: str) -> str:
    """Strip diacritics, tatweel, normalize alef variants."""
    text = re.sub(
        r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]',
        '', text
    )
    text = text.replace('\u0640', '')
    text = re.sub(r'[^\u0600-\u06FF\s]', '', text)
    text = re.sub(r'[\u0622\u0623\u0625\u0671]', '\u0627', text)
    return re.sub(r'\s+', ' ', text).strip()


# ─────────────────────────────────────────────────────────────
# Step 3 — Fetch Word-by-Word Uthmani Text (Quran.com API v4)
# ─────────────────────────────────────────────────────────────

def fetch_quran_words(surah: int, start_ayah: int, end_ayah: int) -> List[dict]:
    """
    Fetch per-word Uthmani Arabic from Quran.com API v4 (correct OpenType font data).
    Fetch ayah-level English translation from alquran.cloud (natural sentence, not word-by-word).
    Returns flat list of word dicts with ayah_number, text, english_text, word_index.
    """
    # ── English translations (alquran.cloud — gives proper sentences) ──
    eng_data = _alquran_api_get(f"surah/{surah}/en.sahih")
    english_by_ayah = {
        a["numberInSurah"]: a["text"]
        for a in eng_data["data"]["ayahs"]
    }

    # ── Arabic words per ayah (Quran.com API v4) ──
    words = []
    for ayah_num in range(start_ayah, end_ayah + 1):
        verse_key = f"{surah}:{ayah_num}"
        url = (
            f"{QURANCOM_API}/verses/by_key/{verse_key}"
            f"?words=true&word_fields=text_uthmani"
        )
        resp = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
        resp.raise_for_status()
        verse = resp.json()["verse"]

        english = english_by_ayah.get(ayah_num, "")

        # Only include actual words (skip end-of-ayah markers like ١)
        word_idx = 0
        for w in verse.get("words", []):
            if w.get("char_type_name") != "word":
                continue
            text = w.get("text_uthmani") or w.get("text", "")
            if not text.strip():
                continue
            words.append({
                "ayah_number": ayah_num,
                "word_index": word_idx,
                "text": text,
                "english_text": english,
                "start": None,
                "end": None,
            })
            word_idx += 1

    return words


# ─────────────────────────────────────────────────────────────
# Post-processing
# ─────────────────────────────────────────────────────────────

def _drop_crammed_tail(timed_words: List[dict], min_duration: float = 0.12) -> List[dict]:
    """
    Drop trailing words the aligner crammed into near-zero durations — they are
    not audible (the audio was cut mid-recitation, or the text had words the
    audio never contained). Only the tail is touched; a fast word mid-recitation
    is never removed.
    """
    words = list(timed_words)
    dropped = 0
    while words and (words[-1]["end"] - words[-1]["start"]) < min_duration:
        words.pop()
        dropped += 1
    if dropped:
        print(f"    Dropped {dropped} crammed inaudible word(s) at the tail.")
    return words


def _compute_display_windows(timed_words: List[dict], min_word_display: float = 0.18) -> List[dict]:
    """
    Compute display windows for word-pop mode.

    display_start = word.start (same as acoustic start)
    display_end   = next_word.start   ← fills natural pauses (madd, breath)
                                         so the word stays visible until the next
                                         word begins — no blank frames on gaps.

    Words whose display window is shorter than min_word_display are merged into
    the following word (display text only — acoustic start/end unchanged on the
    surviving entry).  This prevents sub-frame flashes on rapid syllable sequences.

    The original start/end fields are preserved so mushaf mode is unaffected.
    """
    if not timed_words:
        return timed_words

    # Work on shallow copies so we don't mutate caller's list in-place
    words = [dict(w) for w in timed_words]

    # Step 1 — assign display windows
    for i in range(len(words) - 1):
        words[i]["display_start"] = words[i]["start"]
        words[i]["display_end"]   = words[i + 1]["start"]
    words[-1]["display_start"] = words[-1]["start"]
    words[-1]["display_end"]   = words[-1]["end"]

    # Step 2 — merge words too short to read (cascades forward)
    result: List[dict] = []
    i = 0
    while i < len(words):
        w = words[i]
        duration = w["display_end"] - w["display_start"]
        if duration < min_word_display and i + 1 < len(words):
            # Merge this word's text into the following word and extend its
            # display window backwards so there's still no gap.
            nxt = dict(words[i + 1])
            nxt["text"]          = w["text"] + " " + nxt["text"]
            nxt["display_start"] = w["display_start"]
            words[i + 1]         = nxt   # update in-place for next iteration
            i += 1
            continue                     # skip the too-short word
        result.append(w)
        i += 1

    return result


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _alquran_api_get(endpoint: str) -> dict:
    resp = requests.get(f"{ALQURAN_API}/{endpoint}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise ValueError(f"alquran.cloud API error [{endpoint}]: {data.get('status')}")
    return data
