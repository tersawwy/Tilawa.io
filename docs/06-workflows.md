# 6. Workflows

How a run flows through the system, stage by stage.

---

## Workflow 1: End-to-end run

```mermaid
sequenceDiagram
    actor U as Creator
    participant M as main.py
    participant S as Sourcing
    participant Y as Sync
    participant R as Renderer
    participant T as Uploader

    U->>M: python main.py --url … --duration 45
    M->>M: load .env + config.yaml, apply CLI overrides
    M->>M: clean tmp/ (keep Quran cache)
    M->>S: download_audio (+ download_video if --background video)
    S-->>M: tmp/audio.mp3 (tmp/source_video.mp4)
    M->>Y: build_timed_words(audio)
    Y-->>M: tmp/timed_words.json + tmp/sync_meta.json
    M->>R: render_video(style, background)
    R-->>M: output/final_video.mp4
    alt without --no-upload
        M->>T: upload_to_tiktok(video, caption from sync_meta)
        T-->>M: post URL
    end
    M->>M: clean tmp/
    M-->>U: "Done! Video saved to …"
```

**Steps**

1. **Configure.** `.env` → environment, `config.yaml` loaded, CLI flags override `style`, `aspect`, `background`, `duration`.
2. **Clean.** `tmp/` is emptied so no stale audio/timings leak into a new video.
3. **Source.** yt-dlp cuts exactly the requested section. If YouTube refuses the section download (HTTP 403), the full track is downloaded and trimmed locally.
4. **Sync.** Produces the timed word list (see Workflow 2).
5. **Render.** Produces the video (Workflow 3).
6. **Upload** (optional). Caption uses the surah detected in step 4.
7. **Clean** `tmp/` again.

---

## Workflow 2: Sync — "listen, locate, align"

```mermaid
flowchart TB
    A["tmp/audio.mp3"] --> B["wav2vec2 CTC<br/>frame-level emissions (20 ms)"]
    B --> C["CTC greedy words<br/>exact times, keeps repeats"]
    A --> D["Energy VAD<br/>breath-group utterances"]
    D --> E["Quran Whisper per utterance<br/>clean words"]
    C & E --> F{"Surah/ayah detection<br/>bigram search over whole Quran"}
    F --> G["Quran.com words<br/>detected range ± 2 ayahs<br/>+ isti'adha / basmala"]
    C --> H1["merge fragments → locate → segments"]
    E --> H2["locate → segments"]
    G --> H1 & H2
    H1 --> I1["CTC forced align<br/>per segment"]
    H2 --> I2["CTC forced align<br/>per segment"]
    I1 & I2 --> J{"keep source with<br/>more aligned words"}
    J --> K["tmp/timed_words.json"]
```

**Steps**

1. **Listen.** The wav2vec2 model produces per-frame letter probabilities for the whole clip; a greedy decode gives words with exact times. Separately the audio is split at pauses and each breath group is transcribed by the Quran-tuned Whisper.
2. **Detect** the surah and ayah range by comparing the transcripts with every window of the Quran (character-bigram overlap). Skipped when `--surah/--start-ayah/--end-ayah` are given.
3. **Locate.** A Viterbi search assigns every heard word to a position in the Quran text. Allowed moves: next word, skip a missed word, **jump back (repeat — whole or partial ayah)**, jump ahead, or *not Quran* (dua, talk, applause).
4. **Segment.** Runs of consecutive positions become segments; every jump back starts a new one (a repeat).
5. **Align.** Each segment's exact Quran text is force-aligned on its own audio window → precise word start/end. Segment edges are extended to the ayah boundary when the audio supports it (the recogniser often misses an ayah's first word).
6. **Choose** whichever word source produced more confidently aligned words.
7. **Save** timed words and `sync_meta.json`; print the recitation map.

Details: [Deep Dive: Sync](deep-dive/sync.md).

---

## Workflow 3: Rendering (mushaf style)

