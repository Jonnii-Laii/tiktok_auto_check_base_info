# TikTok Seller Violation Score Monitor (Python)

A lightweight automation template for monitoring TikTok Seller `violation_score` daily using:

- **Playwright** for authenticated browser context
- **APScheduler** for daily scheduling
- Optional webhook alerting

## Features

- Bootstrap and persist login session (`storage_state.json`)
- Fetch violation overview from seller internal API in an authenticated browser context
- Parse and check `violation_score`
- Alert when score is not zero
- Daily schedule with cron expression

## Quick Start

### 1) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2) Configure environment

```bash
cp .env.example .env
# edit .env with your seller values
```

### 3) Bootstrap login session

```bash
python monitor.py bootstrap-login
```

A Chromium window opens; log in manually, then press Enter in terminal to save session.

### 4) Run once

```bash
python monitor.py run-once
```

### 5) Run daily scheduler

```bash
python monitor.py schedule
```

## Notes

- This project calls an internal endpoint. Parameters/tokens may evolve.
- Keep request frequency low to reduce risk of anti-bot triggers.
- For server deployment, ensure timezone consistency and secure `.env`/session file handling.
