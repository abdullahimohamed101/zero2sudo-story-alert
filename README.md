# zero2sudo Story Alert — Railway Edition

A small personal monitor for the public Instagram account `@zero2sudo`.

Every Railway run:

1. Opens `@zero2sudo` Stories using your previously authenticated Instagram session.
2. Checks the active Story slides.
3. Extracts external links exposed by the Story UI.
4. Emails unseen links to `itsabdulmohamed101@gmail.com`.
5. Saves emailed links in SQLite so they are not sent twice.
6. Exits.

It does not bypass login challenges, CAPTCHAs, private-account restrictions, or rate limits.

---

## What you need

- GitHub account
- Railway account
- Your own Instagram account
- Email inbox: `itsabdulmohamed101@gmail.com`
- Resend account and API key for Railway email delivery
- Python 3.10+ on your computer for one short, one-time Instagram login

Your laptop does **not** run the monitor after setup.

---

# PART 1 — One-time Instagram session export

This is the only step that needs a browser on your computer.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python export_session.py
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
python export_session.py
```

A Chromium window opens.

1. Log into **your own Instagram account** normally.
2. Wait until the Instagram home page is fully loaded.
3. Return to the terminal.
4. Press Enter.
5. The script prints a long Base64 value.

Copy only the text between:

```text
-----BEGIN VALUE-----
...
-----END VALUE-----
```

That is your `INSTAGRAM_STORAGE_STATE_B64`.

**Treat it like a password.** It contains your authenticated browser session.

You can delete `instagram-storage-state.json` after Railway is working.

---

# PART 2 — Email API

Railway disables outbound SMTP on Free, Trial, and Hobby plans, so the deployed
bot sends email through Resend's HTTPS API.

1. Create a Resend account using the same address as `ALERT_EMAIL`.
2. Create an API key.
3. Save it as the Railway variable `RESEND_API_KEY`.

The default sender is `Story Alert <onboarding@resend.dev>`. Resend permits that
testing sender only when `ALERT_EMAIL` is the address on your Resend account. To
send elsewhere, verify a domain in Resend and set `EMAIL_FROM` to an address on
that domain.

`GMAIL_APP_PASSWORD` remains available as a fallback for local runs or Railway
Pro, where outbound SMTP is available. Do **not** use your normal Gmail password.

---

# PART 3 — Put the project on GitHub

Create a new private GitHub repository, for example:

```text
zero2sudo-story-alert
```

Push these files to it.

Do not commit:

```text
instagram-storage-state.json
.env
```

They are already in `.gitignore`.

---

# PART 4 — Deploy on Railway

1. In Railway, create a new project.
2. Choose **Deploy from GitHub repo**.
3. Select your new repository.
4. Railway will detect the root `Dockerfile`.

Add these Railway variables:

```text
TARGET_USERNAME=zero2sudo
ALERT_EMAIL=itsabdulmohamed101@gmail.com
RESEND_API_KEY=<your Resend API key>
EMAIL_FROM=Story Alert <onboarding@resend.dev>
INSTAGRAM_STORAGE_STATE_B64=<the long value from export_session.py>
DATA_DIR=/data
```

Do not put quotes around the values.

---

# PART 5 — Add persistent storage

The SQLite database needs to survive between cron executions.

In the Railway service:

1. Add a **Volume**.
2. Mount it at:

```text
/data
```

The bot will store:

```text
/data/stories.db
```

Nothing else needs to be persisted.

---

# PART 6 — Make it run every 5 minutes

Configure the Railway service as a Cron Job with:

```text
*/5 * * * *
```

Each run executes the Docker image's default command:

```text
python main.py
```

The script performs one scan and exits.

Railway cron schedules use UTC, but `*/5 * * * *` is every five minutes regardless of timezone.

---

# PART 7 — First verification

Run/deploy the service manually once before relying on the cron.

Healthy logs look like:

```text
[->] Checking @zero2sudo
[OK] Scan complete. New links emailed: 0
```

When a new external Story link exists:

```text
[NEW] https://company.com/jobs/...
[OK] Email sent: https://company.com/jobs/...
[OK] Scan complete. New links emailed: 1
```

You should receive:

```text
🚨 New internship link from @zero2sudo
```

in Gmail.

### Simulate a Story email

To test the full deduplication and email delivery path without waiting for a
real Story, temporarily add this Railway variable:

```text
SIMULATED_STORY_URL=https://example.com/story-email-test-1
```

Redeploy or manually run the service. The first run sends one test email and
logs:

```text
[TEST] Simulating Story link: https://example.com/story-email-test-1
[TEST] Simulated Story emails sent: 1
```

Later runs use the database to suppress that same URL while continuing the real
Instagram scan. Change the final test number to send another test, or remove the
variable when finished.

---

# If Instagram logs you out

The service logs:

```text
[!] Instagram session is no longer authenticated.
```

It also attempts to email you a warning.

Fix:

```bash
python export_session.py
```

Then replace `INSTAGRAM_STORAGE_STATE_B64` in Railway with the new value.

The project intentionally stops instead of attempting to bypass Instagram authentication challenges.

---

# Important limitation

Instagram changes its Story HTML frequently. This monitor checks outbound links that are actually exposed in the authenticated Story page DOM.

If Instagram changes how link stickers are represented, the extraction selector may need an update.

That is separate from Railway deployment—the hosted infrastructure can still be functioning even if Instagram changes the Story UI.
