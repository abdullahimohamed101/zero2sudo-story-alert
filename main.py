import base64
import json
import os
import sqlite3
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


TARGET_USERNAME = os.getenv("TARGET_USERNAME", "zero2sudo").lstrip("@")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "itsabdulmohamed101@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
STORAGE_STATE_B64 = os.getenv("INSTAGRAM_STORAGE_STATE_B64", "")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "stories.db"

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "l.instagram.com",
}


def log(message: str):
    print(message, flush=True)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_links (
                url TEXT PRIMARY KEY,
                first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def already_seen(url: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM seen_links WHERE url = ? LIMIT 1",
                (url,),
            ).fetchone()
            is not None
        )


def mark_seen(url: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO seen_links(url) VALUES (?)", (url,))
        conn.commit()


def decode_storage_state():
    if not STORAGE_STATE_B64:
        raise RuntimeError(
            "INSTAGRAM_STORAGE_STATE_B64 is missing. "
            "Run export_session.py locally once and add its output to Railway."
        )

    try:
        raw = base64.b64decode(STORAGE_STATE_B64).decode("utf-8")
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError(
            "INSTAGRAM_STORAGE_STATE_B64 is invalid. Re-run export_session.py."
        ) from exc


def unwrap_instagram_redirect(url: str) -> str:
    """Extract the actual destination from common Instagram redirect links."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        if host == "l.instagram.com":
            qs = parse_qs(parsed.query)
            for key in ("u", "url"):
                if qs.get(key):
                    return unquote(qs[key][0])
    except Exception:
        pass

    return url


def normalize_external_url(url: str):
    if not url:
        return None

    url = unwrap_instagram_redirect(url)

    if not url.startswith(("http://", "https://")):
        return None

    host = (urlparse(url).hostname or "").lower()

    if not host:
        return None

    if host in INSTAGRAM_HOSTS or host.endswith(".instagram.com"):
        return None

    return url


def send_email(url: str):
    if not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_APP_PASSWORD is missing.")

    msg = EmailMessage()
    msg["Subject"] = f"🚨 New internship link from @{TARGET_USERNAME}"
    msg["From"] = ALERT_EMAIL
    msg["To"] = ALERT_EMAIL

    msg.set_content(
        f"""New story link detected from @{TARGET_USERNAME}

APPLY / OPEN:
{url}

Instagram:
https://www.instagram.com/{TARGET_USERNAME}/

This alert was sent automatically by your Railway story monitor.
"""
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(ALERT_EMAIL, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def send_session_expired_email():
    """Best-effort notification when Instagram requires login again."""
    if not GMAIL_APP_PASSWORD:
        return

    msg = EmailMessage()
    msg["Subject"] = f"⚠️ @{TARGET_USERNAME} monitor needs Instagram login"
    msg["From"] = ALERT_EMAIL
    msg["To"] = ALERT_EMAIL
    msg.set_content(
        """Your Instagram monitoring session is no longer authenticated.

Run export_session.py locally again, replace INSTAGRAM_STORAGE_STATE_B64
in Railway, and redeploy/restart the service.

The bot did not attempt to bypass Instagram's login challenge.
"""
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(ALERT_EMAIL, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except Exception:
        pass


def extract_external_links(page):
    links = set()

    # Normal anchors are the most reliable source when Instagram exposes the sticker as a link.
    for anchor in page.locator("a[href]").all():
        try:
            href = anchor.get_attribute("href")
            normalized = normalize_external_url(href)
            if normalized:
                links.add(normalized)
        except Exception:
            pass

    return links


def page_fingerprint(page) -> str:
    """Fingerprint the current Story slide without relying only on the URL."""
    parts = [page.url]

    for selector, attr in (
        ("img", "src"),
        ("video", "src"),
        ('a[href]', "href"),
    ):
        try:
            values = page.locator(selector).evaluate_all(
                f"els => els.map(e => e.getAttribute('{attr}')).filter(Boolean).slice(0, 20)"
            )
            parts.extend(values)
        except Exception:
            pass

    return "|".join(parts)


def click_next_story(page) -> bool:
    selectors = [
        'button[aria-label="Next"]',
        '[aria-label="Next"]',
        'button:has-text("Next")',
    ]

    before = page_fingerprint(page)

    for selector in selectors:
        try:
            locator = page.locator(selector).last
            if locator.count() and locator.is_visible():
                locator.click(timeout=2000)

                for _ in range(8):
                    page.wait_for_timeout(400)
                    if page_fingerprint(page) != before:
                        return True

                return False
        except Exception:
            pass

    return False


def looks_logged_out(page) -> bool:
    if "/accounts/login" in page.url:
        return True

    try:
        body = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        body = ""

    login_markers = (
        "log in to instagram",
        "log in",
        "sign up",
    )

    # Only use body markers if the URL is also account/login-ish to avoid false positives.
    return "/accounts/" in page.url and any(marker in body for marker in login_markers)


def scan_once():
    init_db()
    storage_state = decode_storage_state()

    story_url = f"https://www.instagram.com/stories/{TARGET_USERNAME}/"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        context = browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        try:
            log(f"[→] Checking @{TARGET_USERNAME}")
            page.goto(story_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3500)

            if looks_logged_out(page):
                log("[!] Instagram session is no longer authenticated.")
                send_session_expired_email()
                return 2

            try:
                body_text = page.locator("body").inner_text(timeout=3000).lower()
            except Exception:
                body_text = ""

            unavailable_markers = (
                "story is unavailable",
                "stories aren't available",
                "this story is unavailable",
            )

            if any(marker in body_text for marker in unavailable_markers):
                log("[i] No accessible active story right now.")
                return 0

            processed_fingerprints = set()
            new_links = 0

            # Safety cap prevents getting stuck if Instagram changes its controls.
            for _ in range(30):
                fingerprint = page_fingerprint(page)

                if fingerprint in processed_fingerprints:
                    break

                processed_fingerprints.add(fingerprint)
                page.wait_for_timeout(600)

                for url in extract_external_links(page):
                    if already_seen(url):
                        continue

                    log(f"[NEW] {url}")

                    # Only mark it seen after Gmail accepts the message.
                    send_email(url)
                    mark_seen(url)
                    new_links += 1
                    log(f"[✓] Email sent: {url}")

                if not click_next_story(page):
                    break

            log(f"[✓] Scan complete. New links emailed: {new_links}")
            return 0

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        sys.exit(scan_once())
    except Exception as exc:
        log(f"[FATAL] {type(exc).__name__}: {exc}")
        sys.exit(1)
