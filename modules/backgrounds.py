"""
Background layer for the mushaf renderer — always produced as a finished,
full-length video (tmp/bg.mp4) so the final compose is a single ffmpeg pass.

Modes:
  video       the sheikh's own footage: cover-cropped to portrait, heavily
              blurred and dimmed (also hides any text burned into the source)
  ambient     Pexels nature footage when a key is configured (slow zoom,
              dissolves, soft grade), otherwise a generative scene drawn on a
              canvas in headless Chromium: drifting aurora light and rising
              particles that breathe gently with the reciter's voice
"""

import json
import os
import subprocess
from typing import List, Optional

import numpy as np


FPS_DEFAULT = 30

# Generative themes: base gradient (top, bottom) + light colours
THEMES = {
    "night":   {"base": ["#060a1c", "#0a2130"], "lights": ["#2bb3a6", "#4b55d8", "#e3b56c", "#2a7fd0"]},
    "dawn":    {"base": ["#170b22", "#35192a"], "lights": ["#f0a45b", "#c9577a", "#7a55c4", "#f2cf8c"]},
    "emerald": {"base": ["#03130f", "#0a2926"], "lights": ["#2fb68a", "#1d6fa3", "#d8c27a", "#3ec7b0"]},
}


def pick_theme(theme: str, surah: Optional[int]) -> str:
    """'auto' picks a theme from the surah number: varied across a series,
    but the same surah always looks the same."""
    if theme in THEMES:
        return theme
    names = sorted(THEMES)
    return names[(surah or 0) % len(names)]


# ─────────────────────────────────────────────────────────────
# Encoder choice
# ─────────────────────────────────────────────────────────────

_HW = None


def encoder_args(kind: str = "final") -> List[str]:
    """H.264 encoder flags. Apple VideoToolbox when available (≈5× faster
    than x264 at 1080×1920 on Apple Silicon), else x264 veryfast.
    'intermediate' (bg.mp4, re-encoded once more) gets extra bitrate."""
    global _HW
    if _HW is None:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        _HW = "h264_videotoolbox" in out
    if _HW:
        rate = "16M" if kind == "intermediate" else "8M"
        return ["-c:v", "h264_videotoolbox", "-b:v", rate, "-allow_sw", "1", "-pix_fmt", "yuv420p"]
    crf = "17" if kind == "intermediate" else "20"
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"]


# ─────────────────────────────────────────────────────────────
# Audio envelope (drives the generative "breathing")
# ─────────────────────────────────────────────────────────────

def audio_envelope(audio_path: str, fps: int, duration: float) -> List[float]:
    """Per-frame loudness in [0, 1], smoothed (fast-ish attack, slow release)
    so light swells with the voice and settles softly in pauses."""
    sr = 16000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio_path, "-t", str(duration),
         "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True,
    ).stdout
    y = np.frombuffer(raw, dtype=np.float32)
    n = int(duration * fps)
    hop = sr // fps
    rms = np.array([
        float(np.sqrt(np.mean(y[i * hop:(i + 1) * hop] ** 2))) if i * hop < len(y) else 0.0
        for i in range(n)
    ])
    ref = np.percentile(rms, 95) if rms.size and rms.max() > 0 else 1.0
    rms = np.clip(rms / (ref + 1e-9), 0, 1)
    env, v = [], 0.0
    for r in rms:
        v += (r - v) * (0.12 if r > v else 0.03)
        env.append(round(float(v), 4))
    return env


# ─────────────────────────────────────────────────────────────
# Generative scene (canvas in headless Chromium)
# ─────────────────────────────────────────────────────────────

_SCENE_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:#000;overflow:hidden}canvas{display:block}</style></head>
<body><canvas id="c" width="%(w)d" height="%(h)d"></canvas><script>
const T = %(theme)s, W = %(w)d, H = %(h)d, SEED = %(seed)d;
const c = document.getElementById('c'), g = c.getContext('2d');
let s = SEED;                                   // deterministic PRNG
const rnd = () => (s = (s * 1664525 + 1013904223) %% 4294967296) / 4294967296;
const hex = h => [1,3,5].map(i => parseInt(h.slice(i, i + 2), 16));
const rgba = (h, a) => { const [r, gg, b] = hex(h); return `rgba(${r},${gg},${b},${a})`; };

