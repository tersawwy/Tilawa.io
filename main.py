#!/usr/bin/env python3
"""
QuranAI — TikTok Pipeline
Usage:
    python main.py --url "https://youtube.com/watch?v=..." \
                   --surah 1 --start-ayah 1 --end-ayah 7 \
                   --start-time "00:00:10"
"""

import argparse
import os
import yaml


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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


def main():
    args = parse_args()
    config = load_config(args.config)
    ensure_dirs()

    # CLI flags override config.yaml settings
    style  = args.style  or config.get("render", {}).get("style", "mushaf")
    aspect = args.aspect or config.get("output", {}).get("aspect", "portrait")

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
    print(f"  Style        : {style}")
    print(f"  Aspect       : {aspect}  ({dims})")
    print(f"  Output       : {args.output}")
    print("=" * 60 + "\n")

    # ── Module 1: Sourcing ──────────────────────────────────────
    print("[1/4] Sourcing — downloading audio & background(s)...")
    from modules.sourcing import download_audio, fetch_background, fetch_cinematic_clips

    audio_path = download_audio(
        url=args.url,
        start_time=args.start_time,
        duration=config["video"]["duration"],
        output_path="tmp/audio.mp3",
    )

    if style == "word-pop":
        n_clips = config.get("sourcing", {}).get("clips_per_video", 8)
        clips = fetch_cinematic_clips(config, n_clips=n_clips)
        background_path = clips[0] if clips else None  # fallback for mushaf args
        print(f"    B-roll clips: {len(clips)} clip(s) fetched")
    else:
        clips = None
        background_path = fetch_background(
            api_key=config["background"]["pixabay_api_key"],
            query=config["background"]["search_query"],
            output_path="tmp/background.mp4",
        )
        print(f"    Background : {background_path}")

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
    )
    print(f"    Output     : {output_path}\n")

    # ── Module 4: TikTok Upload ──────────────────────────────────
    if args.no_upload:
        print("[4/4] Upload — skipped (--no-upload flag set)")
    else:
        print("[4/4] Upload — posting to TikTok...")
        from modules.uploader import upload_to_tiktok
        from modules.sync import get_surah_name

        surah_name_ar, surah_name_en = get_surah_name(args.surah)
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

    print("=" * 60)
    print("  Done! Video saved to:", output_path)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
