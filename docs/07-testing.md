# 7. Testing

Two layers: fast **offline unit tests**, and a **sync accuracy benchmark** with exact ground truth.

---

## Unit tests (offline, ~3 seconds)

```bash
pip install pytest
python -m pytest tests -q
```

Expected: `41 passed`.

| File | Tests | Covers |
|---|---|---|
| `tests/test_quran_locate.py` | 14 | Viterbi locator: straight recitation, full/partial repeats, dropped/misspelled words, junk speech, starting mid-ayah, isti'adha/basmala, fragmented slow words |
| `tests/test_render_repeats.py` | 8 | Render timeline: repeats don't flicker, long pauses fade, breaths dissolve, pages, ayah marker glue, ffmpeg graph construction |
| `tests/test_scene_manager.py` | 8 | Word-pop scene cutting |
| `tests/test_sync_windows.py` | 8 | Word-pop display windows |
| `tests/test_sync_tail.py` | 2 | Dropping inaudible words crammed at the end of a cut clip |
| `tests/test_word_render.py` | 1 | Word-pop Arabic rendering through Chromium (needs `playwright install chromium`) |

No network or ML models are needed; the locator tests use small synthetic ASR word lists.

---

## Sync benchmark (exact ground truth)

```bash
python tests/bench_sync.py          # all cases, ~5–10 minutes
python tests/bench_sync.py B C      # selected cases
```

Needs network (downloads per-ayah MP3s from [everyayah.com](https://everyayah.com), cached in `tmp/bench_cache/`) and the models.

**How it works:** each everyayah file is exactly one ayah, so when the benchmark stitches files together it knows the true start time of every recited ayah. It then runs the real sync and measures how far each predicted start is from the truth.

| Case | Scenario | Reciter |
|---|---|---|
| A | 3 s of silence, then 75:6–12 (late start) | Alafasy |
| B | 75:6, 7, **7 again**, 8, 9 (full repeat) | Alafasy |
| C | 67:1, 2, **last 40% of 2 again**, 3 (partial repeat) | Alafasy |
| D | Isti'adha + basmala + 112:1–2, **4 s pause**, 112:3–4 | Alafasy |
| E | 75:6–9, then 75:10 **cut in half** | Alafasy |
| F | 97:1, 2, 3, **2, 3 again**, 4 (two-ayah repeat) | Alafasy |
| A-husary / B-husary | Cases A and B, slower reciter | Husary |

A case **passes** when the surah is detected correctly, no recitation is missed or invented, and start-time error is ≤ 0.3 s median / ≤ 0.6 s max.

Current results:

```
  A          PASS  median 0.01s  max 0.03s
  B          PASS  median 0.016s max 0.03s
  C          PASS  median 0.083s max 0.1s
  D          PASS  median 0.037s max 0.071s
  E          PASS  median 0.012s max 0.03s
  F          PASS  median 0.026s max 0.07s
  A-husary   PASS  median 0.021s max 0.031s
  B-husary   PASS  median 0.022s max 0.033s
  8/8 passed
```

**Re-run the benchmark after changing anything in `modules/sync.py`, `modules/quran_align.py`, or the `sync:` config.**

### Adding a case

Add a builder in `tests/bench_sync.py` using the `Track` helper, and register it in `CASES`:

```python
def case_G(r):  # e.g. repeat after a long pause
    t = Track(r)
    t.ayah(36, 1)
    t.silence(6.0)
    t.ayah(36, 1, note="repeat")
    t.ayah(36, 2)
    return t, 36          # track + expected surah

CASES["G"] = (case_G, "Alafasy_128kbps")
```

`t.ayah(surah, ayah, frac_from=0.6)` plays only the last 40% of an ayah (partial repeat); `frac_to=0.5` cuts it in half. Reciter folder names come from everyayah.com (e.g. `Minshawy_Murattal_128kbps`).

---

## Checking a real YouTube run

There's no ground truth for YouTube clips, so check these by eye:

1. Read the **Recitation map** printed during sync — do the ayah ranges and `(repeat)` markers match what you hear?
2. Look at `tmp/timed_words.json` if the run failed before cleanup.
3. Watch the output: each ayah should appear right as the reciter starts it.

---

## Legacy script

`test_arabic_render.py` is an old Pillow-based font check from before the browser renderer. It needs `pip install arabic-reshaper python-bidi` and doesn't test the current pipeline; prefer the unit tests above.

---

**Next:** [Troubleshooting →](08-troubleshooting.md)
