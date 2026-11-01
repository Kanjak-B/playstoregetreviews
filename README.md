# PlayStoreGetReviews V2.1

Collect public Google Play reviews, track negative and modified reviews in SQLite, analyze them with OpenAI, and generate JSON/HTML reports for authorized email recipients through Resend or SMTP. This implements the five phases in [doc/specifications.md](doc/specifications.md). Emails are disabled by default. There is no web interface, mass mailing, or multi-tenant service.

## Start with an application name (Windows / Git Bash)

```bash
py -3 -m venv .venv
source .venv/Scripts/activate
python -m pip install -e '.[llm]'
playstoregetreviews start "WhatsApp"
```

The guided command finds the package ID, asks you to choose if results are ambiguous, and runs collection, analysis, and reporting. First-use setup asks for an OpenAI key and offers Resend email; subsequent runs reuse the private local settings. Use `--email` to configure Resend later, or paste an exact Play Store application URL instead of its name. SMTP and Cron are not required for this route.

See the **[Windows + Git Bash + Resend guide](doc/windows-git-bash.md)** for the short setup, Resend sender restrictions, and optional Windows Task Scheduler settings.

## Install and test

Python 3.11 or newer is required:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[llm]'
cp config.example.json config.local.json
# Development dependencies and tests:
python -m pip install -e '.[test,llm]'
python -m pytest -q
python -m pip check
```

The test suite uses synthetic data, mocked external APIs, and a verified local SMTP TLS server. It never sends mail to external recipients. The CI workflow targets Python 3.11–3.13 on Linux and Windows. The local TLS integration test requires OpenSSL and permission to open a loopback socket. Docker is optional and is not required or included.

## Configure

Edit `config.local.json` to select applications, languages, countries, negative-score thresholds, fetch limits, report paths, and schedules. `--config` takes precedence over the exported `PLAYSTORE_CONFIG` variable. Paths are relative to the working directory; use absolute paths for scheduled runs. `.env` is loaded automatically next to the selected JSON configuration, or from the working directory when none is selected. Existing environment variables take precedence.

Without a configuration file, the legacy application (`com.visa.cemea.visapay`, French, country `cd`), `reviews.db`, and `exports/` paths are retained. The example uses `data/reviews.db` for a new V2 database. The old SQLite schema must be imported explicitly rather than overwritten.

Store credentials outside Git. OpenAI uses `PLAYSTORE_LLM_API_KEY`, falling back to an existing `OPENAI_API_KEY`. Resend uses `RESEND_API_KEY`; optional SMTP uses `PLAYSTORE_SMTP_USERNAME` and `PLAYSTORE_SMTP_PASSWORD`. The default LLM is `gpt-4o-mini`; select a model supporting strict structured outputs and `max_completion_tokens`. The SDK is optional for collection and export, and is initialized lazily.

## Commands

```sh
playstoregetreviews collect --config config.local.json
playstoregetreviews analyze --config config.local.json
playstoregetreviews report --config config.local.json
playstoregetreviews email-preview --config config.local.json --report-id 1
# Only after configuring SMTP and authorizing the recipient:
playstoregetreviews send-email --config config.local.json --report-id 1
# Complete pipeline respecting each application's interval:
playstoregetreviews run --config config.local.json
playstoregetreviews export --config config.local.json
```

`--package` selects one configured application for `analyze`, `report`, or `run`. The original `collect_negative_reviews.py` and `export_reviews.py` entry points remain available after installation, including `--config` support. Exports retain the `score,content` CSV columns and equivalent JSON objects, with distinct timestamped filenames. A combined export includes all stored applications.

The **[operations guide](doc/operations.md)** covers migration, report generation, SMTP settings and consent, delivery history and recovery, quotas, scheduling, backups, and deployment requirements. Ready-to-adapt Cron files are in [`deploy/`](deploy/).

## Collection and storage

The collector paginates up to each application's limit, applies timeouts and bounded retries, and logs JSON counters without review contents or raw external errors. Failure returns a nonzero exit status without preventing independent applications from being processed. Reviews saved before a validation error remain stored; retrying is idempotent.

The pinned scraper's public API swallows some network errors and disables the default TLS verification context at import. Our adapter restores verified TLS and reuses its internal parser so errors remain visible. Revalidate that adapter when upgrading the scraper. An empty response can legitimately contain no reviews; it is not proof of external-service readiness.

SQLAlchemy/SQLite enforces uniqueness by application and review ID. A hash of score, content, and review date detects changes. First-seen timestamps are preserved, last-seen timestamps are updated, and modified reviews return to `pending` analysis status. Existing reviews are updated even if their score becomes positive. Newly encountered positive reviews are ignored; reviews missing from a later fetch are not deleted.

To import a legacy database, back it up, configure a **nonexistent destination**, and explicitly identify the owning application:

```sh
playstoregetreviews migrate --source reviews.db --package com.visa.cemea.visapay --config config.local.json
```

The source remains untouched. The optional legacy webhook uses `REVIEW_WEBHOOK_URL` only for new negative reviews; failures are logged without URLs. Durable webhook delivery retries are not implemented.

## Analysis and reports

Only pending reviews at or below the configured score threshold are analyzed. Whitespace and control characters are normalized; recognizable email addresses, phone numbers, and URLs are masked. Author names, real review IDs, and review dates are not sent to the LLM. This is **best-effort masking**, not guaranteed anonymization of names, addresses, or other personal information in free text. Review the data and applicable Google Play terms before collection or external analysis.

Identical prepared texts with the same score are grouped within a batch, preserving opaque references to every source. Empty or oversized texts are skipped without truncation. System instructions treat application metadata and review contents as untrusted data, request uncertainty and limitations, and distinguish reported symptoms from unverified technical causes. No tools or code execution are enabled. Human review is still necessary.

Pydantic rejects extra properties, invalid priorities/categories, unknown or repeated references, refusals, and truncated responses. Counts are calculated locally from distinct source references, including all members of a deduplicated group; they are never accepted from the model. Separate issues may overlap in their sources.

SQLite stores validated results, source-version hashes, model/provider, prompt version, timestamps, and known token usage. Completed unchanged versions are not reanalyzed merely because the model setting changes. If a review changes during an LLM call, its new version remains pending. Invalid raw responses and remote exception messages are not stored. Known consumption is retained even when response validation fails.

```sh
playstoregetreviews analysis-show --config config.local.json --analysis-id 1
```

This output contains private references and must not be published. Generated reports include study periods, source counts, summaries, priorities, recommendations, confidence, limitations, and consumption metadata. HTML is escaped, source versions are deduplicated, and reports remain available after delivery failures.

## Limits and reliability

The `analysis` configuration sets `model`, `max_reviews_per_batch` (40), `max_batches_per_run` (5), `max_input_chars` (24000), `max_output_tokens` (3000), and `timeout_seconds` (60). Batch attempts are shared across applications in a command. The JSON schema adds input overhead beyond message characters. These are volume limits, not a monetary spending cap.

Set both `input_cost_per_million` and `output_cost_per_million` to current USD prices to estimate costs from reported usage. Missing usage or prices remain `null`, never an assumed zero. Provider billing remains authoritative; discounts are not inferred. There are no automatic paid LLM retries. A timeout may have been billed even when its usage is unknown.

Application-level reservations prevent overlapping analyses and scheduled pipelines. Interrupted reservations require explicit recovery after stopping their owner; see the operations guide. SMTP delivery also distinguishes confirmed failures from uncertain acceptance. An uncertain message is never automatically resent: SMTP cannot guarantee exactly-once delivery in all failure scenarios.

## Privacy and deployment

Databases, reports, exports, logs, previews, `.env`, and local configuration are ignored by Git. Historical data removed from tracking remains on disk and in earlier Git history; history is not automatically purged. Do not republish those private files.

Production requires authorized HTTPS access to `play.google.com` and `api.openai.com`, LLM credentials, and either HTTPS access to `api.resend.com` plus a Resend key, or an independently permitted SMTP TCP route plus credentials. Collection, paid LLM analysis, and actual external email delivery still require validation with those services. No active Cron job or real email recipient is installed automatically. The development validation uses synthetic data and local/mocked transports only.