```mermaid
sequenceDiagram
    participant R as renderer.py
    participant B as backgrounds.py
    participant C as Chromium
    participant A as ayah_layer.py
    participant F as ffmpeg

    R->>B: build_background(mode)
    alt --background video
        B->>F: cover-crop, sharp intro → blur + dim + slow zoom
    else ambient + Pexels key
        B->>F: stock clips, drift, warm grade, dissolves
    else ambient (default)
        B->>C: draw generative scene per frame (parallel workers)
        C-->>F: JPEG frames → upscale + grain
    end
    F-->>R: tmp/bg.mp4
    R->>A: render_text_images(unique ayahs)
    A->>C: shape Uthmani text, auto-fit / paginate, screenshot
    C-->>A: tmp/text/ayah_*.png + title.png
    R->>A: build_intervals(timed_words)
    R->>A: build_compose_cmd(...)
    A->>F: one filter graph: bg + vignette + title + ayah fades + audio
    F-->>R: output/final_video.mp4
```

The `word-pop` style instead renders frame by frame with MoviePy over B-roll scenes (see [Deep Dive: Rendering](deep-dive/rendering.md#legacy-word-pop-style)).

---

## Workflow 4: TikTok upload

```mermaid
sequenceDiagram
    participant M as main.py
    participant U as uploader.py
    participant P as Headless Chromium
    participant T as TikTok

    M->>U: upload_to_tiktok(video, cookies, caption, privacy)
    U->>P: launch, load Netscape cookies
    P->>T: open /creator-center/upload
    alt redirected to login
        T-->>U: login page → error "cookies may be expired"
    end
    P->>T: attach video file
    P->>T: wait for processing
    P->>T: type caption (human-like delays), set privacy
    P->>T: click Post
    T-->>U: profile URL
    U-->>M: post URL
```

---

## Data flow

```mermaid
flowchart LR
    url["YouTube URL"] --> audio["tmp/audio.mp3"]
    url -.video bg.-> vid["tmp/source_video.mp4"]
    audio --> tw["tmp/timed_words.json"]
    audio --> meta["tmp/sync_meta.json"]
    qapi["Quran.com words"] --> tw
    tw --> png["tmp/text/*.png"]
    tw --> iv["display intervals"]
    vid --> bg["tmp/bg.mp4"]
    audio -.loudness.-> bg
    bg & png & iv & audio --> out["output/final_video.mp4"]
    meta --> cap["TikTok caption"]
    out & cap --> tt["TikTok post"]
```

### `timed_words.json` format

```json
[
  {
    "ayah_number": 2,
    "word_index": 0,
    "text": "ٱلْحَمْدُ",
    "english_text": "[All] praise is [due] to Allah, Lord of the worlds -",
    "start": 1.96,
    "end": 2.84,
    "segment_id": 0,
    "confidence": -0.412,
    "display_start": 1.96,
    "display_end": 2.84
  }
]
```

- `ayah_number` `0` = basmala (shown without an ayah marker).
- A repeated ayah appears again with a higher `segment_id`.
- `display_start/end` are used by the word-pop style.

---

## State management

There is no persistent state besides two caches:

- `tmp/quran_clean.json` — diacritic-free Quran text for surah detection (kept between runs).
- `~/.cache/huggingface/` — the downloaded models.

Everything else is per-run and deleted at the start and end of each run.

## Error handling

| Situation | Behaviour |
|---|---|
| YouTube 403 on section download | Automatic fallback: download full track, trim locally |
| No Arabic recognised / detection confidence < 25% | Stops with a message asking for `--surah/--start-ayah/--end-ayah` |
| Quran Whisper fails to load | Continues with CTC words only |
| A segment aligns badly | Falls back to rough timing; tiny weak segments are dropped |
| Pexels returns nothing | Falls back to the generative scene |
| No hardware encoder | Falls back to x264 |
| ffmpeg compose fails | Raises with the last 2,000 characters of ffmpeg's error output |
| TikTok UI element not found | Logs a `Warning:` and continues; login redirect raises |

---

**Next:** [Testing →](07-testing.md)
