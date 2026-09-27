#!/usr/bin/env python3
"""
Sync benchmark with exact ground truth.

Builds synthetic recitations from everyayah.com per-ayah MP3s (one file = one
ayah, so every ayah's start time is known by construction), including late
starts, full repeats, partial repeats, isti'adha/basmala, long pauses and audio
cut mid-ayah. Runs the real sync module on each and reports how far each
predicted recitation start is from the truth.

Not part of pytest (needs network + models):
    python tests/bench_sync.py              # all cases
    python tests/bench_sync.py B C          # selected cases
"""

import json
import os
import subprocess
import sys
import urllib.request
from statistics import median

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SR = 16000
CACHE = os.path.join("tmp", "bench_cache")
EVERYAYAH = "https://everyayah.com/data/{reciter}/{name}.mp3"

MEDIAN_TARGET = 0.30
MAX_TARGET = 0.60


# ─────────────────────────────────────────────────────────────
# Audio building blocks
# ─────────────────────────────────────────────────────────────

def _load(reciter: str, name: str) -> np.ndarray:
    os.makedirs(CACHE, exist_ok=True)
    mp3 = os.path.join(CACHE, f"{reciter}_{name}.mp3")
    if not os.path.exists(mp3):
        urllib.request.urlretrieve(EVERYAYAH.format(reciter=reciter, name=name), mp3)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", mp3, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _onset(y: np.ndarray) -> float:
    """First moment of speech inside a clip (skip its leading silence)."""
    frame = int(0.01 * SR)
    energy = np.array([np.abs(y[i:i + frame]).mean() for i in range(0, len(y) - frame, frame)])
    thr = max(energy.max() * 0.05, 1e-4)
    idx = int(np.argmax(energy > thr))
    return idx * 0.01


class Track:
    """Accumulates audio + ground-truth recitation events (ayah, start)."""

    def __init__(self, reciter: str):
        self.reciter = reciter
        self.parts = []
        self.t = 0.0
        self.events = []  # (ayah, start_seconds, note)

    def silence(self, sec: float):
        self.parts.append(np.zeros(int(sec * SR), dtype=np.float32))
        self.t += sec

    def ayah(self, surah: int, ayah: int, frac_from: float = 0.0, frac_to: float = 1.0,
             note: str = "", label: bool = True):
        y = _load(self.reciter, f"{surah:03d}{ayah:03d}")
        y = y[int(len(y) * frac_from): int(len(y) * frac_to)]
        if label:
            self.events.append((ayah, round(self.t + _onset(y), 3), note))
        self.parts.append(y)
        self.t += len(y) / SR
        self.silence(0.3)

    def raw(self, name: str):
        y = _load(self.reciter, name)
        self.parts.append(y)
        self.t += len(y) / SR
        self.silence(0.3)

    def save(self, path: str):
        audio = np.concatenate(self.parts)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
             "-i", "-", path],
            input=audio.tobytes(), check=True,
        )
        return len(audio) / SR


# ─────────────────────────────────────────────────────────────
# Cases
# ─────────────────────────────────────────────────────────────

def case_A(r):  # late start, straight recitation
    t = Track(r); t.silence(3.0)
    for a in range(6, 13):
        t.ayah(75, a)
    return t, 75


def case_B(r):  # full repeat of one ayah
    t = Track(r)
    for a, note in [(6, ""), (7, ""), (7, "repeat"), (8, ""), (9, "")]:
        t.ayah(75, a, note=note)
    return t, 75


def case_C(r):  # partial repeat: last 40% of 67:2 recited again
    t = Track(r)
    t.ayah(67, 1)
    t.ayah(67, 2)
    t.ayah(67, 2, frac_from=0.6, note="partial repeat")
    t.ayah(67, 3)
    return t, 67


def case_D(r):  # isti'adha + basmala + long mid pause
    t = Track(r)
    t.raw("audhubillah")
    t.raw("bismillah")
    t.ayah(112, 1); t.ayah(112, 2)
    t.silence(4.0)
    t.ayah(112, 3); t.ayah(112, 4)
    return t, 112


def case_E(r):  # audio cut mid-ayah
    t = Track(r)
    for a in range(6, 10):
        t.ayah(75, a)
    t.ayah(75, 10, frac_to=0.5, note="cut")
    return t, 75


def case_F(r):  # repeat of two ayahs together, then continue
    t = Track(r)
    for a, note in [(1, ""), (2, ""), (3, ""), (2, "repeat"), (3, "repeat"), (4, "")]:
        t.ayah(97, a, note=note)
    return t, 97


