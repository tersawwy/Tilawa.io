# 1. Getting Started

This guide takes you from a fresh machine to your first rendered video, step by step. Budget about 15 minutes, most of which is downloads.

> **You don't need any API key.** Everything runs locally. Keys are optional extras (see [Step 6](#step-6--optional-extras)).

---

## Step 1 — Check your system

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 or newer | Developed and tested on 3.12 |
| ffmpeg | 5 or newer | Must be on your `PATH` (`ffmpeg -version` works) |
| Disk space | ~3 GB free | ~1.8 GB of ML models are downloaded once |
| RAM | 8 GB minimum, 16 GB recommended | The speech model uses ~2–3 GB |
| OS | macOS (Apple Silicon recommended), Linux | Windows should work but is untested |

**Apple Silicon Macs are the fastest setup**: the speech model runs on the GPU (MPS) and video encoding uses the hardware H.264 encoder automatically. On other machines everything still works, just slower (see [Performance](#what-to-expect)).

Install ffmpeg if you don't have it:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg

# Verify
ffmpeg -version
```

---

## Step 2 — Get the code

```bash
git clone https://github.com/tersawwy/Tilawa.io.git
cd Tilawa.io
```

---

## Step 3 — Create a virtual environment (recommended)

Keeping the project's packages separate avoids version clashes (this project pins `torchaudio<2.9`, for example).

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
```

---

## Step 4 — Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

- `requirements.txt` installs PyTorch, torchaudio, Hugging Face Transformers, yt-dlp, MoviePy, Pillow, Playwright, and a few small libraries.
- `playwright install chromium` downloads the headless browser used to **shape the Arabic text** (the Uthmani font needs a real text-shaping engine) and to draw the animated background.

---

## Step 5 — Render your first video

```bash
python main.py \
  --url "https://www.youtube.com/watch?v=7RHYNhg8xEg" \
  --duration 45 \
  --no-upload
```

That URL is a Minshawi recitation of Surah Al-Qiyamah; any Quran recitation on YouTube works.

**Always keep `--no-upload` while you're testing** — without it the pipeline tries to post to TikTok at the end.

### What happens on the first run

1. **Model downloads (first run only, ~1.8 GB)** into `~/.cache/huggingface/`:
   - `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` — listens to the recitation and times every word
   - `tarteel-ai/whisper-base-ar-quran` — a Quran-tuned Whisper used to recognise which surah/ayahs are recited
2. **Quran text index (~2 MB)** is built once and cached in `tmp/quran_clean.json`.
3. The four pipeline stages run and print progress:

```
[1/4] Sourcing — downloading audio (and video/B-roll if needed)...
[2/4] Sync — transcribing & aligning Quranic text...
    Detected: Surah 75, Ayahs 1–6
    Recitation map:
        0.16–  3.72s  basmala
        4.72– 44.14s  1:1 → 6:4
[3/4] Render — compositing 1080×1920 (portrait) video (style=mushaf)...
    Background: generative scene (theme=dawn)
[4/4] Upload — skipped (--no-upload flag set)
  Done! Video saved to: output/final_video.mp4
```

The **Recitation map** is worth reading: it shows exactly which ayahs (and words) the sync heard, and marks any repeats with `(repeat)`.

### What to expect

| Machine | 45-second clip, end to end |
|---|---|
| Apple M2, 16 GB | ~2–3 minutes (sync ~1–1.5 min, render ~35 s) |
| Modern x86 CPU, no GPU | roughly 2–4× longer |

The first run is slower because of the model downloads.

---

## Step 6 — Optional extras

### Real nature footage instead of the generative background

The default `ambient` background is a generative animated scene. If you'd rather use real stock footage (clouds, sea, forest…), get a free key at [pexels.com/api](https://www.pexels.com/api/) and put it in `.env`:

```bash
cp .env.example .env
# then edit .env:
PEXELS_API_KEY=your_real_key
```

`main.py` loads `.env` automatically. Real environment variables take precedence.

### Posting to TikTok

See **[TikTok Upload](04-tiktok-upload.md)** — it needs exported browser cookies and has important caveats.

---

## Step 7 — Check everything works (optional)

```bash
pip install pytest
python -m pytest tests -q
```

All tests run offline in a few seconds. See [Testing](07-testing.md) for the sync accuracy benchmark.

---

**Next:** [Usage Guide →](02-usage.md)
