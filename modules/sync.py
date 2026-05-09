"""
Module 2 — Text & Synchronization

New pipeline:
1. faster-whisper transcribes audio → rough text used ONLY for ayah detection
2. Character bigram matching against full Quran index → auto-detects Surah/Ayah
3. Perfect Uthmani word-by-word text from Quran.com API v4 (char_type_name=word)
4. stable-ts forced alignment → maps audio timestamps to the known Quran text
5. Output: per-word timed list with timestamps from actual recitation
"""

import json
import os
import re
import sys
from collections import OrderedDict
from typing import List, Tuple

import requests


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
    # ── Detection ─────────────────────────────────────────────
    if surah and start_ayah and end_ayah:
        print(f"    Using manual override: Surah {surah}, Ayahs {start_ayah}–{end_ayah}")
    else:
        print("    Transcribing audio for ayah detection...")
        whisper_words = _transcribe_for_detection(audio_path, config["whisper"])
        transcribed_text = " ".join(w["word"] for w in whisper_words)

        print("    Auto-detecting Surah & Ayah from transcription...")
        surah, start_ayah, end_ayah = detect_ayahs(transcribed_text)
        print(f"    Detected: Surah {surah}, Ayahs {start_ayah}–{end_ayah}")

    # ── Fetch word-by-word Uthmani text ───────────────────────
    print("    Fetching word-by-word Quranic text from Quran.com API...")
    quran_words = fetch_quran_words(surah, start_ayah, end_ayah)
    print(f"    Fetched {len(quran_words)} words across ayahs {start_ayah}–{end_ayah}")

    # ── Forced alignment ──────────────────────────────────────
    print("    Running stable-ts forced alignment...")
    timed_words = align_forced(audio_path, quran_words, config)

    # ── Save ──────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(timed_words, f, ensure_ascii=False, indent=2)

    print(f"    Saved {len(timed_words)} timed words → {output_path}")
    return os.path.abspath(output_path)


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
# Step 1 — Transcription (for detection only)
# ─────────────────────────────────────────────────────────────

QURAN_PROMPT = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ"


