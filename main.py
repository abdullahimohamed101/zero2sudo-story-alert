import base64
import html
import json
import os
import sqlite3
import smtplib
import sys
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

TARGET_USERNAME = os.getenv("TARGET_USERNAME", "zero2sudo").lstrip("@")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "itsabdulmohamed101@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_FROM = os.getenv(
    "EMAIL_FROM", "Story Alert <onboarding@resend.dev>"
).strip()
STORAGE_STATE_B64 = os.getenv("INSTAGRAM_STORAGE_STATE_B64", "")
SIMULATED_STORY_URL = os.getenv("SIMULATED_STORY_URL", "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "stories.db"

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "l.instagram.com",
}

STORY_LINK_KEYS = {
    "link_url",
    "story_link_url",
    "web_uri",
    "weburi",
}


def log(message: str):
    print(message, flush=True)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
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
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM seen_links WHERE url = ? LIMIT 1",
                (url,),
            ).fetchone()
            is not None
        )


def mark_seen(url: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
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

    # Instagram sometimes HTML-escapes URLs placed in DOM attributes.
    url = html.unescape(url).strip()
    url = unwrap_instagram_redirect(url)

    if not url.startswith(("http://", "https://")):
        return None

    host = (urlparse(url).hostname or "").lower()

    if not host:
        return None

    if host in INSTAGRAM_HOSTS or host.endswith(".instagram.com"):
        return None

    return url


def _username_from_story_node(node):
    """Return the owner username when a dict looks like an Instagram Story node."""
    if not isinstance(node, dict):
        return None

    for key in ("user", "owner"):
        account = node.get(key)
        if isinstance(account, dict) and account.get("username"):
            return str(account["username"]).lstrip("@").lower()

    return None


def _collect_link_fields(value, links):
    """Collect only fields Instagram uses for Story link stickers."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in STORY_LINK_KEYS and isinstance(child, str):
                normalized = normalize_external_url(child)
                if normalized:
                    links.add(normalized)
            _collect_link_fields(child, links)
    elif isinstance(value, list):
        for child in value:
            _collect_link_fields(child, links)


def extract_story_links_from_payload(payload, target_username=TARGET_USERNAME):
    """Extract link-sticker URLs from Story JSON belonging to the target account."""
    target = target_username.lstrip("@").lower()
    links = set()

    def visit(value):
        if isinstance(value, dict):
            if _username_from_story_node(value) == target:
                _collect_link_fields(value, links)
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return links


def deliver_email(subject: str, body: str):
    """Deliver by HTTPS on Railway, with Gmail SMTP as a local/Pro fallback."""
    if RESEND_API_KEY:
        payload = json.dumps(
            {
                "from": EMAIL_FROM,
                "to": [ALERT_EMAIL],
                "subject": subject,
                "text": body,
            }
        ).encode("utf-8")
        request = Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "zero2sudo-story-alert/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                if response.status not in (200, 201):
                    raise RuntimeError(
                        f"Resend returned unexpected HTTP {response.status}."
                    )
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Resend rejected the email (HTTP {exc.code}): {detail}"
            ) from exc
        return

    if not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "Email delivery is not configured. Set RESEND_API_KEY on Railway "
            "or GMAIL_APP_PASSWORD where outbound SMTP is available."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ALERT_EMAIL
    msg["To"] = ALERT_EMAIL
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(ALERT_EMAIL, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def send_email(url: str):
    deliver_email(
        f"🚨 New internship link from @{TARGET_USERNAME}",
        f"""New story link detected from @{TARGET_USERNAME}

APPLY / OPEN:
{url}

Instagram:
https://www.instagram.com/{TARGET_USERNAME}/

This alert was sent automatically by your Railway story monitor.
""",
    )


def email_unseen_links(links):
    """Email normalized links once, marking each one seen only after delivery."""
    sent = 0

    for url in links:
        if already_seen(url):
            continue

        log(f"[NEW] {url}")
        send_email(url)
        mark_seen(url)
        sent += 1
        log(f"[OK] Email sent: {url}")

    return sent


def send_session_expired_email():
    """Best-effort notification when Instagram requires login again."""
    if not RESEND_API_KEY and not GMAIL_APP_PASSWORD:
        return

    try:
        deliver_email(
            f"⚠️ @{TARGET_USERNAME} monitor needs Instagram login",
            """Your Instagram monitoring session is no longer authenticated.

Run export_session.py locally again, replace INSTAGRAM_STORAGE_STATE_B64
in Railway, and redeploy/restart the service.

The bot did not attempt to bypass Instagram's login challenge.
""",
        )
    except Exception:
        pass


def extract_external_links(page, captured_story_links=None):
    links = set(captured_story_links or ())

    # Link stickers are not always anchors. Instagram also uses link-like elements
    # with data attributes, depending on the frontend version being served.
    selector = "[href], [data-href], [data-url], [data-link], [data-lynx-uri]"
    for element in page.locator(selector).all():
        try:
            for attribute in ("href", "data-href", "data-url", "data-link", "data-lynx-uri"):
                normalized = normalize_external_url(element.get_attribute(attribute))
                if normalized:
                    links.add(normalized)
        except Exception:
            pass

    # Initial Story data can be embedded in JSON scripts rather than rendered as
    # an href. Restrict extraction to JSON owned by the requested account.
    try:
        scripts = page.locator('script[type="application/json"]').all_text_contents()
        for raw in scripts:
            try:
                links.update(extract_story_links_from_payload(json.loads(raw)))
            except (TypeError, ValueError):
                pass
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
        'div[role="button"]:has(svg[aria-label="Next"])',
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
        except Exception:
            pass

    # The Story viewer supports keyboard navigation even when its button markup
    # changes. This also gives the page one final chance to advance.
    try:
        page.keyboard.press("ArrowRight")
        for _ in range(8):
            page.wait_for_timeout(400)
            if page_fingerprint(page) != before:
                return True
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
    from playwright.sync_api import sync_playwright

    init_db()
    simulated_links = set()
    simulated_emails = 0

    if SIMULATED_STORY_URL:
        simulated_url = normalize_external_url(SIMULATED_STORY_URL)
        if not simulated_url:
            raise RuntimeError(
                "SIMULATED_STORY_URL must be a valid external http(s) URL."
            )

        log(f"[TEST] Simulating Story link: {simulated_url}")
        simulated_payload = {
            "user": {"username": TARGET_USERNAME},
            "story_link_stickers": [
                {"story_link": {"link_url": simulated_url}}
            ],
        }
        simulated_links = extract_story_links_from_payload(simulated_payload)
        simulated_emails = email_unseen_links(simulated_links)
        log(f"[TEST] Simulated Story emails sent: {simulated_emails}")

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
        captured_story_links = set()

        def capture_story_response(response):
            """Capture link stickers from Instagram's Story JSON responses."""
            try:
                parsed = urlparse(response.url)
                if not (parsed.hostname or "").lower().endswith("instagram.com"):
                    return

                content_type = response.headers.get("content-type", "").lower()
                if "json" not in content_type:
                    return

                captured_story_links.update(
                    extract_story_links_from_payload(response.json())
                )
            except Exception:
                # DOM extraction remains available if a response is unavailable
                # or Instagram changes the payload format.
                pass

        page.on("response", capture_story_response)

        try:
            log(f"[->] Checking @{TARGET_USERNAME}")
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
            observed_links = set()
            new_links = simulated_emails

            # Safety cap prevents getting stuck if Instagram changes its controls.
            for _ in range(30):
                fingerprint = page_fingerprint(page)

                if fingerprint in processed_fingerprints:
                    break

                processed_fingerprints.add(fingerprint)
                page.wait_for_timeout(600)

                links = extract_external_links(page, captured_story_links)
                observed_links.update(links)

                new_links += email_unseen_links(links)

                if not click_next_story(page):
                    break

            if not observed_links:
                log(
                    "[i] Story slides were found, but no external link stickers "
                    "were exposed by Instagram."
                )
            else:
                log(
                    f"[i] Story slides checked: {len(processed_fingerprints)}; "
                    f"external links found: {len(observed_links)}"
                )

            log(f"[OK] Scan complete. New links emailed: {new_links}")
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
