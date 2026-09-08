import base64
import json
from playwright.sync_api import sync_playwright

OUTPUT_FILE = "instagram-storage-state.json"

print("""
This opens a local Chromium window ONLY so you can log in to your own Instagram account.
Your password is typed directly into Instagram, not into this script.

After Instagram is fully logged in, return to this terminal and press Enter.
""")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")

    input("Press Enter AFTER you are fully logged in to Instagram... ")

    state = context.storage_state()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

    encoded = base64.b64encode(
        json.dumps(state, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    browser.close()

print("\nSUCCESS.")
print(f"A local backup was saved to: {OUTPUT_FILE}")
print("\nCopy the value BETWEEN the lines below into Railway as:")
print("INSTAGRAM_STORAGE_STATE_B64")
print("\n-----BEGIN VALUE-----")
print(encoded)
print("-----END VALUE-----")
print("\nTreat this value like a password. Do not commit or share it.")
