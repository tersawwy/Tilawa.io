"""
Quran recitation aligner — "listen, locate, align".

1. Two word sources, each with times:
   - wav2vec2 Arabic CTC greedy decode: frame-exact, never collapses repeats,
     but shatters words under heavy melisma / reverb.
   - Quran-tuned Whisper (tarteel) per utterance (energy-VAD breath groups):
     clean words even in mujawwad; short contexts keep it from merging repeats.
2. ``locate()`` runs a Viterbi search over Quran word positions, so every heard
   word is placed at the Quran position it most likely is. Transitions allow the
   reciter to move on, repeat (jump back — whole or partial ayah), skip ahead,
   or say something that is not in the searched text (JUNK: dua, applause,
   intro talk). Isti'adha / basmala are optional prefix positions.
3. The path is split into segments of consecutive Quran words. Each segment's
   exact text is CTC force-aligned on its own audio window, which gives
   precise word start/end times (≈20 ms frames).

The locator is pure Python/numpy and unit-tested offline; models load lazily.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


SAMPLE_RATE = 16000

ASR_MODEL = "tarteel-ai/whisper-base-ar-quran"
ASR_GENERATION_CONFIG = "openai/whisper-base"   # tarteel ships no timestamp config
CTC_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-arabic"

ISTIADHA = "اعوذ بالله من الشيطان الرجيم"
BASMALA = "بسم الله الرحمن الرحيم"
BASMALA_UTHMANI = "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"   # display text

# Pseudo ayah numbers for the optional prefixes (never rendered as subtitles)
ISTIADHA_AYAH = -1
BASMALA_AYAH = 0


# ─────────────────────────────────────────────────────────────
# Text normalisation + fuzzy word similarity
# ─────────────────────────────────────────────────────────────

_DIACRITICS = re.compile(
    r"[ؐ-ًؚ-ٰٟۖ-ۭـ]"
)


def normalize(text: str) -> str:
    """Aggressive normalisation for matching (not for display).

    Strips all harakat and Uthmani small marks (incl. small waw/yeh), unifies
    alef/hamza/ta-marbuta/alef-maqsura spellings so Uthmani text, simple
    script and ASR output compare on the same skeleton.
    """
    text = _DIACRITICS.sub("", text)
    text = re.sub(r"[آأإٱٲٳ]", "ا", text)  # alef forms
    text = text.replace("ى", "ي")                                  # ى → ي
    text = text.replace("ة", "ه")                                  # ة → ه
    text = re.sub(r"[ؤئ]", "ء", text)                         # ؤ ئ → ء
    text = re.sub(r"[^ء-ي\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """1.0 = identical, 0.0 = nothing in common (normalised edit distance).

    A leading article/conjunction glued on by the ASR (و، ف، ال) is tolerated
    by also comparing with it removed.
    """
    if not a or not b:
        return 0.0
    best = 1.0 - _levenshtein(a, b) / max(len(a), len(b))
    for pre in ("و", "ف"):
        if a.startswith(pre) and len(a) > 2 and not b.startswith(pre):
            s = 1.0 - _levenshtein(a[1:], b) / max(len(a) - 1, len(b))
            best = max(best, s - 0.05)
    return best


# ─────────────────────────────────────────────────────────────
# Positions — the searchable Quran text
# ─────────────────────────────────────────────────────────────

@dataclass
class Position:
    ayah: int          # ayah number (ISTIADHA_AYAH / BASMALA_AYAH for prefixes)
    word_index: int    # index within the ayah
    norm: str          # normalised text used for matching
    text: str          # original spelling (CTC alignment target)
    word: Optional[dict] = None  # the original quran word dict (None for prefixes)


def build_positions(quran_words: List[dict], surah: int, with_prefix: bool = True) -> List[Position]:
    """Flatten quran words into positions, optionally preceded by isti'adha and
    basmala (skipped for surah 1, where basmala *is* ayah 1, and surah 9)."""
    positions: List[Position] = []
    if with_prefix:
        for i, w in enumerate(ISTIADHA.split()):
            positions.append(Position(ISTIADHA_AYAH, i, normalize(w), w))
        if surah not in (1, 9):
            # basmala is Quran text, so it is subtitled (as ayah 0, no marker);
            # isti'adha only claims its audio (word=None → not displayed)
            for i, (w, disp) in enumerate(zip(BASMALA.split(), BASMALA_UTHMANI.split())):
                word = {"ayah_number": BASMALA_AYAH, "word_index": i, "text": disp,
                        "english_text": "", "start": None, "end": None}
                positions.append(Position(BASMALA_AYAH, i, normalize(w), w, word))
    for w in quran_words:
        positions.append(Position(w["ayah_number"], w["word_index"], normalize(w["text"]), w["text"], w))
    return positions


# ─────────────────────────────────────────────────────────────
# Fragment merging — slow recitation splits one word into pieces
# ─────────────────────────────────────────────────────────────

def merge_fragments(
    heard: Sequence[Tuple[str, float, float]],
    positions: Sequence[Position],
    max_gap: float = 2.5,
    min_match: float = 0.7,
    min_gain: float = 0.1,
) -> List[Tuple[str, float, float]]:
    """Join adjacent heard words when their concatenation matches a Quran
    word clearly better than either piece does alone.

    Long madd/ghunna makes the CTC emit one word as several pieces
    ("الا" "نسا" "ن" → الانسان, "لملا" "ءكه" → الملاءكه). Left alone, the
    pieces match nothing (look like noise) or match the wrong short word,
    which then fakes a repeat. Two genuine words never concatenate into a
    single Quran word, so they are left apart."""
    vocab = sorted({p.norm for p in positions if p.norm})

    def best(text: str) -> float:
        return max((similarity(text, v) for v in vocab), default=0.0)

    out: List[Tuple[str, float, float]] = []
    for w, s, e in heard:
        w = normalize(w)
        if out and s - out[-1][2] < max_gap:
            pw, ps, pe = out[-1]
            joined = pw + w
            m = best(joined)
            if m >= min_match and m >= max(best(pw), best(w)) + min_gain:
                out[-1] = (joined, ps, e)
                continue
        out.append((w, s, e))
    return out


# ─────────────────────────────────────────────────────────────
# Locate — Viterbi over Quran positions
# ─────────────────────────────────────────────────────────────

@dataclass
class LocateConfig:
    match_floor: float = 0.55   # similarity at which a word match ties with JUNK
    emit_scale: float = 4.0     # weight of the similarity term
    skip_cost: float = 0.8      # per Quran word skipped (ASR dropped a word)
    max_skip: int = 3           # skips beyond this are treated as a jump
    stay_cost: float = 0.6      # same position again (ASR split one word in two)
    repeat_cost: float = 1.5    # jump back (repeat / partial repeat)
    repeat_span_ayahs: int = 2  # how many ayahs back a repeat may reach
    jump_cost: float = 4.0      # jump forward beyond max_skip
    junk_enter: float = 1.2
    junk_stay: float = 0.2
    junk_exit: float = 0.8
    start_cost: float = 0.5     # starting anywhere but the first position


def _transition_matrix(positions: Sequence[Position], cfg: LocateConfig) -> np.ndarray:
    """T[i, j] = log-score of moving from position i to position j."""
    P = len(positions)
    idx = np.arange(P)
    d = idx[None, :] - idx[:, None]            # j - i
    T = np.full((P, P), -cfg.jump_cost, dtype=np.float64)

    T[d == 1] = 0.0
    for k in range(2, cfg.max_skip + 2):
        T[d == k] = -cfg.skip_cost * (k - 1)
    T[d == 0] = -cfg.stay_cost

    # Backward jumps: allowed within the current ayah and a few ayahs back.
    # Ordinal ayah index (prefixes count as their own "ayahs").
    ayah_ord = np.zeros(P, dtype=np.int64)
    for i in range(1, P):
        ayah_ord[i] = ayah_ord[i - 1] + (positions[i].ayah != positions[i - 1].ayah)
    ord_diff = ayah_ord[:, None] - ayah_ord[None, :]   # ayah(i) - ayah(j)
    back = d < 0
    T[back & (ord_diff <= cfg.repeat_span_ayahs)] = -cfg.repeat_cost
    T[back & (ord_diff > cfg.repeat_span_ayahs)] = -cfg.jump_cost * 2
    return T


def locate(
    asr_words: Sequence[str],
    positions: Sequence[Position],
    cfg: Optional[LocateConfig] = None,
) -> List[Optional[int]]:
    """Return, for each ASR word, the index of the Quran position it was
    recited at, or None if it is not part of the searched text (JUNK)."""
    cfg = cfg or LocateConfig()
    n, P = len(asr_words), len(positions)
    if n == 0:
        return []
    if P == 0:
        return [None] * n

    T = _transition_matrix(positions, cfg)
    norms = [normalize(w) for w in asr_words]
    emit = np.array(
        [[cfg.emit_scale * (similarity(a, p.norm) - cfg.match_floor) for p in positions] for a in norms]
    )

    J = P  # JUNK state index
    score = np.empty(P + 1)
    score[:P] = emit[0] - cfg.start_cost
    score[0] += cfg.start_cost
    score[J] = -cfg.junk_enter * 0.5
    back = np.zeros((n, P + 1), dtype=np.int64)
    back[0] = -1

    for t in range(1, n):
        cand = score[:P, None] + T                           # P × P
        best_from_pos = cand.argmax(axis=0)
        best_pos = cand[best_from_pos, np.arange(P)]
        from_junk = score[J] - cfg.junk_exit
        use_junk = from_junk > best_pos
        new = np.where(use_junk, from_junk, best_pos) + emit[t]
        back[t, :P] = np.where(use_junk, J, best_from_pos)

        junk_from_pos = score[:P] - cfg.junk_enter
        k = int(junk_from_pos.argmax())
        if score[J] - cfg.junk_stay >= junk_from_pos[k]:
            new_j, back[t, J] = score[J] - cfg.junk_stay, J
        else:
            new_j, back[t, J] = junk_from_pos[k], k
        score = np.append(new, new_j)

    state = int(score.argmax())
    path = [state]
    for t in range(n - 1, 0, -1):
        state = int(back[t, state])
        path.append(state)
    path.reverse()
    return [None if s == J else s for s in path]


# ─────────────────────────────────────────────────────────────
# Segments — runs of consecutive Quran positions
# ─────────────────────────────────────────────────────────────

@dataclass
class Segment:
    first: int                     # first Quran position (inclusive)
    last: int                      # last Quran position (inclusive)
    asr_idx: List[int] = field(default_factory=list)  # ASR words that fell in it
    start: float = 0.0             # rough times from ASR
    end: float = 0.0


def segments_from_path(
    path: Sequence[Optional[int]],
    asr_times: Sequence[Tuple[float, float]],
    positions: Sequence[Position],
    max_skip: int = 3,
    max_junk_run: int = 6,
    max_stay_gap: float = 1.0,
    max_stay_gap_clean: float = 2.5,
) -> List[Segment]:
    """Split a located path into segments. A new segment starts at every
    backward jump (repeat), forward jump longer than ``max_skip``, where the
    isti'adha/basmala prefix meets the ayah text, and after more than
    ``max_junk_run`` JUNK words in a row (real non-Quran speech).

    Shorter JUNK runs do not break a segment: slow reciters (long madd) make
    the CTC split one word into fragments ("الا نسا ن" = الإنسان) that match
    nothing on their own. Skips of ≤ max_skip also stay in the segment — the
    CTC stage re-inserts the missed words since it aligns the full span."""
    def group(p: int) -> int:
        # isti'adha and basmala are each their own group; all ayah text is one
        a = positions[p].ayah
        return a if a <= BASMALA_AYAH else 1

    segs: List[Segment] = []
    cur: Optional[Segment] = None
    junk_run = 0
    for i, pos in enumerate(path):
        if pos is None:
            junk_run += 1
            if junk_run > max_junk_run:
                cur = None
            continue
        # Same position again continues the segment only when it follows
        # closely (one slow word split into fragments); after a longer
        # stretch it is the reciter restarting from that word — a repeat.
        # Unmatched speech in between (the rest of the ayah, unheard) is
        # the signature of a restart.
        gap = asr_times[i][0] - cur.end if cur is not None else 0.0
        stay_ok = (
            cur is not None and pos == cur.last
            and (gap < max_stay_gap or (junk_run == 0 and gap < max_stay_gap_clean))
        )
        junk_run = 0
        if (
            cur is not None
            and (cur.last < pos <= cur.last + max_skip + 1 or stay_ok)
            and group(cur.last) == group(pos)
        ):
            cur.last = pos
            cur.asr_idx.append(i)
            cur.end = asr_times[i][1]
            continue
        cur = Segment(first=pos, last=pos, asr_idx=[i], start=asr_times[i][0], end=asr_times[i][1])
        segs.append(cur)
    return segs


# ─────────────────────────────────────────────────────────────
# Audio + models (lazy)
# ─────────────────────────────────────────────────────────────

def load_audio(path: str) -> np.ndarray:
    """Decode any audio file to mono float32 at 16 kHz via ffmpeg."""
    import subprocess

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


_CTC = None


def _get_ctc(model_name: str = CTC_MODEL):
    global _CTC
    if _CTC is None or _CTC[0] != model_name:
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        proc = Wav2Vec2Processor.from_pretrained(model_name)
        model = Wav2Vec2ForCTC.from_pretrained(model_name).eval()
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        try:
            model = model.to(device)
        except Exception:
            device = "cpu"
        _CTC = (model_name, proc, model, device)
    return _CTC


@dataclass
class Emissions:
    log_probs: np.ndarray        # [frames, vocab]
    frame_sec: float             # seconds per frame
    vocab: Dict[str, int]
    blank: int
    word_sep: int

    def frame(self, t: float) -> int:
        return int(round(t / self.frame_sec))


def ctc_emissions(audio: np.ndarray, model_name: str = CTC_MODEL,
                  chunk_sec: float = 20.0, overlap_sec: float = 2.0) -> Emissions:
    """Frame-level CTC log-probabilities for the whole clip, computed in
    overlapping chunks (keeps attention memory bounded on long clips) and
    stitched by keeping each chunk's centre."""
    import torch

    _, proc, model, device = _get_ctc(model_name)
    vocab = proc.tokenizer.get_vocab()
    stride = 320  # wav2vec2 conv stack: 20 ms at 16 kHz
    frame_sec = stride / SAMPLE_RATE

    n = len(audio)
    chunk, ov = int(chunk_sec * SAMPLE_RATE), int(overlap_sec * SAMPLE_RATE)
    total_frames = n // stride + 1
    out = None
    start = 0
    while True:
        end = min(start + chunk, n)
        piece = audio[start:end]
        inputs = proc(piece, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_values.to(device)
        with torch.inference_mode():
            lp = torch.log_softmax(model(inputs).logits[0].float(), dim=-1).cpu().numpy()
        if out is None:
            # frames past the model's last output default to certain blank
            out = np.full((total_frames, lp.shape[1]), -1e4, dtype=np.float32)
            out[:, vocab["<pad>"]] = 0.0
        f0 = start // stride
        keep_from = 0 if start == 0 else (ov // 2) // stride
        keep_to = lp.shape[0] if end == n else lp.shape[0] - (ov // 2) // stride
        keep_to = min(keep_to, total_frames - f0)
        out[f0 + keep_from: f0 + keep_to] = lp[keep_from: keep_to]
        if end == n:
            break
        start = end - ov
    return Emissions(out, frame_sec, vocab, vocab["<pad>"], vocab["|"])


def greedy_words(em: Emissions) -> List[Tuple[str, float, float]]:
    """Best-path CTC decode into words with frame-accurate (start, end) times.
    '|' and '-' tokens (and long blank runs) separate words."""
    inv = {i: c for c, i in em.vocab.items()}
    ids = em.log_probs.argmax(axis=1)
    seps = {em.word_sep, em.vocab.get("-", -1)}
    specials = {em.blank, em.vocab.get("<s>"), em.vocab.get("</s>"), em.vocab.get("<unk>")}
    max_blank_run = int(0.35 / em.frame_sec)

    words: List[Tuple[str, float, float]] = []
    chars: List[str] = []
    w_start = w_end = None
    prev = None
    blank_run = 0

    def flush():
        nonlocal chars, w_start, w_end
        if chars:
            text = normalize("".join(chars))
            if text:
                words.append((text, round(w_start * em.frame_sec, 3), round((w_end + 1) * em.frame_sec, 3)))
        chars, w_start, w_end = [], None, None

    for f, i in enumerate(ids):
        i = int(i)
        if i in specials:
            blank_run += 1
            if blank_run == max_blank_run:
                flush()
            prev = i
            continue
        blank_run = 0
        if i in seps:
            flush()
        elif i != prev:
            if w_start is None:
                w_start = f
            chars.append(inv[i])
            w_end = f
        else:
            w_end = f
        prev = i
    flush()
    return words


def _tokens_for(text: str, em: Emissions) -> List[int]:
    """Map a normalised word to CTC vocab ids (unknown chars dropped)."""
    return [em.vocab[c] for c in _ctc_spelling(text) if c in em.vocab]


def _ctc_spelling(text: str) -> str:
    """Spelling for CTC targets: the model's vocab keeps ة/ى/hamza forms, so
    only strip marks (normalize() would over-merge letters the model knows)."""
    text = _DIACRITICS.sub("", text)
    text = re.sub(r"[ٱٲٳ]", "ا", text)
    return re.sub(r"[^ء-ي]", "", text)


def align_words_ctc(
    em: Emissions, words: List[str], t0: float, t1: float,
) -> Tuple[List[Tuple[float, float]], float]:
    """Force-align exact ``words`` inside [t0, t1]. Returns per-word
    (start, end) times and the mean per-token log-probability (confidence)."""
    import torch
    import torchaudio.functional as AF

    f0 = max(0, em.frame(t0))
    f1 = min(em.log_probs.shape[0], em.frame(t1))
    token_words = [_tokens_for(w, em) for w in words]
    keep = [i for i, tw in enumerate(token_words) if tw]
    targets: List[int] = []
    owner: List[int] = []
    for k, i in enumerate(keep):
        if k:
            targets.append(em.word_sep); owner.append(-1)
        targets.extend(token_words[i]); owner.extend([i] * len(token_words[i]))

    # CTC needs at least one frame per token (plus one between repeated tokens)
    need = len(targets) + sum(1 for a, b in zip(targets, targets[1:]) if a == b)
    if not targets or f1 - f0 < need:
        return [], float("-inf")

    lp = torch.from_numpy(em.log_probs[f0:f1]).unsqueeze(0)
    tg = torch.tensor([targets], dtype=torch.int32)
    ali, scores = AF.forced_align(lp, tg, blank=em.blank)
    ali, scores = ali[0].numpy(), scores[0].numpy()

    # Walk the frame alignment: each run of a non-blank token = one target token
    spans: List[Tuple[int, int]] = []
    tok_scores: List[float] = []
    f = 0
    T = len(ali)
    while f < T:
        if ali[f] == em.blank:
            f += 1
            continue
        g = f
        while g + 1 < T and ali[g + 1] == ali[f]:
            g += 1
        spans.append((f, g))
        tok_scores.append(float(scores[f:g + 1].mean()))
        f = g + 1
    if len(spans) != len(targets):
        return [], float("-inf")

    times: Dict[int, List[int]] = {}
    for (a, b), o in zip(spans, owner):
        if o >= 0:
            times.setdefault(o, [a, b])[1] = b
    result: List[Tuple[float, float]] = []
    last_end = t0
    for i in range(len(words)):
        if i in times:
            a, b = times[i]
            s, e = (f0 + a) * em.frame_sec, (f0 + b + 1) * em.frame_sec
            result.append((round(s, 3), round(e, 3)))
            last_end = e
        else:  # word with no alignable letters — zero-length at previous end
            result.append((round(last_end, 3), round(last_end, 3)))
    return result, float(np.mean(tok_scores))


_ASR = None


def transcribe_text(audio: np.ndarray, model_name: str = ASR_MODEL) -> str:
    """Quran-tuned Whisper transcript (text only) — used for surah/ayah
    detection, where its clean spelling matters more than timing."""
    global _ASR
    from transformers import pipeline

    if _ASR is None or _ASR[0] != model_name:
        from transformers import GenerationConfig

        asr = pipeline("automatic-speech-recognition", model=model_name, device="cpu")
        # tarteel's checkpoint ships an outdated generation config that rejects
        # the `language` argument; the base Whisper config is compatible.
        asr.model.generation_config = GenerationConfig.from_pretrained(ASR_GENERATION_CONFIG)
        _ASR = (model_name, asr)
    out = _ASR[1](
        {"raw": audio, "sampling_rate": SAMPLE_RATE},
        chunk_length_s=30,
        generate_kwargs={"language": "ar", "task": "transcribe"},
    )
    return out["text"]


def speech_utterances(
    audio: np.ndarray, min_pause: float = 0.3, min_len: float = 0.4,
    max_len: float = 15.0, silence_db: float = -25.0,
) -> List[Tuple[float, float]]:
    """Split the clip into utterances (breath groups) at energy pauses.

    Plain energy, on purpose: neural VADs treat melodic chanting (mujawwad)
    as non-speech, and CTC blanks run through long sustained vowels. A
    recitation pause is a real drop in loudness. Utterances longer than
    ``max_len`` are cut at their quietest point so a Whisper pass never sees
    a repeat inside its context."""
    hop = int(0.02 * SAMPLE_RATE)
    n = len(audio) // hop
    if n == 0:
        return []
    frames = audio[: n * hop].reshape(n, hop)
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
    loud = np.percentile(db, 95)
    voiced = db > loud + silence_db
    # smooth: fill pauses shorter than min_pause
    min_pause_f = max(1, int(min_pause / 0.02))

    utts: List[List[int]] = []
    f = 0
    while f < n:
        if not voiced[f]:
            f += 1
            continue
        start, last, g = f, f, f
        while g < n:
            if voiced[g]:
                last = g
            elif g - last >= min_pause_f:
                break
            g += 1
        utts.append([start, last + 1])
        f = g

    out: List[Tuple[float, float]] = []
    stack = [(a, b) for a, b in utts if (b - a) * 0.02 >= min_len]
    while stack:
        a, b = stack.pop(0)
        if (b - a) * 0.02 <= max_len:
            out.append((round(a * 0.02, 3), round(b * 0.02, 3)))
            continue
        lo, hi = a + (b - a) // 4, b - (b - a) // 4
        cut = lo + int(np.argmin(db[lo:hi]))
        stack[:0] = [(a, cut), (cut, b)]
    return sorted(out)


def transcribe_utterances(
    audio: np.ndarray, utts: Sequence[Tuple[float, float]], model_name: str = ASR_MODEL,
) -> List[Tuple[str, float, float]]:
    """Quran-tuned Whisper per utterance → normalised words with approximate
    times (spread over the utterance by letter count). Short contexts keep
    Whisper from collapsing repeated phrases into one."""
    words: List[Tuple[str, float, float]] = []
    pad = int(0.1 * SAMPLE_RATE)
    for t0, t1 in utts:
        a = audio[max(0, int(t0 * SAMPLE_RATE) - pad): int(t1 * SAMPLE_RATE) + pad]
        toks = [normalize(w) for w in transcribe_text(a, model_name).split()]
        toks = [w for w in toks if w]
        if not toks:
            continue
        total = sum(len(w) for w in toks)
        t = t0
        for w in toks:
            d = (t1 - t0) * len(w) / total
            words.append((w, round(t, 3), round(t + d, 3)))
            t += d
    return words
