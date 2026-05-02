# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Single-file Python script (`global_brief.py`) that generates a daily global financial brief for a retail investor based in Thailand. It fetches live market data from Yahoo Finance, calls Gemini 2.5 Flash to produce a structured markdown brief, optionally saves it as a `.md` file locally, and sends it to a Telegram chat.

## Running the script

```bash
# One-off live run (requires .env to be present)
python3 global_brief.py

# Dry run — generates brief but skips Telegram send
python3 global_brief.py --dry-run

# Scheduled via cron at 09:00 Bangkok time (UTC+7 = 02:00 UTC)
# 0 2 * * * python3 /path/to/global_brief.py >> /path/to/run.log 2>&1
```

## Environment setup

Copy `.env.example` to `.env` and fill in the required vars:

```
GEMINI_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Optional vars:

- `SAVE_MARKDOWN=true` — save a `.md` backup of the brief (default: `false`)
- `BRIEF_OUTPUT_DIR=...` — where to save `.md` files (default: `~/Documents/Finance`; the `bk/` folder in this repo is used locally)
- `DRY_RUN=true` — same as `--dry-run` flag; generates brief but skips Telegram

The script loads `.env` itself with a built-in parser — no `python-dotenv` needed. It uses only stdlib (`os`, `sys`, `time`, `json`, `urllib`, `datetime`, `pathlib`, `textwrap`, `urllib.parse`).

## Architecture

Everything lives in `global_brief.py`. The flow is linear:

1. `load_dotenv()` — reads `.env` into `os.environ`
2. `validate()` — exits early if any required env var is missing
3. `fetch_all_prices()` — fetches 27 tickers from Yahoo Finance v8 API (no key required), with 0.3s rate limiting between calls
4. `format_price_block()` — formats fetched prices into a structured text block injected into the Gemini prompt
5. `build_prompt(price_block)` — returns the full Gemini prompt; instructs Gemini to use the live price block, scan financial news, and write the brief in a fixed markdown format
6. `call_gemini()` — sends prompt, retries up to 3 times with exponential backoff (handles 429 rate limits), returns raw markdown string
7. `save_markdown()` — writes raw markdown to disk (only if `SAVE_MARKDOWN=true`)
8. `send_telegram()` — splits output into ≤4096-char chunks, posts each via Telegram with `parse_mode="Markdown"`
9. `send_fail_alert()` — sends a short plain-text error notification to Telegram if the script crashes

Execution is tracked in `run.log` (same directory as the script) with statuses: `START`, `FETCH`, `GEMINI`, `SAVE`, `DONE`, `FAIL`.

## Key design decisions

- **Live data injected into prompt**: Yahoo Finance prices are fetched in Python first, then passed as a static block to Gemini. Gemini is told to use only those numbers and not substitute from its own memory.
- **Gemini writes the brief**: the prompt includes exact section headers, table schemas, and format instructions — Gemini fills in the content. The Python script is orchestration only.
- **Telegram with `parse_mode="Markdown"`**: raw markdown is sent to Telegram. Fail alerts use `parse_mode=""` (plain text) since they contain no markdown.
- **No external dependencies**: stdlib only — runs anywhere Python 3 is available without a `pip install` step.
- **`bk/` folder**: local markdown backup files live here (committed as examples); in production `BRIEF_OUTPUT_DIR` should be set to a preferred path.