CASES = {
    "A": (case_A, "Alafasy_128kbps"),
    "B": (case_B, "Alafasy_128kbps"),
    "C": (case_C, "Alafasy_128kbps"),
    "D": (case_D, "Alafasy_128kbps"),
    "E": (case_E, "Alafasy_128kbps"),
    "F": (case_F, "Alafasy_128kbps"),
    "A-husary": (case_A, "Husary_128kbps"),
    "B-husary": (case_B, "Husary_128kbps"),
}


# ─────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────

def predicted_events(timed_words):
    """A recitation event starts wherever the segment or the ayah changes."""
    events, prev = [], None
    for w in timed_words:
        if w["ayah_number"] <= 0:  # basmala is subtitled but isn't a numbered ayah
            continue
        key = (w.get("segment_id"), w["ayah_number"])
        if key != prev:
            events.append((w["ayah_number"], w["start"]))
        prev = key
    return events


def match_events(truth, pred):
    """Order-preserving match on ayah label (LCS). Returns pairs + misses."""
    n, m = len(truth), len(pred)
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            L[i][j] = L[i + 1][j + 1] + 1 if truth[i][0] == pred[j][0] else max(L[i + 1][j], L[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if truth[i][0] == pred[j][0]:
            pairs.append((i, j)); i += 1; j += 1
        elif L[i + 1][j] >= L[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def run_case(name, config):
    from modules.sync import build_timed_words

    builder, reciter = CASES[name]
    track, surah = builder(reciter)
    os.makedirs(CACHE, exist_ok=True)
    audio_path = os.path.join(CACHE, f"case_{name}.mp3")
    dur = track.save(audio_path)
    config = json.loads(json.dumps(config))
    config["video"]["duration"] = dur

    out = os.path.join(CACHE, f"case_{name}_timed.json")
    build_timed_words(audio_path=audio_path, config=config, output_path=out)
    with open(out, encoding="utf-8") as f:
        timed = json.load(f)
    with open("tmp/sync_meta.json", encoding="utf-8") as f:
        meta = json.load(f)

    truth = [(a, s) for a, s, _ in track.events]
    pred = predicted_events(timed)
    pairs = match_events(truth, pred)
    errors = [abs(truth[i][1] - pred[j][1]) for i, j in pairs]

    print(f"\n── Case {name} ({reciter}, surah {surah}, {dur:.1f}s) — detected surah {meta['surah']}")
    matched_t = {i for i, _ in pairs}
    matched_p = {j for _, j in pairs}
    for i, (a, s, note) in enumerate(track.events):
        pj = next((j for ti, j in pairs if ti == i), None)
        got = f"{pred[pj][1]:6.2f}s  err {abs(s - pred[pj][1]):.2f}s" if pj is not None else "   MISSED"
        print(f"   ayah {a:>3} @ {s:6.2f}s  →  {got}  {note}")
    extra = [pred[j] for j in range(len(pred)) if j not in matched_p]
    for a, s in extra:
        print(f"   EXTRA  ayah {a:>3} @ {s:6.2f}s")

    missed = len(truth) - len(matched_t)
    ok = (
        meta["surah"] == surah and missed == 0 and not extra and errors
        and median(errors) <= MEDIAN_TARGET and max(errors) <= MAX_TARGET
    )
    summary = {
        "case": name, "ok": bool(ok), "surah_ok": meta["surah"] == surah,
        "missed": missed, "extra": len(extra),
        "median": round(median(errors), 3) if errors else None,
        "max": round(max(errors), 3) if errors else None,
    }
    print(f"   → {'PASS' if ok else 'FAIL'}  median {summary['median']}s  max {summary['max']}s  "
          f"missed {missed}  extra {len(extra)}")
    return summary


def main():
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    names = sys.argv[1:] or list(CASES)
    results = [run_case(n, config) for n in names]

    print("\n" + "=" * 64)
    for r in results:
        print(f"  {r['case']:<10} {'PASS' if r['ok'] else 'FAIL'}  median {r['median']}s  "
              f"max {r['max']}s  missed {r['missed']}  extra {r['extra']}  surah_ok {r['surah_ok']}")
    print(f"  {sum(r['ok'] for r in results)}/{len(results)} passed "
          f"(targets: median ≤ {MEDIAN_TARGET}s, max ≤ {MAX_TARGET}s)")
    print("=" * 64)


if __name__ == "__main__":
    main()
