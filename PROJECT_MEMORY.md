# Strategic Implementation Plan: Project "Cinematic Single-Word"

## 1. Gap Analysis: Current vs. Target State
To achieve the high-quality, minimalistic style of the reference video, we must transition from a "karaoke-style" sentence highlighter to a strict single-word render.

* **Current State:** Displays full Ayahs. Highlights the active word in gold `#F5C518`, grays out past words. Includes English translation.
* **Target State (The Reference Video):** * **Strictly single-word rendering:** Only one word is on screen at a time.
    * **Typography:** Large, bold, highly legible Arabic font (e.g., *KFGQPC Uthman Taha* or *Scheherazade New*).
    * **Styling:** Pure white text with a subtle, feathered drop shadow to ensure contrast against cinematic backgrounds. No English translation.
    * **Animation:** Words appear with a hard cut or a micro-fade, remaining completely static in the dead-center of the screen for their exact duration, then instantly swapping to the next word.
    * **Backgrounds:** Cinematic, moody nature footage (overcast skies, dark oceans, rich greens) rather than bright/generic stock footage.

---

## 2. Module Refactoring Strategy

### A. The Sync Module (`modules/sync.py`)
Single-word rendering is completely unforgiving of bad timestamps. A 100ms delay in a single-word render looks like a visual glitch.

* **Actionable Update:** Ensure `stable-ts` output is precisely mapped.
* **Tajweed Handling:** Words with elongations (Madd) need their duration extended to match the reciter. Prevent `stable-ts` from creating "dead space" between words if the reciter is holding a note. 
* **Data Structure:** The `timed_words.json` must be strictly structured:
    ```json
    [
      {"word": "رَبِّ", "start": 0.00, "end": 0.85},
      {"word": "ٱجۡعَلۡنِي", "start": 0.85, "end": 1.90}
    ]
    ```
    *Rule:* `word[n].end` should perfectly touch `word[n+1].start` unless there is a physical breath/pause. Implement a smoothing algorithm to close micro-gaps (< 0.1s) to prevent text flickering.

### B. The Renderer Module (`modules/renderer.py`) - *The Core Rewrite*
Abandon multi-line text generation and shift to an object-oriented **Clip Composition** architecture.

* **1. Text Reshaping:**
    Because we are rendering single words, line-wrapping is no longer an issue. Simply apply `arabic_reshaper` and `bidi.algorithm.get_display` to the single string.
* **2. Typography & Shadows:**
    Do not rely on standard MoviePy `TextClip` for advanced shadows (it causes pixelation). 
    * *Strategy:* Use Python's `Pillow` (PIL) library to generate a transparent PNG for *each word* dynamically.
    * Draw the white text.
    * Apply a Gaussian Blur to a black copy of the text placed directly behind it to create a high-quality, cinematic drop shadow.
    * Convert these PIL images into MoviePy `ImageClip`s.
* **3. The Timeline Assembly:**
    Generate an array of sequential `ImageClip`s instead of one continuous text overlay.

### C. The Sourcing Module (`modules/sourcing.py`)
The vibe of the reference video relies heavily on "Moody/Cinematic" footage.
* **Actionable Update:** Update `config.yaml` Pixabay queries. 
* *Old Queries:* "nature", "beautiful sky".
* *New Queries:* "dark ocean waves", "foggy forest", "rain landscape", "cinematic mountains".
* *Filter:* Ensure footage is at least 30fps (preferably 60fps) to match the smooth aesthetic.

---

## 3. Technical Execution Blueprint

### Phase 1: The Word Generator Function (`renderer.py`)
Create a function that takes a single word and returns a perfect MoviePy `ImageClip`.

```python
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from bidi.algorithm import get_display
import arabic_reshaper
import numpy as np
from moviepy.editor import ImageClip

def create_word_clip(word, duration, font_path, font_size=120):
    # 1. Reshape the Arabic text
    reshaped_text = arabic_reshaper.reshape(word)
    bidi_text = get_display(reshaped_text)
    
    # 2. Setup Pillow Image (Transparent Background)
    font = ImageFont.truetype(font_path, font_size)
    
    # Get bounding box to size the image dynamically
    left, top, right, bottom = font.getbbox(bidi_text)
    width = right - left + 100 # Add padding for shadow
    height = bottom - top + 100
    
    img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    # 3. Draw Shadow (Black, offset slightly, blurred)
    shadow_offset = (5, 5)
    draw.text((50 + shadow_offset[0], 50 + shadow_offset[1]), bidi_text, font=font, fill=(0, 0, 0, 180))
    img = img.filter(ImageFilter.GaussianBlur(radius=4)) # Smooth cinematic shadow
    
    # 4. Draw Main Text (White) over the shadow
    draw_main = ImageDraw.Draw(img)
    draw_main.text((50, 50), bidi_text, font=font, fill=(255, 255, 255, 255))
    
    # 5. Convert to MoviePy Clip
    img_array = np.array(img)
    clip = ImageClip(img_array).set_duration(duration)
    
    # 6. Apply Animation (Optional: Micro fade-in to match the smooth feel)
    clip = clip.crossfadein(0.1) 
    
    return clip.set_position('center')
```

### Phase 2: Timeline Assembly (`renderer.py`)
Map the `timed_words.json` to this function and concatenate them over the background.

```python
from moviepy.editor import VideoFileClip, CompositeVideoClip, AudioFileClip

def assemble_video(background_path, audio_path, timed_words):
    bg_clip = VideoFileClip(background_path)
    
    word_clips = []
    
    for item in timed_words:
        start = item['start']
        end = item['end']
        duration = end - start
        
        # Generate the visual clip
        clip = create_word_clip(item['word'], duration, "assets/Uthman.otf")
        clip = clip.set_start(start)
        
        word_clips.append(clip)
    
    # Overlay words onto background
    final_video = CompositeVideoClip([bg_clip] + word_clips)
    
    # Set Audio
    final_video = final_video.set_audio(AudioFileClip(audio_path))
    
    # Render (Ensure to close clips afterward for memory management)
    final_video.write_videofile("output/final.mp4", fps=30, codec="libx264", audio_codec="aac")
    
    bg_clip.close()
    final_video.close()
```

---

## 4. Optimization & QA Steps

1.  **Memory Management:** Rendering hundreds of individual `ImageClip` objects can bloat memory in MoviePy. Ensure you are closing clips (`clip.close()`) after the `write_videofile` process completes, especially for an automated pipeline.
2.  **Font Selection:** The reference video relies heavily on elegant typography. Test **KFGQPC Uthman Taha Naskh** or **Scheherazade New**.
3.  **Audio Mastering:** In `sourcing.py`, use `ffmpeg` to add a slight reverb and bass boost to the extracted YouTube `.mp3`. A simple ffmpeg filter like `aecho=0.8:0.9:1000:0.3` mimics the acoustics of a massive mosque, matching the cinematic visuals.
4.  **TikTok Uploader Adjustments:** Because the video text is strictly Arabic, ensure the TikTok captions in `uploader.py` automatically include the English translation so non-Arabic speakers understand the context.