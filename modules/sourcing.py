"""
Module 1 — Sourcing
Downloads the trimmed audio (and, for --background video, the matching video
section) from YouTube, and fetches cinematic B-roll clips (Pexels preferred,
Pixabay-with-quality-filters as fallback, generated gradient last resort).
"""

import os
import random
import subprocess
from pathlib import Path

import requests


# ─────────────────────────────────────────────────────────────
# YouTube Audio Download
# ─────────────────────────────────────────────────────────────

def download_audio(
    url: str,
    start_time: str,
    duration: int,
    output_path: str,
) -> str:
    """
    Download a trimmed audio segment from a YouTube video using yt-dlp.

    Args:
        url:         YouTube video URL.
        start_time:  Start offset as "HH:MM:SS" or "SS".
        duration:    Number of seconds to extract.
        output_path: Where to save the resulting mp3.

    Returns:
        Absolute path to the saved mp3 file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Convert HH:MM:SS → total seconds for yt-dlp's --download-sections
    start_secs = _hms_to_seconds(start_time)
    end_secs = start_secs + duration
    section = f"*{start_secs}-{end_secs}"

    # yt-dlp extracts best audio, then ffmpeg trims it to the section
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",          # best quality
        "--download-sections", section,
        "--force-keyframes-at-cuts",
        "-o", output_path,
        url,
    ]

    print(f"    Running yt-dlp (section {start_secs}s–{end_secs}s)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Section downloads stream straight from googlevideo through ffmpeg,
        # which YouTube intermittently 403s. Fall back to fetching the whole
        # audio track with yt-dlp's own downloader and trimming locally.
        print("    Section download failed — downloading full audio and trimming...")
        _download_full_and_trim(url, start_secs, duration, output_path, result.stderr)

    if not os.path.exists(output_path):
        raise FileNotFoundError(
            f"yt-dlp did not produce expected output at: {output_path}"
        )

    return os.path.abspath(output_path)


def _download_full_and_trim(url: str, start_secs: int, duration: float,
                            output_path: str, first_error: str):
    full_tmpl = os.path.join(os.path.dirname(output_path) or ".", "_full_audio.%(ext)s")
    result = subprocess.run(
        ["yt-dlp", "--no-playlist", "-f", "bestaudio", "-o", full_tmpl, "--print", "after_move:filepath", url],
        capture_output=True, text=True,
    )
    full_path = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not os.path.exists(full_path):
        raise RuntimeError(f"yt-dlp failed:\n{first_error}\n--- full download ---\n{result.stderr}")
    try:
        trim = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(start_secs), "-t", str(duration),
             "-i", full_path, "-vn", "-c:a", "libmp3lame", "-q:a", "0", output_path],
            capture_output=True, text=True,
        )
        if trim.returncode != 0:
            raise RuntimeError(f"ffmpeg trim failed:\n{trim.stderr}")
    finally:
        os.remove(full_path)


def download_video(url: str, start_time: str, duration: float, output_path: str) -> str:
    """Download the matching section of the YouTube video itself (video only,
    ≤720p — it gets blurred, so more resolution is wasted) for the
    ``--background video`` mode."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    start_secs = _hms_to_seconds(start_time)
    fmt = "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]/b"
    cmd = [
        "yt-dlp", "--no-playlist", "-f", fmt,
        "--download-sections", f"*{start_secs}-{start_secs + duration}",
        "--force-keyframes-at-cuts", "--remux-video", "mp4",
        "-o", output_path, url,
    ]
    print(f"    Running yt-dlp for video (section {start_secs}s–{start_secs + duration}s)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(output_path):
        print("    Section download failed — downloading full video and trimming...")
        full_tmpl = os.path.join(os.path.dirname(output_path) or ".", "_full_video.%(ext)s")
        full = subprocess.run(
            ["yt-dlp", "--no-playlist", "-f", fmt, "-o", full_tmpl,
             "--print", "after_move:filepath", url],
            capture_output=True, text=True,
        )
        full_path = full.stdout.strip().splitlines()[-1] if full.stdout.strip() else ""
        if full.returncode != 0 or not os.path.exists(full_path):
            raise RuntimeError(f"yt-dlp video download failed:\n{result.stderr}\n{full.stderr}")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", str(start_secs), "-t", str(duration),
                 "-i", full_path, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 output_path],
                check=True,
            )
        finally:
            os.remove(full_path)
    return os.path.abspath(output_path)


