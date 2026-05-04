# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Daily financial intelligence routine for a retail investor based in Thailand. It fetches live market data from Yahoo Finance, calls Gemini 2.5 Flash to produce a structured markdown brief, optionally saves it as a `.md` file locally, and sends it to a Telegram chat.

**Phase 1 (done):** Single-file script (`global_brief.py`) covering 27 global tickers with a macro market brief.

**Phase 2 (in progress):** Asset-specific news impact analysis. A user-maintained `assets.yaml` watchlist (stocks, ETFs, mutual funds) is loaded at startup; each asset is priced via Yahoo Finance (where available) and passed to Gemini to assess how today's news affects it. Output is an "Asset Impact Watch" table appended to the daily brief. See `ROADMAP.md` for full spec.

## Running the script

```bash
# One-off live run (requires .env to be present)
python3 global_brief.py

# Dry run — generates brief but skips Telegram send
python3 global_brief.py --dry-run

# Scheduled via cron at 09:00 Bangkok time (UTC+7 = 02:00 UTC)
# 0 2 * * * python3 /path/to/global_brief.py >> /path/to/run.log 2>&1
```

### GitHub Actions (recommended for automation)

The workflow at `.github/workflows/global-brief.yml` runs automatically at 09:00 Bangkok time daily. No local machine needed.

Required GitHub Secrets (Settings → Secrets → Actions):
- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Trigger manually via Actions → Global Financial Brief → Run workflow.

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

## Files

| File | Purpose |
|------|---------|
| `global_brief.py` | Main script — all logic lives here |
| `assets.yaml` | User watchlist: stocks, ETFs, mutual funds to track (Phase 2) |
| `.env` | Secrets — not committed |
| `.env.example` | Template for required env vars |
| `.github/workflows/global-brief.yml` | GitHub Actions workflow — runs daily at 09:00 Bangkok |
| `bk/` | Local markdown backup files (examples) |
| `ROADMAP.md` | Phase-by-phase implementation plan |
| `run.log` | Execution log written by the script |

## Architecture

Everything lives in `global_brief.py`. The flow is linear:

1. `load_dotenv()` — reads `.env` into `os.environ`
2. `validate()` — exits early if any required env var is missing
3. `load_watchlist()` — parses `assets.yaml`; separates tickers with live prices from manual-NAV mutual funds *(Phase 2)*
4. `fetch_all_prices()` — fetches 27 core tickers + watchlist tickers from Yahoo Finance v8 API (no key required), with 0.3s rate limiting between calls
5. `format_price_block()` — formats fetched prices into a structured text block injected into the Gemini prompt
6. `build_prompt(price_block)` — returns the full Gemini prompt; instructs Gemini to use the live price block, scan financial news, write the brief in a fixed markdown format, and produce the Asset Impact Watch table *(extended in Phase 2)*
7. `call_gemini()` — sends prompt, retries up to 3 times with exponential backoff (handles 429 rate limits), returns raw markdown string
8. `save_markdown()` — writes raw markdown to disk (only if `SAVE_MARKDOWN=true`)
9. `send_telegram()` — splits output into ≤4096-char chunks, posts each via Telegram with `parse_mode="Markdown"`
10. `send_fail_alert()` — sends a short plain-text error notification to Telegram if the script crashes

Execution is tracked in `run.log` (same directory as the script) with statuses: `START`, `FETCH`, `GEMINI`, `SAVE`, `DONE`, `FAIL`.

## assets.yaml (Phase 2)

User-maintained watchlist supporting three asset types:

- **stock** — Thai or global equities (e.g. `PTT.BK`, `AAPL`)
- **etf** — Exchange-traded funds (e.g. `GLD`, `VT`, `EEMA`)
- **mutual_fund** — Thai or global funds; set `nav_source: manual` if Yahoo Finance does not carry the NAV (price will show as `NAV N/A` but impact is still assessed)

Each entry requires: `type`, `ticker` (or `null`), `name`, `sector`, `currency`, `note`. The `note` field is the key input Gemini uses to reason about news relevance for that specific asset. See `ROADMAP.md §2.1` for the full schema.

## Key design decisions

- **Live data injected into prompt**: Yahoo Finance prices are fetched in Python first, then passed as a static block to Gemini. Gemini is told to use only those numbers and not substitute from its own memory.
- **Gemini writes the brief**: the prompt includes exact section headers, table schemas, and format instructions — Gemini fills in the content. The Python script is orchestration only.
- **`note` field drives impact quality**: for each watchlist asset, the `note` in `assets.yaml` tells Gemini what macro themes or risks drive that asset. Without it, impact assessment would be generic.
- **Mutual funds without live NAV**: assets with `nav_source: manual` skip the Yahoo fetch and receive `NAV N/A` in the price column, but Gemini still assesses news impact from sector/macro context.
- **Telegram with `parse_mode="Markdown"`**: raw markdown is sent to Telegram. Fail alerts use `parse_mode=""` (plain text) since they contain no markdown.
- **No external dependencies**: stdlib only — runs anywhere Python 3 is available without a `pip install` step. `assets.yaml` is parsed with a minimal built-in parser, no `pyyaml` needed.
- **`bk/` folder**: local markdown backup files live here (committed as examples); in production `BRIEF_OUTPUT_DIR` should be set to a preferred path.
