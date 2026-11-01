# Simple start: Windows, Git Bash, and Resend

You can enter a Play Store **application name** or paste its exact Play Store URL. You do not need to find the package ID, edit JSON, configure SMTP, or install Cron for a manual run.

## 1. Install once

Install Python 3.11+ and Git for Windows. Open **Git Bash**:

```bash
git clone https://github.com/Kanjak-B/playstoregetreviews.git
cd playstoregetreviews
py -3 -m venv .venv
source .venv/Scripts/activate
python -m pip install -e '.[llm]'
```

If the repository is already installed, activate the environment, run `git pull --ff-only origin main`, and repeat the `pip install` command to update dependencies.

## 2. Enter the application name

```bash
playstoregetreviews start "WhatsApp"
```

The program searches Google Play. If there are multiple results, choose by title and publisher; it never silently selects an ambiguous match. Alternatively, supply an exact link:

```bash
playstoregetreviews start "https://play.google.com/store/apps/details?id=com.whatsapp"
```

On first use, the program asks for an OpenAI key if one is not already available. Key input is hidden and saved in `.env` next to the local configuration. Analysis calls can be billed. The initial defaults fetch up to 20 reviews and analyze at most one batch of 20 sources; only eligible negative reviews are sent. Existing configured limits are preserved.

Choose whether you also want the report by email. Without email, a local HTML/JSON report is still generated. The last available HTML report path is printed after the run. If no report is generated, check whether the application has negative reviews for the configured language and country, or whether collection/analysis failed.

The default discovery language/country are `fr`/`cd`. Collection preferences and resource limits remain editable in `config.local.json` when needed.

## 3. Use Resend instead of SMTP

Choose email in the guided start, or enable it later:

```bash
playstoregetreviews start "WhatsApp" --email
```

The program asks for:

- Your `RESEND_API_KEY`, using hidden input.
- Your authorized recipient address.
- A sender allowed by your Resend account.

For Resend's test sender, the default is `onboarding@resend.dev`. Resend restricts which recipients can be used with that sender; normally use the email associated with your Resend account. For other recipients, use a sender on your verified domain and the appropriate account permissions. An arbitrary Gmail address is not a verified Resend sender just because you own its mailbox.

Only the OpenAI and Resend API keys are needed for this route. There is no SMTP host, port, password, or STARTTLS setup. HTTPS access is required to `play.google.com`, `api.openai.com`, and `api.resend.com`.

The existing preview and delivery-history commands also work with Resend:

```bash
playstoregetreviews email-preview --config config.local.json --report-id 1
playstoregetreviews email-status --config config.local.json --delivery-id 1
```

Replace IDs with those shown in the logs. Resend sends the same summary and JSON/HTML attachments as the preview. Delivery history prevents repeat sends after confirmed acceptance. A stable API idempotency key adds provider-side protection within Resend's retention window; it is not an unlimited exactly-once guarantee. Timeouts, server failures, and uncertain responses block automatic retry and use the existing `email-resolve` recovery procedure.

## 4. Next time

Open Git Bash in the repository:

```bash
source .venv/Scripts/activate
playstoregetreviews start "WhatsApp"
```

The program reuses saved application settings and credentials. Multiple search matches still require an explicit choice. Unchanged successfully analyzed reviews and confirmed report deliveries are not repeated. Manual `start` bypasses scheduled cadence, but never bypasses deduplication or active reservations.

Keys are stored locally in `.env`; non-secret settings go in `config.local.json`. Both are ignored by Git. Do not upload, share, or copy these files into public folders. On Windows, Unix permission bits do not configure NTFS access controls: restrict access through your Windows account and file security settings. Existing exported environment variables take precedence over `.env` values.

## Optional: Windows Task Scheduler

Cron is not needed on Windows. After validating a manual run, create a task in Windows Task Scheduler:

- Program: the absolute path to `.venv\Scripts\playstoregetreviews.exe`.
- Arguments: `run --config "C:\absolute\path\config.local.json"`.
- Start in: the repository directory.
- Trigger: your preferred interval, for example every 15 minutes.

The application applies each configured `interval_minutes` after successful runs. Guided setup uses absolute data/report paths, and `.env` is loaded next to the chosen JSON configuration. The scheduled task must run under an account allowed to read those private files. No task is installed automatically.

## Tests and support boundary

The CI matrix includes Windows and Linux on Python 3.11–3.13. The Cron wrapper test is Unix-only, and the local SMTP TLS test requires OpenSSL. A configured CI matrix is not evidence that a Windows job already passed: inspect GitHub Actions for the actual result. Real Google Play searches, OpenAI calls, and Resend deliveries require their network routes and credentials; unit tests mock those services without sending real email.
