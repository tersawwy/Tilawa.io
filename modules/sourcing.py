"""
Module 1 — Sourcing
Downloads a trimmed audio clip from YouTube and fetches one or more cinematic
background videos (Pexels preferred, Pixabay-with-quality-filters as fallback,
solid-black last resort).
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
        raise RuntimeError(
            f"yt-dlp failed:\n{result.stderr}"
        )

    if not os.path.exists(output_path):
        raise FileNotFoundError(
            f"yt-dlp did not produce expected output at: {output_path}"
        )

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

    pexels_key  = sc.get("pexels_api_key", "")
    pixabay_key = sc.get("pixabay_api_key", "") or config.get("background", {}).get("pixabay_api_key", "")

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


def _generate_fallback_clips(n: int, cache_dir: Path) -> list:
    """Generate n solid dark-background clips via ffmpeg."""
    clips = []
    for idx in range(n):
        out_path = cache_dir / f"fallback_{idx}.mp4"
        if not out_path.exists():
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "color=c=0x0a0a1a:size=1080x1920:rate=30",
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
# Pixabay Background Video Download
# ─────────────────────────────────────────────────────────────

PIXABAY_API_URL = "https://pixabay.com/api/videos/"

# Fallback: if no key provided or API fails, use a minimal black video
_FALLBACK_COLOR = (0, 0, 0)


def fetch_background(
    api_key: str,
    query: str,
    output_path: str,
    min_duration: int = 30,
) -> str:
    """
    Fetch a nature background video from the Pixabay API.
    If the file already exists at output_path it is reused — no download occurs.
    Falls back to generating a solid black background if the API fails.

    Args:
        api_key:      Pixabay API key (free at pixabay.com).
        query:        Search query string (e.g. "calm nature aerial").
        output_path:  Where to save the downloaded mp4.
        min_duration: Minimum video length in seconds.

    Returns:
        Absolute path to the saved mp4 file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # ── Reuse cached file if it already exists ─────────────────
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        print(f"    Background cached — reusing {output_path}")
        return os.path.abspath(output_path)

    if not api_key or api_key == "YOUR_FREE_PIXABAY_KEY":
        print("    Pixabay API key not set — generating solid background fallback.")
        return _generate_fallback_background(output_path, min_duration)

    try:
        video_url = _pixabay_search(api_key, query, min_duration)
        print(f"    Downloading background from Pixabay...")
        _download_file(video_url, output_path)
        return os.path.abspath(output_path)
    except Exception as exc:
        print(f"    Pixabay fetch failed ({exc}) — using fallback background.")
        return _generate_fallback_background(output_path, min_duration)


def _pixabay_search(api_key: str, query: str, min_duration: int) -> str:
    """Search Pixabay and return a download URL for a suitable video."""
    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "per_page": 20,
        "safesearch": "true",
        "order": "popular",
    }
    resp = requests.get(PIXABAY_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hits = data.get("hits", [])
    if not hits:
        raise ValueError(f"No Pixabay results for query: '{query}'")

    # Filter by minimum duration and prefer portrait or tall videos
    candidates = [h for h in hits if h.get("duration", 0) >= min_duration]
    if not candidates:
        candidates = hits  # relax duration filter if nothing matches

    # Pick a random one from top results for variety
    hit = random.choice(candidates[:10])

    # Prefer medium quality (good balance of file size vs quality)
    videos = hit.get("videos", {})
    for quality in ("medium", "large", "small", "tiny"):
        if quality in videos and videos[quality].get("url"):
            return videos[quality]["url"]

    raise ValueError("No downloadable video URL found in Pixabay response.")


def _download_file(url: str, output_path: str):
    """Stream-download a file to disk."""
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)


def _generate_fallback_background(output_path: str, duration: int) -> str:
    """
    Generate a solid dark background video using ffmpeg.
    Used when Pixabay API key is missing or the request fails.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x0a0a1a:size=1080x1920:rate=30",
        "-t", str(duration),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fallback background failed:\n{result.stderr}")
    return os.path.abspath(output_path)