# ─────────────────────────────────────────────────────────────
# Multi-Clip Cinematic Background Fetcher
# ─────────────────────────────────────────────────────────────

def fetch_cinematic_clips(config: dict, n_clips: int = 8) -> list:
    """
    Fetch up to n_clips cinematic background videos and cache them to tmp/broll/.

    On subsequent runs, already-downloaded clips are reused and no API calls are
    made as long as the cache already contains enough clips.  Delete tmp/broll/
    to force a fresh download.

    Priority (only used when more clips are needed):
      1. Pexels API  (if sourcing.pexels_api_key is set)
      2. Pixabay API (if sourcing.pixabay_api_key is set), with quality filters
      3. Solid black fallback (always works, no API key required)

    Returns a list of absolute local file paths.
    """
    sc = config.get("sourcing", {})
    queries    = sc.get("background_queries", [config.get("background", {}).get("search_query", "nature")])
    min_width  = sc.get("min_clip_width", 1920)
    cache_dir  = Path("tmp/broll")
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Collect any clips already on disk (from previous runs) ──
    cached = sorted(
        str(p.resolve())
        for p in cache_dir.glob("*.mp4")
        if p.stat().st_size > 0
    )

    if len(cached) >= n_clips:
        print(f"    B-roll cached — reusing {len(cached)} clip(s) from {cache_dir}")
        return cached[:n_clips]

    # Need more clips — hit the API for the remainder
    clips: list = list(cached)   # start from what we have
    need = n_clips - len(clips)

    pexels_key  = os.environ.get("PEXELS_API_KEY") or sc.get("pexels_api_key", "")
    pixabay_key = os.environ.get("PIXABAY_API_KEY") or sc.get("pixabay_api_key", "") or config.get("background", {}).get("pixabay_api_key", "")

    if pexels_key:
        print(f"    Fetching {need} clip(s) from Pexels...")
        clips += _pexels_fetch(pexels_key, queries, need, min_width, cache_dir)

    need = n_clips - len(clips)
    if need > 0 and pixabay_key and pixabay_key not in ("YOUR_FREE_PIXABAY_KEY", ""):
        print(f"    Fetching {need} clip(s) from Pixabay (quality-filtered)...")
        clips += _pixabay_fetch_quality(pixabay_key, queries, need, min_width, cache_dir)

    need = n_clips - len(clips)
    if need > 0:
        print(f"    Generating {need} solid-black fallback clip(s)...")
        clips += _generate_fallback_clips(need, cache_dir)

    print(f"    B-roll ready: {len(clips)} clip(s) in {cache_dir}")
    return clips


def _pexels_fetch(api_key: str, queries: list, n: int, min_width: int, cache_dir: Path) -> list:
    """Search Pexels and download up to n unique clips."""
    PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    collected = []
    seen_ids = set()

    random.shuffle(queries)
    for query in queries:
        if len(collected) >= n:
            break
        try:
            resp = requests.get(
                PEXELS_VIDEO_API,
                headers=headers,
                params={"query": query, "per_page": 15, "orientation": "portrait"},
                timeout=15,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])
            # Shuffle for variety across runs
            random.shuffle(videos)
            for v in videos:
                if len(collected) >= n:
                    break
                vid_id = v["id"]
                if vid_id in seen_ids:
                    continue
                # Find a file meeting minimum width
                file_url = None
                for vf in sorted(v.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True):
                    if vf.get("width", 0) >= min_width and vf.get("file_type", "").startswith("video"):
                        file_url = vf["link"]
                        break
                if not file_url:
                    continue
                out_path = cache_dir / f"pexels_{vid_id}.mp4"
                if not out_path.exists():
                    print(f"      Downloading Pexels clip {vid_id} ({query})...")
                    try:
                        _download_file(file_url, str(out_path))
                    except Exception as e:
                        print(f"      Pexels download failed for {vid_id}: {e}")
                        continue
                seen_ids.add(vid_id)
                collected.append(str(out_path.resolve()))
        except Exception as e:
            print(f"      Pexels query '{query}' failed: {e}")

    return collected


