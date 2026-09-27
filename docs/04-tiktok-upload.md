# 4. TikTok Upload

The last pipeline stage can post the finished video to TikTok automatically. It is **optional** — add `--no-upload` to skip it, and upload the file from `output/` by hand instead.

> ⚠️ **Read this first**
> - Automating TikTok with a browser **violates TikTok's Terms of Service**. Use a dedicated account you can afford to lose.
> - TikTok changes its web UI often; the automation can break without warning.
> - The default `tiktok.privacy` is `public`. **Set it to `private` while testing.**
> - Posting many videos quickly can trigger bot detection. Space posts out.

---

## Step 1 — Log in to TikTok in your browser

Use Chrome, Edge, Brave or Firefox, and log in to the account you want to post from at [tiktok.com](https://www.tiktok.com).

## Step 2 — Export your cookies (Netscape format)

1. Install a cookie-export extension that runs locally, for example **"Get cookies.txt LOCALLY"** (Chrome/Edge) or **"cookies.txt"** (Firefox).
2. With a `tiktok.com` tab open, click the extension and export cookies for the current site.
3. Save the file as:

```
cookies/tiktok_cookies.txt
```

(Create the `cookies/` folder if needed. The path is `tiktok.cookies_file` in `config.yaml`.)

The file should look like tab-separated lines:

```
# Netscape HTTP Cookie File
.tiktok.com	TRUE	/	TRUE	1790000000	sessionid	abc123...
```

> 🔒 These cookies **are your logged-in session**. Anyone with the file can post as you. `cookies/` is in `.gitignore` — never commit it or share it.

## Step 3 — Set caption, hashtags and privacy

In `config.yaml`:

```yaml
tiktok:
  cookies_file: "cookies/tiktok_cookies.txt"
  caption: "{surah_name_ar} | Surah {surah_name_en}"
  hashtags: "#quran #recitation #islam #peace #quranrecitation #fyp #foryoupage"
  privacy: "private"          # public | friends | private
```

`{surah_name_ar}` and `{surah_name_en}` are filled in automatically from the detected surah (e.g. `الملك | Surah Al-Mulk`). The hashtags go on the line below.

## Step 4 — Run without `--no-upload`

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --duration 45
```

The uploader opens TikTok's creator upload page in headless Chromium, attaches the video, types the caption with human-like delays, sets privacy, and clicks **Post**:

```
[4/4] Upload — posting to TikTok...
    Loaded 23 cookies.
    Navigating to TikTok upload page...
    Uploading video: final_video.mp4
    Waiting for video processing...
    Setting caption...
    Setting privacy to: private
    Submitting post...
    Posted     : https://www.tiktok.com/@you/...
```

## Step 5 — Check the post

Open your TikTok profile and confirm the video, caption and privacy. If something was skipped, the log shows a `Warning:` line (e.g. *Could not set privacy*).

---

## Keeping it working

| Problem | Fix |
|---|---|
| `TikTok redirected to login page — cookies may be expired` | Log in again in your browser and re-export the cookies. Sessions typically last ~30 days |
| `Cookies file not found` | Check the path in `tiktok.cookies_file` |
| `No valid cookies found` | The file isn't in Netscape format — re-export with a cookies.txt extension |
| `Warning: Could not locate caption input field` / `Could not click Post button` | TikTok changed its UI. Upload manually from `output/`, and update the selectors in [`modules/uploader.py`](../modules/uploader.py) |

See [Deep Dive: Uploader](deep-dive/uploader.md) for how the automation works.

---

**Next:** [Architecture →](05-architecture.md)