const blobs = T.lights.map((col, i) => ({
  col, px: rnd() * 6.28, py: rnd() * 6.28,
  wx: 0.020 + rnd() * 0.025, wy: 0.015 + rnd() * 0.02,
  r: 0.55 + rnd() * 0.25, a: i === 2 ? 0.22 : 0.42,
}));
// two depth layers: tiny far motes, a few large soft near bokeh
const motes = Array.from({length: 60}, () => ({
  x: rnd(), y: rnd(), sp: 0.003 + rnd() * 0.007, sz: 0.4 + rnd() * 1.0,
  ph: rnd() * 6.28, tw: 0.4 + rnd() * 1.2, sw: 3 + rnd() * 8, a: 0.55,
}));
const bokeh = Array.from({length: 9}, () => ({
  x: rnd(), y: rnd(), sp: 0.010 + rnd() * 0.012, sz: 5 + rnd() * 9,
  ph: rnd() * 6.28, tw: 0.2 + rnd() * 0.4, sw: 10 + rnd() * 20, a: 0.16,
}));

window.draw = (t, env) => {
  const glow = 0.82 + 0.18 * env;
  const base = g.createLinearGradient(0, 0, 0, H);
  base.addColorStop(0, T.base[0]); base.addColorStop(1, T.base[1]);
  g.globalCompositeOperation = 'source-over';
  g.fillStyle = base; g.fillRect(0, 0, W, H);

  g.globalCompositeOperation = 'screen';
  for (const b of blobs) {
    const x = W * (0.5 + 0.42 * Math.sin(t * b.wx + b.px));
    const y = H * (0.45 + 0.35 * Math.sin(t * b.wy + b.py));
    const r = W * b.r * (0.92 + 0.08 * Math.sin(t * 0.05 + b.px));
    const rg = g.createRadialGradient(x, y, 0, x, y, r);
    rg.addColorStop(0, rgba(b.col, b.a * glow));
    rg.addColorStop(0.5, rgba(b.col, b.a * glow * 0.35));
    rg.addColorStop(1, rgba(b.col, 0));
    g.fillStyle = rg; g.fillRect(0, 0, W, H);
  }

  // horizon glow: warm light rising from below gives the scene depth
  const hz = g.createRadialGradient(W * 0.5, H * 1.08, 0, W * 0.5, H * 1.08, H * 0.75);
  hz.addColorStop(0, rgba(T.lights[2], 0.20 * glow));
  hz.addColorStop(1, rgba(T.lights[2], 0));
  g.fillStyle = hz; g.fillRect(0, 0, W, H);

  // a soft beam of light from above, swaying very slowly
  const sway = Math.sin(t * 0.035) * W * 0.12;
  const beam = g.createLinearGradient(0, 0, 0, H * 0.9);
  beam.addColorStop(0, `rgba(255,240,215,${0.07 * glow})`);
  beam.addColorStop(1, 'rgba(255,240,215,0)');
  g.fillStyle = beam;
  g.beginPath();
  g.moveTo(W * 0.42 + sway * 0.3, 0); g.lineTo(W * 0.58 + sway * 0.3, 0);
  g.lineTo(W * 0.85 + sway, H * 0.9); g.lineTo(W * 0.15 + sway, H * 0.9);
  g.closePath();
  g.filter = `blur(${Math.round(W * 0.06)}px)`;  // soft edges: light, not a spotlight
  g.fill();
  g.filter = 'none';

  g.globalCompositeOperation = 'lighter';
  const drawMotes = (list, rise) => {
    for (const m of list) {
      const y = (((m.y - t * m.sp) %% 1) + 1) %% 1 * (H + 60) - 30;
      const x = m.x * W + m.sw * Math.sin(t * 0.2 + m.ph);
      const a = m.a * (0.45 + 0.55 * (0.5 + 0.5 * Math.sin(t * m.tw + m.ph))) * glow;
      const R = m.sz * rise;
      const rg = g.createRadialGradient(x, y, 0, x, y, R);
      rg.addColorStop(0, `rgba(255,242,215,${a})`);
      rg.addColorStop(0.4, `rgba(255,242,215,${a * 0.5})`);
      rg.addColorStop(1, 'rgba(255,242,215,0)');
      g.fillStyle = rg;
      g.fillRect(x - R, y - R, 2 * R, 2 * R);
    }
  };
  drawMotes(motes, 3.5);
  drawMotes(bokeh, 1.6);
};
</script></body></html>"""


def _render_generative_chunk(job: dict) -> str:
    """Worker: draw frames [f0, f1) of the scene and encode them to one file.
    Runs in its own process (Playwright's sync API is per-process)."""
    from playwright.sync_api import sync_playwright

    W, H, w, h, fps = job["W"], job["H"], job["w"], job["h"], job["fps"]
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
         "-vf", f"scale={W}:{H}:flags=bicubic,noise=alls=3:allf=t",
         *encoder_args("intermediate"), job["out"]],
        stdin=subprocess.PIPE,
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.set_content(job["html"])
        env = job["envelope"]
        for i in range(job["f0"], job["f1"]):
            e = env[i] if i < len(env) else 0.0
            page.evaluate(f"draw({i / fps:.4f}, {e})")
            ff.stdin.write(page.screenshot(type="jpeg", quality=92))
        browser.close()
    ff.stdin.close()
    if ff.wait() != 0:
        raise RuntimeError("ffmpeg failed while encoding the generative background")
    return job["out"]


