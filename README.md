# Tilawa.io

Give it a YouTube recitation URL and it spits out a TikTok-ready video — 30 seconds, 9:16 portrait, Arabic text highlighting word-by-word as the reciter speaks, English translation below, blurred nature background. Then it uploads.

No paid APIs. Everything runs locally except the Pixabay background fetch (free key).

---

## How it works

Four modules in sequence:

1. **Sourcing** — pulls a 30-second audio clip from YouTube via yt-dlp, grabs a nature video from Pixabay. Falls back to black if Pixabay fails.
2. **Sync** — Whisper transcribes the audio to figure out which Surah and Ayah are being recited. Then fetches the exact Uthmani script and Sahih International translation from Quran.com's API. stable-ts aligns each word to its timestamp.
3. **Renderer** — builds the video frame-by-frame with Pillow + MoviePy. Blurred background, centered Arabic text with per-word highlight, English translation, waveform visualizer. Arabic goes through `arabic_reshaper` + `python-bidi` before hitting Pillow — Pillow can't handle raw Arabic.
4. **Uploader** — Playwright + headless Chromium logs into TikTok with your session cookies, fills in the caption and hashtags, and posts.

---

## Requirements

- Python 3.10+
- ffmpeg
- ImageMagick
- Fonts are included under `assets/fonts/`
- Optional: CUDA (faster Whisper; works fine on CPU, just slower)

---

## Installation

```bash
git clone https://github.com/tersawwy/Tilawa.io.git
cd Tilawa.io
pip install -r requirements.txt
playwright install chromium
```

**Set up your API keys:**

```bash
cp .env.example .env
```

Open `.env` and fill in at least one background provider key:

- **Pexels** (preferred) — free key at [pexels.com/api](https://www.pexels.com/api/)
- **Pixabay** (fallback) — free key at [pixabay.com/api/docs](https://pixabay.com/api/docs/)

If neither key is set, the pipeline still runs but uses a solid black background.

For TikTok uploads, export your browser session cookies in Netscape format to `cookies/tiktok.txt`.

---

## Usage

```bash
python main.py \
  --url "https://www.youtube.com/watch?v=..." \
  --surah 1 \
  --start-ayah 1 \
  --end-ayah 7 \
  --start-time "00:00:10"
```

Surah/ayah detection is automatic if you omit those flags — Whisper figures it out from the audio. `--start-time` controls where in the YouTube video to start cutting.

Skip the upload with `--no-upload` if you just want the rendered file.

### All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | required | YouTube URL |
| `--surah` | auto | Surah number (1–114) |
| `--start-ayah` | auto | First ayah |
| `--end-ayah` | auto | Last ayah |
| `--start-time` | `00:00:00` | Offset into the YouTube video |
| `--style` | `mushaf` | `mushaf` (full ayah) or `word-pop` (one word at a time) |
| `--aspect` | `portrait` | `portrait` (9:16) or `landscape` (16:9) |
| `--output` | `output/final_video.mp4` | Output path |
| `--no-upload` | — | Render only, skip TikTok |
| `--config` | `config.yaml` | Config file path |

---

## Configuration

`config.yaml` controls everything: font sizes, colors, layout positions (Arabic text sits at y=700px, English at y=1550px), Whisper model size, Pixabay key, and TikTok caption/hashtag templates. CLI flags `--style` and `--aspect` override their config equivalents.

---

## About the TikTok upload

Playwright-based uploading violates TikTok's ToS. Use a throwaway account, post in private mode while testing, and plan to refresh your session cookies every ~30 days. TikTok actively changes its UI, so the automation may break without warning.

---

## APIs

| API | Purpose | Auth |
|-----|---------|------|
| [Quran.com v4](https://api.qurancdn.com/) | Uthmani text + Sahih International translation | None |
| [Alquran.cloud](https://api.alquran.cloud/) | Fallback Quran data | None |
| [Pixabay](https://pixabay.com/api/videos/) | Background clips | Free key |

Translation ID 131 on Quran.com = Sahih International.

---

## Project structure

```
Tilawa.io/
├── main.py
├── config.yaml
├── requirements.txt
├── modules/
│   ├── sourcing.py       # download
│   ├── sync.py           # transcribe + align
│   ├── renderer.py       # video composition
│   ├── scene_manager.py  # scene layout
│   └── uploader.py       # TikTok
├── assets/fonts/
│   ├── KFGQPC-Uthmanic-HAFS.otf
│   └── Montserrat-Regular.ttf
├── tests/
├── output/               # rendered videos
└── tmp/                  # intermediate files (gitignored)
```

---

## Testing

Before running the full pipeline, check that Arabic rendering works:

```bash
python test_arabic_render.py
```

This renders a test frame locally — no downloads, no API calls. Catches font and shaping issues before you wait through a full transcription run.
