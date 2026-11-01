# V2 operations

For the simple application-name workflow on Windows, start with the [Git Bash and Resend guide](windows-git-bash.md). SMTP and Cron below are optional alternatives.

V2 implements all five phases of the specification. OpenAI is the first LLM provider; Gemini and Docker are optional and not required. Python 3.11+ is supported. External service credentials are not needed for automated tests.

## Installation and private storage

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[llm]'
cp config.example.json config.local.json
# Development verification:
python -m pip install -e '.[test,llm]'
python -m pytest -q
python -m pip check
```

Use **absolute paths** for `db_path`, `report_dir`, and `export_dir` in production. Relative paths resolve against the working directory, not the configuration file. Keep data directories private (mode 700) and configuration/secret files at mode 600. Databases and generated report files are protected with mode 600. Protect backups as well.

JSON configuration and exported environment variables are the configuration sources; `.env` is loaded automatically next to the selected JSON configuration (or from the working directory when none is selected), without overriding existing environment variables. Never put credentials in Git, command arguments, or logs. Back up SQLite while the application is stopped, or use SQLite's `backup` API rather than copying a database during writes without consistency guarantees.

## Manual workflow and migration

```sh
playstoregetreviews collect --config config.local.json
playstoregetreviews analyze --config config.local.json
playstoregetreviews report --config config.local.json
playstoregetreviews report --config config.local.json --report-id 1
playstoregetreviews email-preview --config config.local.json --report-id 1
playstoregetreviews send-email --config config.local.json --report-id 1
playstoregetreviews email-status --config config.local.json --delivery-id 1
playstoregetreviews export --config config.local.json
```

Use `--package` to select one configured application for `analyze`, `report`, and `run`. Legacy scripts remain available after installation. Import the legacy database with `migrate --source ... --package ...`, with a nonexistent destination configured in JSON. The original database is not modified. Back up before migration.

Opening an existing V2 database adds new tables and nullable `analysis_reviews.review_date` and `applications.recipient_email` columns without deleting rows. Migration is additive and repeatable. Old analysis snapshot dates remain unknown rather than being reconstructed from potentially modified reviews.

## Phase 3: reports

`report` consumes completed analyses not yet linked to a report. It writes `report-ID.json` and `report-ID.html`, stores the immutable document in SQLite, and supports regeneration by ID. Each file is replaced atomically. If either write fails, the document stays in SQLite and incomplete rendering is retried by the next command.

A report includes application, generation/analysis periods in UTC, review period, distinct source count, summaries, priorities, recommendations, confidence, limitations, models, and known usage. Source references and hashes provide traceability without re-exporting raw review text. Old analyses can have unknown/partial review periods, explicitly noted in the document. New analyses snapshot review dates before their LLM call.

Reports cover new batches since the previous report. Multiple versions of a review in those batches count once, using the latest version. Issues with matching titles and categories are merged, counting distinct references. Different titles are not assumed to describe the same issue. Costs cover all consumed analyses, including superseded versions; unknown usage or prices remain `null`.

A per-application source-version fingerprint suppresses identical reports. Returning to an already-reported set of versions reuses the initial report rather than producing another email. Jinja2 autoescaping protects HTML; no model text is marked safe. Templates ship inside the package and work independently of the current directory.

## Phase 4: SMTP email

Sending is **disabled by default**. Merge this example into the private configuration:

```json
{
  "email": {
    "provider": "smtp",
    "enabled": true,
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "from_address": "reports@example.com",
    "auth_required": true,
    "max_attempts": 3,
    "max_deliveries_per_run": 10
  },
  "apps": [{
    "package_name": "com.example.app",
    "name": "Example App",
    "recipient_email": "authorized@example.com",
    "recipient_authorized": true,
    "interval_minutes": 1440
  }]
}
```

`recipient_authorized: true` records the operator's confirmation of recipient consent. Recipients never come from the LLM; there is no arbitrary `--to` override. Simple mailbox addresses are validated; display names and header newlines are rejected.

Provide `PLAYSTORE_SMTP_USERNAME` and `PLAYSTORE_SMTP_PASSWORD` using a secret manager or private exported variables. SMTP needs process-accessible credentials: a placeholder substituted only by an HTTPS proxy is insufficient. Use `auth_required: false` only for an explicitly authorized encrypted relay without authentication.

The transport supports STARTTLS or implicit TLS (`security: ssl`, typically port 465), with certificate and hostname validation; plaintext is not supported. Permit the SMTP host/port through the appropriate TCP route independently of HTTPS policies. The standard SMTP transport does not use `HTTPS_PROXY`.

`email-preview` writes a multipart `.eml` and HTML preview without connecting or reserving delivery. The email summarizes key recommendations and attaches the complete JSON/HTML report. Previewing requires a sender and an authorized recipient, but not credentials or enabled SMTP. Inspect the preview before sending real messages.

A transaction reserves each report/recipient pair. Retries use a stable `Message-ID`. Confirmed deliveries are not resent. Definite pre-acceptance failures (`failed`) can be retried by the next command or pipeline, up to a persistent `max_attempts`. Exhausted deliveries do not block eligible newer reports. Reports remain available after failure, and credentials/raw external error messages are never logged.

**SMTP cannot guarantee exactly-once delivery in every failure scenario.** A timeout or disconnect during submission may follow acceptance; this becomes `unknown` and blocks automatic retry. A process crash can leave `sending`, which is also blocked. Stop the owning process and check provider logs before resolving:

```sh
# The server confirms acceptance:
playstoregetreviews email-resolve --config config.local.json --delivery-id 1 --outcome sent
# The server confirms the message was NOT accepted:
playstoregetreviews email-resolve --config config.local.json --delivery-id 1 --outcome retry
```

Do not select `retry` without that confirmation: a stable `Message-ID` does not require every server to deduplicate. Delivery history and addresses returned by `email-status` are private. No empty report email is generated without a successful analysis and source reviews.

## Phase 5: pipeline and Cron

```sh
playstoregetreviews run --config /etc/playstoregetreviews/config.json
playstoregetreviews run --config /etc/playstoregetreviews/config.json --package com.example.app --force
```

After success, the pipeline respects each application's `interval_minutes` (default 1440). It collects reviews, analyzes pending versions, renders reports, and sends unsent reports when globally enabled and authorized for that application. Otherwise it only generates reports. LLM batch and delivery limits are shared across the command's applications.

A backlog or failure leaves the run incomplete (nonzero exit status) and retryable without repeating successful analyses or confirmed messages. Empty/oversized review texts remain skipped without triggering a paid retry loop. `--force` bypasses cadence, never deduplication or reservations. A busy application is reported as incomplete without making external calls.

SQLite reservations prevent overlapping pipelines for one application. Manual analysis and delivery retain their own reservations. After a crash, stop the owning process before recovery:

```sh
playstoregetreviews run-recover --config config.local.json --package com.example.app
# Only if an analysis is also left active:
playstoregetreviews analysis-recover --config config.local.json --analysis-id 1
```

These commands do not reset uncertain emails. Resolve those separately as described above.

Adapt [run-scheduled.sh](../deploy/run-scheduled.sh) and [playstoregetreviews.cron.example](../deploy/playstoregetreviews.cron.example). Keep `runtime.env` outside Git with mode 600. It exports absolute `PLAYSTORE_BIN`/`PLAYSTORE_CONFIG` paths and required secrets. The wrapper sources this file as trusted shell code; never use a file supplied by an untrusted party. Create a private log directory (mode 700) and configure log rotation. Install the adapted entry with `crontab -e` on the execution host. No active Cron job is installed automatically in the development environment.

## Validation and external prerequisites

Tests exercise the full workflow with synthetic data, mocked transports, and a local SMTP TLS server using a verified certificate: escaped reports, multiple batches, additive migration, retries, uncertain acceptance, reservations, consent, scheduling, and absence of repeated analysis/delivery. The local test needs OpenSSL and permission to open a loopback socket. GitHub Actions targets Linux and Windows with Python 3.11, 3.12, and 3.13 and builds the package; configuration alone does not establish remote CI success.

Production still requires permitted HTTPS access to `play.google.com` and `api.openai.com`, LLM credentials/model access, and an authorized SMTP TCP route and credentials. Actual collection, paid LLM calls, and external email delivery require validation with those prerequisites. Do not activate scheduled delivery before configuration and verification. Reports, databases, logs, previews, and local settings are ignored by Git; old private history is not automatically purged.
