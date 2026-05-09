"""
Module 4 — TikTok Uploader
Authenticates via exported browser cookies and uploads the final video
using Playwright (headless Chromium).

WARNING: Cookie-based automation is against TikTok's Terms of Service.
Use a dedicated account. Always test with privacy="private" first.
"""

import os
import time
import random

from playwright.sync_api import sync_playwright, Page, BrowserContext


# ─────────────────────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────────────────────

def upload_to_tiktok(
    video_path: str,
    cookies_file: str,
    caption: str,
    privacy: str = "private",
) -> str:
    """
    Upload a video to TikTok using session cookies.

    Args:
        video_path:   Absolute path to the mp4 file.
        cookies_file: Path to Netscape-format cookies.txt from your browser.
        caption:      Caption text including hashtags.
        privacy:      "public" or "private".

    Returns:
        URL of the posted video (or empty string if undetectable).
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if not os.path.exists(cookies_file):
        raise FileNotFoundError(
            f"Cookies file not found: {cookies_file}\n"
            "Export your TikTok cookies using the 'Get cookies.txt LOCALLY' browser extension."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        # Load cookies from file
        _load_cookies(context, cookies_file)

        page = context.new_page()

        try:
            post_url = _do_upload(page, video_path, caption, privacy)
        finally:
            browser.close()

    return post_url


# ─────────────────────────────────────────────────────────────
# Cookie Loading
# ─────────────────────────────────────────────────────────────

def _load_cookies(context: BrowserContext, cookies_file: str):
    """Parse a Netscape cookies.txt file and inject into Playwright context."""
    cookies = []
    with open(cookies_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expires, name, value = parts[:7]
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain.lstrip("."),
                "path": path,
                "secure": secure.upper() == "TRUE",
                "httpOnly": False,
            })

    if not cookies:
        raise ValueError(f"No valid cookies found in: {cookies_file}")

    context.add_cookies(cookies)
    print(f"    Loaded {len(cookies)} cookies.")


# ─────────────────────────────────────────────────────────────
# Upload Flow
# ─────────────────────────────────────────────────────────────

UPLOAD_URL = "https://www.tiktok.com/creator-center/upload"


def _do_upload(page: Page, video_path: str, caption: str, privacy: str) -> str:
    """Navigate TikTok's upload interface and post the video."""
    print("    Navigating to TikTok upload page...")
    page.goto(UPLOAD_URL, wait_until="networkidle", timeout=30000)
    _human_delay(1.5, 2.5)

    # Check login status
    if "login" in page.url.lower():
        raise RuntimeError(
            "TikTok redirected to login page — cookies may be expired.\n"
            "Re-export your cookies from a logged-in browser session."
        )

    # Find and interact with the upload iframe
    print("    Locating upload area...")
    frame = _get_upload_frame(page)

    # Upload the video file
    print(f"    Uploading video: {os.path.basename(video_path)}")
    file_input = frame.locator("input[type='file']")
    file_input.set_input_files(video_path)

    # Wait for upload to process (progress bar to disappear)
    print("    Waiting for video processing...")
    _wait_for_upload(frame)

    # Fill in caption
    print("    Setting caption...")
    _set_caption(frame, caption)
    _human_delay(0.5, 1.0)

    # Set privacy
    print(f"    Setting privacy to: {privacy}")
    _set_privacy(frame, privacy)
    _human_delay(0.5, 1.0)

    # Post the video
    print("    Submitting post...")
    _click_post(frame, page)

    # Wait for confirmation
    post_url = _wait_for_confirmation(page)
    return post_url


def _get_upload_frame(page: Page):
    """TikTok upload UI runs inside an iframe — locate it."""
    # Give the page time to load the iframe
    _human_delay(2.0, 3.0)
    frames = page.frames
    for frame in frames:
        if "upload" in frame.url or "creator" in frame.url:
            return frame
    # If no dedicated frame found, operate on main page
    return page.main_frame


def _wait_for_upload(frame, timeout_ms: int = 120_000):
    """Wait for the video upload progress to complete."""
    try:
        # Wait for processing indicator to appear then disappear
        frame.wait_for_selector(
            "[class*='upload-progress'], [class*='uploading']",
            state="visible",
            timeout=15000,
        )
        frame.wait_for_selector(
            "[class*='upload-progress'], [class*='uploading']",
            state="hidden",
            timeout=timeout_ms,
        )
    except Exception:
        # Progress element may not exist — just wait a fixed time
        _human_delay(8.0, 12.0)


def _set_caption(frame, caption: str):
    """Type the caption into TikTok's caption input."""
    # Try multiple selector strategies
    selectors = [
        "[data-e2e='caption-input']",
        "[class*='caption'] [contenteditable]",
        "[contenteditable='true']",
        "div[class*='DraftEditor']",
    ]
    for sel in selectors:
        try:
            el = frame.locator(sel).first
            el.click()
            _human_delay(0.3, 0.6)
            # Clear existing content and type caption
            el.fill("")
            for char in caption:
                el.type(char, delay=random.randint(20, 60))
            return
        except Exception:
            continue
    print("    Warning: Could not locate caption input field.")


def _set_privacy(frame, privacy: str):
    """Set the video privacy setting."""
    # TikTok's privacy dropdown varies by version; try common selectors
    try:
        privacy_button = frame.locator("[class*='permission'], [data-e2e*='permission']").first
        privacy_button.click()
        _human_delay(0.4, 0.8)

        if privacy == "private":
            option = frame.locator("text=Only you").first
        elif privacy == "friends":
            option = frame.locator("text=Friends").first
        else:
            option = frame.locator("text=Everyone, text=Public").first

        option.click()
        _human_delay(0.3, 0.5)
    except Exception:
        print(f"    Warning: Could not set privacy to '{privacy}' — leaving as default.")


def _click_post(frame, page: Page):
    """Click the Post button."""
    selectors = [
        "[data-e2e='post-button']",
        "button:has-text('Post')",
        "[class*='btn-post']",
        "button[type='submit']",
    ]
    for sel in selectors:
        try:
            btn = frame.locator(sel).first
            btn.click()
            return
        except Exception:
            continue

    # Last resort — look on the main page
    try:
        page.get_by_role("button", name="Post").click()
    except Exception:
        print("    Warning: Could not click Post button — manual intervention may be needed.")


def _wait_for_confirmation(page: Page, timeout_ms: int = 30_000) -> str:
    """Wait for TikTok to confirm the post and return the video URL."""
    try:
        page.wait_for_url("**/profile**", timeout=timeout_ms)
        return page.url
    except Exception:
        pass

    try:
        page.wait_for_selector(
            "text=Your video is being uploaded, text=Posted",
            timeout=timeout_ms,
        )
    except Exception:
        pass

    return page.url


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _human_delay(min_s: float = 0.5, max_s: float = 1.5):
    """Sleep for a randomized human-like delay to reduce bot detection."""
    time.sleep(random.uniform(min_s, max_s))
