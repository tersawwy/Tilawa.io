"""
Module: Scene Manager
Groups timed_words.json into scenes for word-pop mode.

A scene is a contiguous time segment backed by one B-roll clip.
Scene boundaries are cut on natural breath/phrase pauses (gap > 0.4s),
with a minimum scene duration of 2.5s (short phrases merge into previous).

Output: tmp/scenes.json
[
  {
    "clip_path": "/abs/path/clip_0.mp4",
    "scene_start": 0.0,
    "scene_end": 5.2,
    "word_indices": [0, 1, 2, 3]   ← indices into timed_words list
  },
  ...
]
"""

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Tuple


@dataclass
class Scene:
    clip_path:   str
    scene_start: float
    scene_end:   float
    word_indices: List[int] = field(default_factory=list)

    def duration(self) -> float:
        return self.scene_end - self.scene_start


# ─────────────────────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────────────────────

def build_scenes(
    timed_words: List[dict],
    clip_paths: List[str],
    output_path: str = "tmp/scenes.json",
    gap_threshold: float = 0.4,
    min_scene_duration: float = 2.5,
) -> List[Scene]:
    """
    Build a list of Scene objects from word timestamps and assign B-roll clips.

    Args:
        timed_words:        List of word dicts with 'display_start'/'display_end' fields.
        clip_paths:         Ordered list of local B-roll clip file paths.
        output_path:        Where to write scenes.json.
        gap_threshold:      Silence gap (seconds) that triggers a scene cut.
        min_scene_duration: Scenes shorter than this are merged into the previous scene.

    Returns:
        List of Scene objects (also written to output_path).
    """
    if not timed_words:
        raise ValueError("timed_words is empty — cannot build scenes.")
    if not clip_paths:
        raise ValueError("clip_paths is empty — at least one B-roll clip required.")

    boundaries = _detect_phrase_boundaries(timed_words, gap_threshold, min_scene_duration)
    scenes = _assign_clips(timed_words, boundaries, clip_paths)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in scenes], f, ensure_ascii=False, indent=2)

    print(f"    Scene manager: {len(scenes)} scene(s) → {output_path}")
    for i, s in enumerate(scenes):
        print(f"      Scene {i+1}: {s.scene_start:.2f}s – {s.scene_end:.2f}s "
              f"({s.duration():.2f}s, {len(s.word_indices)} words, "
              f"{os.path.basename(s.clip_path)})")

    return scenes


def load_scenes(scenes_path: str) -> List[Scene]:
    """Load scenes.json back into Scene objects."""
    with open(scenes_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Scene(**d) for d in data]


# ─────────────────────────────────────────────────────────────
# Phrase Boundary Detection
# ─────────────────────────────────────────────────────────────

def _detect_phrase_boundaries(
    timed_words: List[dict],
    gap_threshold: float,
    min_scene_duration: float,
) -> List[Tuple[float, float]]:
    """
    Detect natural phrase boundaries in the word timeline.

    Returns a list of (scene_start, scene_end) tuples representing scene time ranges.
    Each tuple covers a contiguous group of words.

    Algorithm:
    1. Cut at the midpoint of any inter-word gap > gap_threshold seconds.
    2. Merge any resulting scene shorter than min_scene_duration into the previous.
    """
    # Collect potential cut points (midpoints of significant gaps)
    cut_times = []
    for i in range(len(timed_words) - 1):
        curr_end  = timed_words[i].get("display_end", timed_words[i]["end"])
        next_start = timed_words[i + 1].get("display_start", timed_words[i + 1]["start"])
        gap = next_start - curr_end
        if gap > gap_threshold:
            cut_times.append(curr_end + gap / 2)

    # Build raw time ranges
    total_start = timed_words[0].get("display_start", timed_words[0]["start"])
    total_end   = timed_words[-1].get("display_end", timed_words[-1]["end"])

    raw_boundaries = [total_start] + cut_times + [total_end]
    raw_ranges: List[Tuple[float, float]] = [
        (raw_boundaries[i], raw_boundaries[i + 1])
        for i in range(len(raw_boundaries) - 1)
    ]

    # Merge scenes shorter than min_scene_duration into the previous
    merged: List[Tuple[float, float]] = []
    for start, end in raw_ranges:
        duration = end - start
        if merged and duration < min_scene_duration:
            # Extend previous scene's end
            prev_start, _ = merged[-1]
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))

    return merged


# ─────────────────────────────────────────────────────────────
# Clip Assignment
# ─────────────────────────────────────────────────────────────

def _assign_clips(
    timed_words: List[dict],
    boundaries: List[Tuple[float, float]],
    clip_paths: List[str],
) -> List[Scene]:
    """
    Assign one B-roll clip per scene and map word indices.

    Clips are assigned round-robin.  No two consecutive scenes share the same
    clip when more than one clip is available (improves visual variety).
    """
    n_clips = len(clip_paths)
    scenes: List[Scene] = []
    last_clip_idx = -1

    for scene_idx, (start, end) in enumerate(boundaries):
        # Pick a clip, avoiding repeating the immediately previous one
        clip_idx = scene_idx % n_clips
        if clip_idx == last_clip_idx and n_clips > 1:
            clip_idx = (clip_idx + 1) % n_clips
        last_clip_idx = clip_idx

        # Map word indices that fall within this scene's time range
        word_indices = []
        for wi, w in enumerate(timed_words):
            w_start = w.get("display_start", w["start"])
            if start <= w_start < end:
                word_indices.append(wi)
            elif w_start >= end:
                break

        scenes.append(Scene(
            clip_path    = clip_paths[clip_idx],
            scene_start  = round(start, 3),
            scene_end    = round(end,   3),
            word_indices = word_indices,
        ))

    # Sanity: every word must belong to exactly one scene
    all_mapped = sorted(wi for s in scenes for wi in s.word_indices)
    expected   = list(range(len(timed_words)))
    if all_mapped != expected:
        missing = set(expected) - set(all_mapped)
        extra   = set(all_mapped) - set(expected)
        if missing:
            print(f"    Warning: {len(missing)} word(s) not assigned to any scene: {sorted(missing)[:5]}")
        if extra:
            print(f"    Warning: {len(extra)} duplicate word assignment(s)")

    return scenes
