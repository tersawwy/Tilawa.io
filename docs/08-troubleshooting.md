# 8. Troubleshooting & FAQ

---

## Installation

### `playwright._impl._errors.Error: Executable doesn't exist`
Chromium wasn't downloaded. Run:
```bash
playwright install chromium
```

### `AttributeError: module 'torchaudio.functional' has no attribute 'forced_align'`
torchaudio 2.9 removed `forced_align`. Install a supported version:
```bash
pip install "torchaudio>=2.1,<2.9"
```
(Keep torch and torchaudio on matching versions, e.g. `torch==2.7.1 torchaudio==2.7.1`.)

### `A module that was compiled using NumPy 1.x cannot be run in NumPy 2.x`
An older compiled package (often matplotlib/contourpy, pulled in by MoviePy) doesn't match your NumPy. Upgrade them:
```bash
pip install -U matplotlib contourpy
```
Using a fresh virtual environment (see [Getting Started](01-getting-started.md#step-3--create-a-virtual-environment-recommended)) avoids most of these.

### `ffmpeg: command not found`
Install ffmpeg and make sure it's on your `PATH` ([Step 1](01-getting-started.md#step-1--check-your-system)).

---

## Downloading

### `HTTP error 403 Forbidden` from yt-dlp
YouTube sometimes refuses partial ("section") downloads. The pipeline automatically retries by downloading the full track and trimming it locally — you'll see `Section download failed — downloading full audio and trimming...`. If the full download fails too, update yt-dlp (YouTube changes often):
```bash
pip install -U yt-dlp
```

### Age-restricted / private / region-locked videos
yt-dlp can't fetch them without login. Pick another upload of the same recitation.

---

## Sync

### `Low confidence (0.xx) — audio may be noisy or not Quranic`
Auto-detection couldn't identify the passage. Tell it the surah and range:
```bash
python main.py --url "..." --surah 93 --start-ayah 1 --end-ayah 11 --no-upload
```
All three flags are required for the override to apply.

### The first ayah appears late, or some ayahs are missing
- Check the **Recitation map** in the output — it lists exactly what was aligned.
- Heavily melodic *mujawwad* recitation with strong reverb and crowd reactions is the hardest case; the sync is partial there. Use the manual overrides and prefer clearer uploads.
- Music or nasheed under the voice confuses the speech models.

### The basmala isn't shown
It's shown whenever it's recited. In prayer (taraweeh), many imams recite it silently — so there's nothing to subtitle.

### The isti'adha (أعوذ بالله…) isn't shown
By design: it's not part of the Quran text, so it's recognised (to keep timing right) but never subtitled.

### It's slow
- First run downloads ~1.8 GB of models — later runs skip this.
- Sync runs on the Apple GPU (MPS) automatically; on other machines it uses the CPU. A 45 s clip takes ~1–1.5 min on an M2, several minutes on older CPUs.
- Shorter clips (`--duration 30`) are proportionally faster.

---

## Rendering

### Output is slower to render on Linux/Windows
The hardware encoder is used only when ffmpeg has `h264_videotoolbox` (macOS). Elsewhere x264 `veryfast` is used automatically — correct output, a few times slower.

### `ffmpeg compose failed: …`
The error ends with ffmpeg's own message. Common causes: an ffmpeg build without `libx264` (install a full build, e.g. `brew install ffmpeg`), or a very old ffmpeg (< 5).

### Text looks wrong (broken letters, missing marks)
Make sure `arabic.font` points to `assets/fonts/ScheherazadeNew-Regular.ttf` (or `KFGQPC-Uthmanic-HAFS.otf`). Other fonts may not match the Quran.com Uthmani encoding. Don't add CSS bold/stroke to the text — it breaks this font's rendering.

### I want real nature footage but get the animated scene
`PEXELS_API_KEY` isn't set (or is still the placeholder). Put your real key in `.env` or `sourcing.pexels_api_key`. The log line `Background: …` tells you which background was used.

### The sheikh video is sharp for only a second
The sharp intro lasts until 1 s before the first recited word. If your clip starts mid-recitation, it's blurred from the start. Use an earlier `--start-time` to get a longer intro, or disable it with `background.video_sharp_intro: false`.

---

## Uploading

See the table in [TikTok Upload](04-tiktok-upload.md#keeping-it-working). Most issues are expired cookies (re-export them) or TikTok UI changes (upload manually from `output/`).

---

## FAQ

**Does it need the internet?**
Yes for YouTube, the Quran APIs and the first model download. The models themselves run locally.

**Does it cost anything?**
No. There are no paid APIs. Pexels/Pixabay keys are free and optional.

**Which translation / text is used?**
Word-by-word Uthmani text from Quran.com (King Fahd Complex Hafs encoding). The on-screen video is Arabic only.

**Can I use it for Instagram Reels / YouTube Shorts?**
Yes — the default 1080×1920 MP4 fits all three. Use `--aspect landscape` for regular YouTube.

**Where are my videos?**
`output/final_video.mp4`, or wherever `--output` points. `output/` is git-ignored.

---

Still stuck? Open an issue with the full console output and the YouTube URL/start time you used.