def _transcribe_for_detection(audio_path: str, whisper_config: dict) -> List[dict]:
    """
    Transcribe audio with faster-whisper to get approximate text for ayah detection.
    Timestamps from this step are NOT used for final sync — stable-ts handles that.
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(
        whisper_config.get("model", "medium"),
        device=whisper_config.get("device", "cpu"),
        compute_type="int8",
    )

    segments, info = model.transcribe(
        audio_path,
        language="ar",
        word_timestamps=True,
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=False,
        initial_prompt=QURAN_PROMPT,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 200, "speech_pad_ms": 100},
    )

    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                word = w.word.strip()
                if word:
                    words.append({"word": word, "start": round(w.start, 3), "end": round(w.end, 3)})

    print(f"    Whisper: {len(words)} words (lang={info.language}, "
          f"conf={info.language_probability:.2f})")
    return words


# ─────────────────────────────────────────────────────────────
# Step 2 — Auto-Detection via Character Bigram Overlap
# ─────────────────────────────────────────────────────────────

def detect_ayahs(transcribed_text: str) -> Tuple[int, int, int]:
    """
    Find the best matching Surah/Ayah range for a Whisper transcription.
    Uses character bigram overlap — more tolerant of Tajweed phonetic variation
    than word-level Jaccard. Searches a window of up to 15 ayahs.
    Returns (surah_number, start_ayah, end_ayah).
    """
    quran = _load_quran_index()
    query_str = _clean_arabic(transcribed_text)

    if not query_str.strip():
        print("    Warning: Whisper returned no Arabic text — check audio quality.")
        print("    Please re-run with --surah / --start-ayah / --end-ayah.")
        sys.exit(1)

    best_score = -1.0
    best_match = (1, 1, 1)

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

    surah_num, start_ayah, end_ayah = best_match

    # Safety buffer: fetch one extra ayah — Whisper's VAD often cuts the last ayah short
    total_ayahs = len(next(s["ayahs"] for s in quran if s["number"] == surah_num))
    end_ayah = min(end_ayah + 1, total_ayahs)

    return surah_num, start_ayah, end_ayah


def _bigram_overlap(a: str, b: str) -> float:
    """Character bigram Jaccard similarity. More tolerant than word Jaccard."""
    def bigrams(s):
        s = s.replace(" ", "")
        return set(s[i:i+2] for i in range(len(s) - 1))
    bg_a, bg_b = bigrams(a), bigrams(b)
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


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


def _strip_for_alignment(text: str) -> str:
    """Strip harakat/diacritics for stable-ts alignment.

    Whisper's BPE tokenizer was trained on Arabic without diacritics. Passing
    fully-vowelised Uthmani text inflates the token count per word, breaking the
    1-to-1 word mapping and triggering the inaccurate proportional fallback.
    Stripping harakat here preserves word boundaries (spaces unchanged) while
    letting the tokenizer see the same bare-consonant form it was trained on.
    """
    text = re.sub(
        r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4'
        r'\u06E7\u06E8\u06EA-\u06ED]',
        '', text
    )
    return text.replace('\u0640', '')  # tatweel/kashida


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
# Step 4 — Forced Alignment via stable-ts
# ─────────────────────────────────────────────────────────────

def align_forced(audio_path: str, quran_words: List[dict], config: dict) -> List[dict]:
    """
    Use stable-ts forced alignment to map audio timestamps to known Quran text.
    Passes the full text as one string (load_faster_whisper has a bug with list input).
    Word timestamps are then mapped back to our quran_words list by index.
    """
    import stable_whisper

    # Group words by ayah for proportional-within-ayah fallback
    ayah_groups: OrderedDict = OrderedDict()
    for w in quran_words:
        ayah_groups.setdefault(w["ayah_number"], []).append(w)

    # Strip harakat before alignment: Whisper's BPE tokenizer was trained on
    # bare Arabic consonants. Diacritics inflate the token count and break the
    # 1-to-1 word mapping, pushing the code into the inaccurate proportional
    # fallback. The display text (w["text"]) is kept unchanged.
    full_text = " ".join(_strip_for_alignment(w["text"]) for w in quran_words)

    print(f"    Loading Whisper model '{config['whisper']['model']}' for alignment...")
    model = stable_whisper.load_faster_whisper(
        config["whisper"]["model"],
        device=config["whisper"].get("device", "cpu"),
        compute_type="int8",
    )

    print("    Aligning audio to Quran text (this may take 30–90 s)...")
    result = model.align(audio_path, full_text, language="ar")

    # Collect all word-level timings from stable-ts
    aligned = []
    for seg in (result.segments or []):
        for sw in (getattr(seg, "words", []) or []):
            aligned.append(sw)

    print(f"    stable-ts returned {len(aligned)} word timings for {len(quran_words)} Quran words")

    timed_words = []
    n_q = len(quran_words)
    n_a = len(aligned)

    if n_a >= n_q:
        # More or equal aligned words than Quran words — direct index mapping
        for i, qw in enumerate(quran_words):
            sw = aligned[i]
            entry = dict(qw)
            entry["start"] = round(float(sw.start), 3)
            entry["end"]   = round(float(sw.end),   3)
            timed_words.append(entry)
        print("    Direct word-level timestamps applied.")

    elif n_a > 0:
        # Fewer aligned words — map proportionally, then fix within-ayah order
        # Step 1: assign each Quran word a timestamp from the closest aligned word
        for i, qw in enumerate(quran_words):
            a_idx = round(i * (n_a - 1) / (n_q - 1)) if n_q > 1 else 0
            sw = aligned[min(a_idx, n_a - 1)]
            entry = dict(qw)
            entry["start"] = round(float(sw.start), 3)
            entry["end"]   = round(float(sw.end),   3)
            timed_words.append(entry)

        # Step 2: within each ayah, redistribute proportionally between first/last word times
        # This prevents duplicate timestamps for words mapped to the same aligned token
        tw_by_ayah: OrderedDict = OrderedDict()
        for w in timed_words:
            tw_by_ayah.setdefault(w["ayah_number"], []).append(w)

        for ayah_words in tw_by_ayah.values():
            a_start = ayah_words[0]["start"]
            a_end   = ayah_words[-1]["end"]
            n = len(ayah_words)
            if n > 1:
                duration = max(a_end - a_start, 0.3)
                per_word = duration / n
                for j, w in enumerate(ayah_words):
                    w["start"] = round(a_start + j * per_word, 3)
                    w["end"]   = round(a_start + (j + 1) * per_word, 3)

        print(f"    Proportional mapping applied ({n_a} aligned → {n_q} Quran words).")

    else:
        # No alignment result — even distribution fallback
        print("    Warning: alignment returned no words. Using even distribution.")
        duration = float(config.get("video", {}).get("duration", 30))
        per_word = duration / max(n_q, 1)
        for i, qw in enumerate(quran_words):
            entry = dict(qw)
            entry["start"] = round(i * per_word, 3)
            entry["end"]   = round((i + 1) * per_word, 3)
            timed_words.append(entry)

    # Sanity: non-negative, non-zero duration
    for w in timed_words:
        w["start"] = max(0.0, w["start"])
        w["end"]   = max(w["start"] + 0.05, w["end"])

    # Add display windows for word-pop mode (harmless extra fields for mushaf mode)
    timed_words = _compute_display_windows(timed_words)

    return timed_words


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