def _pixabay_fetch_quality(api_key: str, queries: list, n: int, min_width: int, cache_dir: Path) -> list:
    """Search Pixabay with quality filters and download up to n clips."""
    collected = []
    seen_ids = set()

    random.shuffle(queries)
    for query in queries:
        if len(collected) >= n:
            break
        try:
            params = {
                "key": api_key,
                "q": query,
                "video_type": "film",
                "per_page": 20,
                "safesearch": "true",
                "order": "popular",
                "editors_choice": "true",
                "min_width": min_width,
            }
            resp = requests.get(PIXABAY_API_URL, params=params, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])

            # Relax editors_choice if nothing found
            if not hits:
                params.pop("editors_choice")
                resp = requests.get(PIXABAY_API_URL, params=params, timeout=15)
                resp.raise_for_status()
                hits = resp.json().get("hits", [])

            random.shuffle(hits)
            for h in hits:
                if len(collected) >= n:
                    break
                vid_id = h["id"]
                if vid_id in seen_ids:
                    continue
                videos = h.get("videos", {})
                file_url = None
                for quality in ("large", "medium", "small"):
                    vdata = videos.get(quality, {})
                    if vdata.get("url") and vdata.get("width", 0) >= min_width:
                        file_url = vdata["url"]
                        break
                if not file_url:
                    continue
                out_path = cache_dir / f"pixabay_{vid_id}.mp4"
                if not out_path.exists():
                    print(f"      Downloading Pixabay clip {vid_id} ({query})...")
                    try:
                        _download_file(file_url, str(out_path))
                    except Exception as e:
                        print(f"      Pixabay download failed for {vid_id}: {e}")
                        continue
                seen_ids.add(vid_id)
                collected.append(str(out_path.resolve()))
        except Exception as e:
            print(f"      Pixabay query '{query}' failed: {e}")

    return collected


_FALLBACK_PALETTES = [
    ("0x0d1b2a", "0x274a5c"),   # deep teal
    ("0x1a1030", "0x3a2a5c"),   # indigo dusk
    ("0x1c1408", "0x4a3418"),   # amber dusk
    ("0x0a1a12", "0x2a4a34"),   # forest night
]


def _generate_fallback_clips(n: int, cache_dir: Path) -> list:
    """
    Generate n slowly-drifting gradient clips via ffmpeg — used only when no
    Pexels/Pixabay key is configured. A designed gradient reads as intentional;
    flat black reads as broken/missing, so this is not just a last resort.
    """
    clips = []
    for idx in range(n):
        out_path = cache_dir / f"fallback_{idx}.mp4"
        if not out_path.exists():
            c1, c2 = _FALLBACK_PALETTES[idx % len(_FALLBACK_PALETTES)]
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i",
                f"gradients=s=1080x1920:c0={c1}:c1={c2}:x0=540:y0=200:x1=540:y1=1920:rate=30",
                "-t", "30",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"      ffmpeg fallback clip {idx} failed: {result.stderr[:200]}")
                continue
        clips.append(str(out_path.resolve()))
    return clips


def _hms_to_seconds(hms: str) -> int:
    """Convert HH:MM:SS or MM:SS or plain seconds string to int seconds."""
    parts = hms.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    else:
        return int(parts[0])


# ─────────────────────────────────────────────────────────────
# Pixabay / download helpers
# ─────────────────────────────────────────────────────────────

PIXABAY_API_URL = "https://pixabay.com/api/videos/"


def _download_file(url: str, output_path: str):
    """Stream-download a file to disk."""
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
