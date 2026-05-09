# Goal Description

The objective is to build an automated workflow that fetches Quran recitation videos from YouTube, extracts 30-second clips, generates trending synchronized bilingual subtitles (Arabic script + English translation), formats the video for TikTok (9:16 portrait style), and automatically posts it to TikTok. 

We will adopt the **latest 2026 TikTok styles** for Quran recitations, which are defined by high-quality minimalist/lofi aesthetic backgrounds, clear Uthmani Arabic script with dynamic word-by-word highlighting, and clean sans-serif English translations.

## User Review Required

> [!IMPORTANT]
> Please review this overall strategy. Since generating perfectly synced Quranic Arabic and translations from random YouTube audio can be technically challenging due to Tajweed (recitation rules), we have two main paths available for transcription. See "Open Questions" for choices on how we source the text.

> [!WARNING]  
> TikTok has rate limits and anti-spam measures for bot uploads. The upload mechanism will require session cookies from an existing TikTok account to bypass some of these limitations.

## Proposed Changes / Architecture

We will create a Python-based pipeline consisting of 4 core modules. Optionally, this can be managed via continuous execution (cron jobs) or a simple script triggered manually.

### 1. Sourcing Module (YouTube & Backgrounds)
- **YouTube Fetching (`yt-dlp`):** A script that takes a YouTube URL, search term, or playlist, and downloads a high-quality slice of the video/audio (e.g., 30-60 seconds).
- **Background Replacement:** (Optional but recommended for the "Calm/Lofi" trend). Fetching ambient 9:16 nature clips from the Pexels API to replace the original YouTube background.

### 2. Synchronization & Text Module
- **Transcription (`faster-whisper`):** Extract word-level timestamps from the 30-second audio track.
- **Text Alignment (`arabic-reshaper` + `python-bidi`):** Arabic text needs special handling in Python to connect letters properly. 
- **Translation:** Use an API (like OpenAI) or the Quran.com API to map the transcribed text to its precise, perfect Uthmani script and English translation.

### 3. Video Processing Module (The "2026 Aesthetic")
- **`MoviePy` & `ImageMagick`:** Render the video programmatically.
- **The Format:**
  - **Ratio:** 9:16 Vertical Video.
  - **Top/Center Screen:** The original YouTube video cropped or masked (if it features a famous Qari), or beautifully rendered Arabic text.
  - **Dynamic Subtitles:** Applying a word-by-word color glow/highlight to the Uthmani text to match the recitation's rhythm.
  - **Bottom Center:** Subtle, clean English translation (e.g., in Montserrat or Roboto font) that transitions smoothly via fade-in.
  - **Overlay:** A subtle vignette and visual audio-wave for high retention.

### 4. TikTok Uploader Module
- **Automation (`tiktok-uploader` / Playwright):** A headless browser script that authenticates via your exported session cookies, injects the final `.mp4`, adds trending hashtags (e.g., `#quran #recitation #islamic #peace`), and posts the video automatically.

---

## Open Questions

> [!IMPORTANT]
> **1. How should we handle the Quranic text matching?**
> *Option A (AI-driven):* Use Open AI's Whisper to transcribe the audio, then translate it. (Pros: Works for ANY video. Cons: AI might make small spelling errors in Quranic Arabic, which is highly sensitive).
> *Option B (API-driven):* You manually provide the Surah and Ayah range (e.g. Surah 2, Verses 1-5). Our script downloads this specific text from Alquran.cloud and aligns it to the audio. (Pros: 100% text accuracy. Cons: Requires you to know which verse the YouTube video is playing).
> *Which do you prefer?*

> [!TIP]  
> **2. Visual Style:** 
> Do you want to keep the original YouTube video's visuals (blurred and cropped to 9:16), or do you want the script to automatically fetch "aesthetic" drone/nature backgrounds and just use the YouTube video for the audio?

> [!CAUTION]
> **3. TikTok Authentication:**
> To post to TikTok automatically, you will need to log into TikTok in a browser and export your cookies.txt file. Are you comfortable with this step?

## Verification Plan

### Automated Tests
- Build out the pipeline step-by-step (Download -> Subtitles -> Render -> Upload).
- Produce a sample 10-second offline clip to verify the Arabic rendering (no disconnected letters or left-to-right bugs) without uploading it.

### Manual Verification
- We will generate a complete 30-second mp4 file locally.
- Review the timing, font readability, and word-by-word animation.
- Upon your approval, we will do a test upload to a private TikTok session to ensure the uploader tool works without shadowbans.