def render_generative(
    out_path: str, duration: float, W: int, H: int, fps: int,
    theme: str, envelope: List[float], seed: int = 7, scale: int = 2, workers: int = 0,
) -> str:
    """Draw the scene at 1/scale resolution (it is soft by design, so this
    costs no quality), in parallel browser processes over frame ranges, then
    join the pieces losslessly. ffmpeg upscales and adds fine grain so dark
    gradients don't band after compression."""
    from concurrent.futures import ProcessPoolExecutor

    w, h = W // scale, H // scale
    html = _SCENE_HTML % {"w": w, "h": h, "theme": json.dumps(THEMES[theme]), "seed": seed}
    n = int(round(duration * fps))
    workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
    bounds = [round(k * n / workers) for k in range(workers + 1)]
    base = os.path.splitext(out_path)[0]
    jobs = [
        {"W": W, "H": H, "w": w, "h": h, "fps": fps, "html": html, "envelope": envelope,
         "f0": bounds[k], "f1": bounds[k + 1], "out": f"{base}_part{k}.mp4"}
        for k in range(workers) if bounds[k + 1] > bounds[k]
    ]
    with ProcessPoolExecutor(max_workers=len(jobs)) as ex:
        parts = list(ex.map(_render_generative_chunk, jobs))

    listing = f"{base}_parts.txt"
    with open(listing, "w") as f:
        f.writelines(f"file '{os.path.abspath(p)}'\n" for p in parts)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing,
             "-c", "copy", out_path],
            check=True,
        )
    finally:
        for p in parts + [listing]:
            if os.path.exists(p):
                os.remove(p)
    return os.path.abspath(out_path)


# ─────────────────────────────────────────────────────────────
# Sheikh's own video, softly blurred
# ─────────────────────────────────────────────────────────────

def render_video_bg(
    src: str, out_path: str, duration: float, W: int, H: int, fps: int,
    blur: float = 10.0, brightness: float = -0.14, saturation: float = 0.8, zoom: float = 0.05,
    sharp_until: float = 0.0, transition: float = 1.2,
) -> str:
    """Cover-crop the source to portrait, blur it heavily and dim it, with a
    very slow push-in. Blurring happens at 1/3 resolution (same look, much
    faster); the blur also hides any text already burned into the video.

    With ``sharp_until`` > 0 the footage first plays sharp (you see the
    sheikh clearly), then dissolves into the soft blurred version over
    ``transition`` s starting at ``sharp_until`` — i.e. as recitation begins."""
    lw, lh = W // 3, H // 3
    frames = max(int(duration * fps), 1)
    blurred = (
        f"scale={lw}:{lh}:force_original_aspect_ratio=increase,crop={lw}:{lh},"
        f"gblur=sigma={blur},eq=brightness={brightness}:saturation={saturation},"
        f"zoompan=z='1+{zoom}*on/{frames}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":s={W}x{H}:fps={fps},setsar=1,format=yuv420p"
    )
    t0 = min(max(sharp_until, 0.0), max(duration - transition, 0.0))
    norm = f"fps={fps},settb=1/{fps}"   # xfade needs identical rate + timebase
    if t0 < 0.3:
        graph = f"[0:v]fps={fps},{blurred},noise=alls=3:allf=t[v]"
    else:
        graph = (
            f"[0:v]fps={fps},split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
            f"format=yuv420p,trim=duration={t0 + transition:.3f},{norm}[sharp];"
            f"[b]{blurred},trim=start={t0:.3f},setpts=PTS-STARTPTS,{norm}[soft];"
            f"[sharp][soft]xfade=transition=fade:duration={transition}:offset={t0:.3f},"
            f"noise=alls=3:allf=t[v]"
        )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", src, "-t", f"{duration:.3f}",
         "-an", "-filter_complex", graph, "-map", "[v]", *encoder_args("intermediate"), out_path],
        check=True,
    )
    return os.path.abspath(out_path)


