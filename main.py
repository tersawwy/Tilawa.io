#!/usr/bin/env python3
"""
QuranAI — TikTok Pipeline
Usage (fully automatic — surah/ayah detected from the audio):
    python main.py --url "https://youtube.com/watch?v=..." --duration 45

Optional overrides:
    python main.py --url "..." --duration 45 \
                   --surah 1 --start-ayah 1 --end-ayah 7 \
                   --start-time "00:00:10"
"""

import argparse
import os
import yaml


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_dotenv(path: str = ".env"):
    """Load KEY=value lines from .env into the environment (existing
    environment variables win). Placeholder values from .env.example are
    ignored."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if value and not value.startswith("your_"):
                os.environ.setdefault(key, value)


def parse_args():
    parser = argparse.ArgumentParser(
        description="QuranAI — Generate and post Quran recitation TikTok videos"
    )
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument("--surah", type=int, default=None, help="(Optional) Override: Surah number (1-114)")
    parser.add_argument("--start-ayah", type=int, default=None, help="(Optional) Override: First ayah")
    parser.add_argument("--end-ayah", type=int, default=None, help="(Optional) Override: Last ayah")
    parser.add_argument(
        "--start-time",
        default="00:00:00",
        help="Start offset in YouTube video (HH:MM:SS), default 00:00:00",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Seconds of audio to clip from the video. Overrides video.duration in config.yaml.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip TikTok upload — render only",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--output",
        default="output/final_video.mp4",
        help="Output video path (default: output/final_video.mp4)",
    )
    parser.add_argument(
        "--style",
        choices=["mushaf", "word-pop"],
        default=None,
        help="Render style: 'mushaf' (full ayah, default) or 'word-pop' (one word at a time). "
             "Overrides render.style in config.yaml.",
    )
    parser.add_argument(
        "--background",
        choices=["video", "ambient"],
        default=None,
        help="Mushaf background: 'video' (the YouTube video itself, softly blurred) or "
             "'ambient' (calm moving scene: Pexels footage if a key is set, else generative). "
             "Overrides render.background in config.yaml.",
    )
    parser.add_argument(
        "--aspect",
        choices=["portrait", "landscape"],
        default=None,
        help="Output aspect ratio: 'portrait' (9:16, 1080×1920) or 'landscape' (16:9, 1920×1080). "
             "Overrides output.aspect in config.yaml.",
    )
    return parser.parse_args()


def ensure_dirs():
    for d in ["tmp", "output", "cookies"]:
        os.makedirs(d, exist_ok=True)


def clean_tmp():
    """Remove per-run artifacts from tmp/ so a new video never reuses stale
    audio, timings, or scene data. quran_clean.json is a durable Quran-text
    cache (not per-run) and is kept."""
    import shutil

    keep = {"quran_clean.json"}
    for name in os.listdir("tmp"):
        if name in keep:
            continue
        path = os.path.join("tmp", name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def main():
    args = parse_args()
    load_dotenv()
    config = load_config(args.config)
    ensure_dirs()
    clean_tmp()

    # CLI flags override config.yaml settings
    style  = args.style  or config.get("render", {}).get("style", "mushaf")
    aspect = args.aspect or config.get("output", {}).get("aspect", "portrait")
    background = args.background or config.get("render", {}).get("background", "ambient")
    if args.duration:
        config["video"]["duration"] = args.duration

    # Inject resolved values back into config so downstream modules see them
    config.setdefault("render",  {})["style"]  = style
    config.setdefault("output",  {})["aspect"] = aspect

    dims = "1080×1920 (portrait)" if aspect == "portrait" else "1920×1080 (landscape)"

    print("\n" + "=" * 60)
    print("  QuranAI Pipeline Starting")
    print("=" * 60)
    print(f"  YouTube URL  : {args.url}")
    print(f"  Surah        : {args.surah}")
    print(f"  Ayahs        : {args.start_ayah} → {args.end_ayah}")
    print(f"  Start time   : {args.start_time}")
    print(f"  Duration     : {config['video']['duration']}s")
    print(f"  Style        : {style}")
    if style != "word-pop":
        print(f"  Background   : {background}")
    print(f"  Aspect       : {aspect}  ({dims})")
    print(f"  Output       : {args.output}")
    print("=" * 60 + "\n")

    # ── Module 1: Sourcing ──────────────────────────────────────
    print("[1/4] Sourcing — downloading audio (and video/B-roll if needed)...")
    from modules.sourcing import download_audio, download_video, fetch_cinematic_clips

    audio_path = download_audio(
        url=args.url,
        start_time=args.start_time,
        duration=config["video"]["duration"],
        output_path="tmp/audio.mp3",
    )

    clips, background_path, video_path = None, None, None
    if style == "word-pop":
        n_clips = config.get("sourcing", {}).get("clips_per_video", 8)
        clips = fetch_cinematic_clips(config, n_clips=n_clips)
        background_path = clips[0] if clips else None
        print(f"    B-roll clips: {len(clips)} clip(s) fetched")
    elif background == "video":
        video_path = download_video(
            url=args.url,
            start_time=args.start_time,
            duration=config["video"]["duration"],
            output_path="tmp/source_video.mp4",
        )
        print(f"    Video      : {video_path}")
    # ambient backgrounds are built at render time (they follow the audio)

    print(f"    Audio      : {audio_path}\n")

    # ── Module 2: Text & Synchronization ────────────────────────
    print("[2/4] Sync — transcribing & aligning Quranic text...")
    from modules.sync import build_timed_words

    timed_words_path = build_timed_words(
        audio_path=audio_path,
        config=config,
        output_path="tmp/timed_words.json",
        surah=args.surah,
        start_ayah=args.start_ayah,
        end_ayah=args.end_ayah,
    )
    print(f"    Timed words: {timed_words_path}\n")

    # ── Module 3: Video Rendering ────────────────────────────────
    print(f"[3/4] Render — compositing {dims} video (style={style})...")
    from modules.renderer import render_video

    output_path = render_video(
        background_path=background_path,
        audio_path=audio_path,
        timed_words_path=timed_words_path,
        config=config,
        output_path=args.output,
        style=style,
        clips=clips,
        background_mode=background,
        video_path=video_path,
    )
    print(f"    Output     : {output_path}\n")

    # ── Module 4: TikTok Upload ──────────────────────────────────
    if args.no_upload:
        print("[4/4] Upload — skipped (--no-upload flag set)")
    else:
        print("[4/4] Upload — posting to TikTok...")
        import json

        from modules.uploader import upload_to_tiktok
        from modules.sync import get_surah_name

        surah = args.surah
        if surah is None:
            # Auto-detected by the sync module — read it back
            with open("tmp/sync_meta.json", "r", encoding="utf-8") as f:
                surah = json.load(f)["surah"]

        surah_name_ar, surah_name_en = get_surah_name(surah)
        caption = config["tiktok"]["caption"].format(
            surah_name_ar=surah_name_ar,
            surah_name_en=surah_name_en,
        )
        hashtags = config["tiktok"]["hashtags"]

        url = upload_to_tiktok(
            video_path=output_path,
            cookies_file=config["tiktok"]["cookies_file"],
            caption=f"{caption}\n{hashtags}",
            privacy=config["tiktok"]["privacy"],
        )
        print(f"    Posted     : {url}\n")

    # ── Cleanup — per-run tmp artifacts no longer needed once output exists ──
    clean_tmp()

    print("=" * 60)
    print("  Done! Video saved to:", output_path)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
