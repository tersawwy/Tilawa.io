# 2. Usage Guide

Everything is driven by one command: `python main.py`. This page covers every flag, common recipes, and how to pick a good clip.

---

## The basic command

```bash
python main.py --url "<YouTube URL>" --duration 45 --no-upload
```

- `--url` is the only required flag.
- The surah and ayahs are **detected automatically** from the audio.
- The result is written to `output/final_video.mp4`.

---

## All flags

| Flag | Default | What it does |
|---|---|---|
| `--url` | *(required)* | YouTube video URL |
| `--start-time` | `00:00:00` | Where to start cutting in the YouTube video (`HH:MM:SS`, `MM:SS` or seconds) |
| `--duration` | `video.duration` in config (58) | How many seconds to cut |
| `--surah` | auto | Override: surah number (1–114) |
| `--start-ayah` | auto | Override: first ayah |
| `--end-ayah` | auto | Override: last ayah |
| `--background` | `ambient` | `ambient` = calm moving scene · `video` = the YouTube video itself, softly blurred |
| `--style` | `mushaf` | `mushaf` = whole ayah on screen · `word-pop` = one word at a time (legacy) |
| `--aspect` | `portrait` | `portrait` (1080×1920, TikTok/Reels/Shorts) or `landscape` (1920×1080) |
| `--output` | `output/final_video.mp4` | Output file path |
| `--no-upload` | off | Render only; skip posting to TikTok |
| `--config` | `config.yaml` | Use a different config file |

`--style`, `--aspect` and `--background` override the matching values in [`config.yaml`](03-configuration.md).

---

## Recipes

### 1. Fully automatic (recommended starting point)

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --duration 45 --no-upload
```

### 2. Start from a specific point in a long video

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --start-time 00:12:30 --duration 50 --no-upload
```

Tip: start 1–2 seconds **before** the reciter begins an ayah so the first ayah fades in naturally.

### 3. Use the sheikh's own video as the background

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --duration 45 --background video --no-upload
```

The video plays **sharp** until just before the first recited word, then dissolves into a soft, dimmed blur so the Arabic text is easy to read. (Turn the sharp intro off with `background.video_sharp_intro: false`.) The blur also hides any text that is already burned into the source video.

### 4. Calm ambient background (default)

```bash
python main.py --url "..." --duration 45 --background ambient --no-upload
```

- **No Pexels key:** a generative animated scene — slow aurora light, a soft beam from above and drifting particles that gently brighten with the reciter's voice. The palette (night / dawn / emerald) is picked per surah, or set it with `render.theme`.
- **With a Pexels key** (in `.env`): real nature footage with slow dissolves and a warm grade.

### 5. Force the surah/ayah range

If auto-detection picks the wrong passage (rare, but possible with noisy audio), give all three overrides:

```bash
python main.py --url "..." --duration 45 \
  --surah 67 --start-ayah 1 --end-ayah 5 --no-upload
```

> The override only applies when **all three** of `--surah`, `--start-ayah` and `--end-ayah` are given.

Repeats and pauses are still detected from the audio inside that range.

### 6. Landscape video (YouTube)

```bash
python main.py --url "..." --duration 60 --aspect landscape --no-upload
```

### 7. Render and post to TikTok

```bash
python main.py --url "..." --duration 45
```

Requires cookies — read [TikTok Upload](04-tiktok-upload.md) first, and set `tiktok.privacy: "private"` for test posts.

### 8. Several variants of the same clip

```bash
python main.py --url "..." --duration 45 --background video   --output output/clip_video.mp4   --no-upload
python main.py --url "..." --duration 45 --background ambient --output output/clip_ambient.mp4 --no-upload
```

---

## What you get

- **The video**: H.264 + AAC MP4 at 30 fps, `+faststart` (ready for web upload).
- **On screen**: each ayah fades in as a whole with a gentle upward drift when the reciter starts it, in the Scheherazade New font with a gold ayah-number marker; the surah name sits small at the top. The basmala is shown when it is recited; the isti'adha is not.
- **Repeats**: if the reciter repeats an ayah (or part of one), the ayah stays on screen / comes back exactly when he restarts it.
- **Pauses**: short breaths keep the ayah on screen; after a pause longer than 4 seconds it fades out and returns when recitation resumes.

### Temporary files

Working files go to `tmp/` (audio, timings, background, text images). `tmp/` is **wiped automatically** at the start and end of each run, except `tmp/quran_clean.json` (the Quran text cache). If a run crashes, the files from that run stay in `tmp/` until the next run — useful for debugging:

| File | Contents |
|---|---|
| `tmp/audio.mp3` | The cut audio |
| `tmp/timed_words.json` | Every displayed word with its start/end time |
| `tmp/sync_meta.json` | Detected surah and ayah range (used for the TikTok caption) |
| `tmp/bg.mp4` | The rendered background layer |
| `tmp/text/*.png` | One transparent image per ayah (or per page of a long ayah) |

---

## Choosing a good clip

The sync is robust to repeats, pauses, late starts and audio cut mid-ayah. What makes the biggest difference:

| Works great | Works, with care | Hard |
|---|---|---|
| Studio *murattal* recitation (Alafasy, Husary, Minshawi murattal) | Live prayer / taraweeh with some echo | Heavy *mujawwad* (very long melodic stretches + strong reverb + crowd reactions) |
| Clear voice, little music/effects | Background talk or audience between ayahs | Background nasheed/music under the recitation |

For hard clips, use the manual overrides (recipe 5) and check the **Recitation map** in the output. See [Troubleshooting](08-troubleshooting.md).

### Length

TikTok favours 30–60 s. Recitation pace varies a lot: 45 s might be 6 short ayahs or half of a long one. Long ayahs are automatically shrunk to fit and, if still too long, split into pages that turn with the recitation.

---

**Next:** [Configuration →](03-configuration.md)
