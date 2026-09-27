# 3. Configuration Reference

All settings live in [`config.yaml`](../config.yaml). Command-line flags override a few of them (noted below). Use a different file with `--config my_config.yaml`.

Secrets (API keys) can go in `.env` instead of `config.yaml` — see [Environment variables](#environment-variables).

---

## `video`

| Key | Default | Description |
|---|---|---|
| `width` | `1080` | Portrait output width (landscape is always 1920×1080) |
| `height` | `1920` | Portrait output height |
| `fps` | `30` | Output frame rate |
| `duration` | `58` | Seconds to cut from YouTube. **Overridden by `--duration`** |

## `arabic`

| Key | Default | Description |
|---|---|---|
| `font` | `assets/fonts/ScheherazadeNew-Regular.ttf` | Quran font (SIL Scheherazade New; supports all Uthmani marks). `KFGQPC-Uthmanic-HAFS.otf` is the mushaf-style alternative — change with care |
| `size` | `90` | Base font size for the legacy `word-pop` style (the default style auto-sizes; see `render.text`) |

## `background`

| Key | Default | Applies to | Description |
|---|---|---|---|
| `blur` | `10` | `--background video` | Blur strength (applied at ⅓ resolution; 10 ≈ strong soft blur) |
| `video_brightness` | `-0.14` | `--background video` | Dimming (−1…1; more negative = darker) |
| `video_sharp_intro` | `true` | `--background video` | Show the video sharp until recitation starts, then dissolve to the blur |
| `brightness` | `0.78` | `word-pop` | B-roll darkening factor |

## `sourcing`

Used for the Pexels footage background and the `word-pop` B-roll.

| Key | Default | Description |
|---|---|---|
| `pexels_api_key` | `""` | Pexels key. Prefer `PEXELS_API_KEY` in `.env` |
| `pixabay_api_key` | `""` | Pixabay key (word-pop B-roll fallback only). Prefer `PIXABAY_API_KEY` |
| `background_queries` | forest, ocean, mountains, rain, desert, river | Search terms for stock footage |
| `clips_per_video` | `8` | How many clips to fetch |
| `min_clip_width` | `1920` | Minimum source clip width (quality filter) |

## `output`

| Key | Default | Description |
|---|---|---|
| `aspect` | `portrait` | `portrait` (1080×1920) or `landscape` (1920×1080). **Overridden by `--aspect`** |

## `render`

| Key | Default | Description |
|---|---|---|
| `style` | `mushaf` | `mushaf` (whole ayah) or `word-pop` (legacy). **Overridden by `--style`** |
| `background` | `ambient` | `ambient` or `video`. **Overridden by `--background`** |
| `theme` | `auto` | Generative scene palette: `night`, `dawn`, `emerald`, or `auto` (picked from the surah number, so the same surah always looks the same) |

### `render.text` (mushaf style)

| Key | Default | Description |
|---|---|---|
| `color` | `#F4EEDD` | Ayah text colour (warm ivory) |
| `accent` | `#D8B46A` | Ayah marker and surah title colour (muted gold) |
| `max_size` | `108` | Largest font size (px). Short ayahs use this |
| `min_size` | `64` | Smallest font size. Ayahs that don't fit even at this size are split into pages |

Advanced layout values (text zone position/size, title position, line height) have sensible defaults in `DEFAULT_TEXT` in [`modules/ayah_layer.py`](../modules/ayah_layer.py) and can also be set under `render.text`: `zone_center`, `zone_height`, `zone_width`, `title_y`, `line_height`.

### `render.word_pop` (legacy style)

| Key | Default | Description |
|---|---|---|
| `fade_duration` | `0.08` | Fade in/out per word (s) |
| `word_y_ratio_portrait` | `0.45` | Vertical word position (fraction of height), portrait |
| `word_y_ratio_landscape` | `0.50` | Same, landscape |
| `font_scale_landscape` | `0.75` | Font scale in landscape |

## `tiktok`

| Key | Default | Description |
|---|---|---|
| `cookies_file` | `cookies/tiktok_cookies.txt` | Netscape-format cookies exported from a logged-in browser |
| `caption` | `{surah_name_ar} \| Surah {surah_name_en}` | Caption template. Placeholders are filled from the detected surah |
| `hashtags` | `#quran #recitation …` | Appended on a new line after the caption |
| `privacy` | `public` | `public`, `friends` or `private`. **Use `private` while testing** |

## `sync`

Controls how the recitation is heard and aligned. The defaults were tuned on a ground-truth benchmark (see [Testing](07-testing.md)); change them only if you have a reason, and re-run the benchmark after.

| Key | Default | Description |
|---|---|---|
| `ctc_model` | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | Arabic speech model that hears words and gives exact timings |
| `asr_model` | `tarteel-ai/whisper-base-ar-quran` | Quran-tuned Whisper, used for surah detection and clean words in melodic recitation |
| `search_margin_ayahs` | `2` | Extra ayahs searched on each side of the detected range |
| `min_segment_confidence` | `-2.5` | Alignment confidence below which a segment falls back to rough timing (or is dropped if tiny) |
| `repeat_cost` | `1.5` | How reluctant the locator is to decide the reciter jumped back (repeat). Lower = detects repeats more eagerly |
| `skip_cost` | `0.8` | Cost per Quran word the recogniser missed |
| `junk_enter` | `1.2` | Cost of deciding something is *not* Quran text (dua, talk, applause). Lower = more speech ignored |

Other locator parameters (all fields of `LocateConfig` in [`modules/quran_align.py`](../modules/quran_align.py)) can be added under `sync:` with the same names, e.g. `jump_cost`, `max_skip`, `stay_cost`.

---

## Environment variables

`main.py` reads a `.env` file in the project root on start-up (copy [`.env.example`](../.env.example)). Variables already set in your shell win over `.env`.

| Variable | Used for |
|---|---|
| `PEXELS_API_KEY` | Real nature footage for `--background ambient` (and word-pop B-roll) |
| `PIXABAY_API_KEY` | Word-pop B-roll fallback |

Hugging Face model downloads use the standard `HF_HOME` / `HF_HUB_CACHE` variables if you want the ~1.8 GB of models somewhere other than `~/.cache/huggingface`.

---

**Next:** [TikTok Upload →](04-tiktok-upload.md)