# ─────────────────────────────────────────────────────────────
# Nature footage (Pexels), slow dissolves
# ─────────────────────────────────────────────────────────────

def render_footage_bg(
    clips: List[str], out_path: str, duration: float, W: int, H: int, fps: int,
    seg: float = 9.0, xfade: float = 1.5, brightness: float = -0.06, saturation: float = 0.85,
) -> str:
    """Chain clips (looping the list as needed) with slow dissolves, each
    with a gentle drift and a soft warm grade (the vignette/scrim in the
    compose step keeps the text readable)."""
    step = seg - xfade
    n = max(1, int(np.ceil((duration - xfade) / step)))
    use = [clips[i % len(clips)] for i in range(n)]
    frames = int(seg * fps)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for c in use:
        cmd += ["-stream_loop", "-1", "-t", f"{seg:.3f}", "-i", c]
    f = []
    for i in range(n):
        f.append(
            # cover-scale 6% oversize, then a slow vertical drift across the
            # spare pixels (cheap crop pan; full-res zoompan is ~3× slower)
            f"[{i}:v]fps={fps},scale={int(W * 1.06) // 2 * 2}:{int(H * 1.06) // 2 * 2}"
            f":force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:x='(iw-ow)/2':y='(ih-oh)*n/{frames}',"
            # soft warm grade in one cheap pass (colorbalance/gblur doubled the cost)
            f"eq=brightness={brightness}:saturation={saturation}:gamma_r=1.04:gamma_b=0.96,"
            f"setsar=1,format=yuv420p[c{i}]"
        )
    last = "c0"
    for i in range(1, n):
        f.append(f"[{last}][c{i}]xfade=transition=fade:duration={xfade}:offset={i * step:.3f}[x{i}]")
        last = f"x{i}"
    f.append(f"[{last}]noise=alls=2:allf=t,trim=duration={duration:.3f}[v]")
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-an",
            *encoder_args("intermediate"), out_path]
    subprocess.run(cmd, check=True)
    return os.path.abspath(out_path)


# ─────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────

def build_background(
    mode: str, out_path: str, duration: float, W: int, H: int, fps: int,
    audio_path: str, config: dict, video_path: Optional[str] = None, surah: Optional[int] = None,
    recitation_start: Optional[float] = None,
) -> str:
    """Produce tmp/bg.mp4 for the chosen mode ('video' or 'ambient')."""
    bg_cfg = config.get("background", {}) or {}
    if mode == "video":
        if not video_path or not os.path.exists(video_path):
            raise FileNotFoundError("background=video needs the downloaded source video")
        sharp = bool(bg_cfg.get("video_sharp_intro", True)) and recitation_start is not None
        # finish the dissolve as the first ayah fades in (it leads its word by 0.35 s)
        sharp_until = max(0.0, recitation_start - 1.0) if sharp else 0.0
        print("    Background: sheikh's video" +
              (f", sharp until {sharp_until:.1f}s then softly blurred" if sharp_until >= 0.3
               else ", softly blurred"))
        return render_video_bg(
            video_path, out_path, duration, W, H, fps,
            blur=float(bg_cfg.get("blur", 10)), brightness=float(bg_cfg.get("video_brightness", -0.14)),
            sharp_until=sharp_until,
        )

    sc = config.get("sourcing", {}) or {}
    pexels_key = os.environ.get("PEXELS_API_KEY") or sc.get("pexels_api_key", "")
    if pexels_key:
        from modules.sourcing import fetch_cinematic_clips

        clips = [c for c in fetch_cinematic_clips(config, n_clips=sc.get("clips_per_video", 6))
                 if "fallback_" not in os.path.basename(c)]
        if clips:
            print(f"    Background: nature footage ({len(clips)} clip(s)), slow dissolves")
            return render_footage_bg(clips, out_path, duration, W, H, fps)
        print("    Pexels returned no clips — using the generative scene.")

    theme = pick_theme(config.get("render", {}).get("theme", "auto"), surah)
    print(f"    Background: generative scene (theme={theme})")
    return render_generative(
        out_path, duration, W, H, fps, theme, audio_envelope(audio_path, fps, duration),
        seed=(surah or 7),
    )
