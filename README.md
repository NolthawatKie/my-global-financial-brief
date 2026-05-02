# Global Financial Brief — Daily Routine

A Python script that generates a daily global financial brief for a retail investor based in Thailand. Runs via cron, fetches live market data from Yahoo Finance, uses Google Gemini to synthesize a structured market analysis, and sends it to Telegram.

## Features

- **Live market data**: 27 tickers (US, APAC, European indexes; commodities; FX; bonds) fetched from Yahoo Finance
- **AI synthesis**: Google Gemini 2.5 Flash produces a structured markdown brief with sentiment analysis and sector breakdowns
- **No dependencies**: stdlib only — runs anywhere Python 3 is available
- **Retry logic**: 3 attempts with exponential backoff for Gemini API calls
- **Execution tracking**: `run.log` logs each run with fetch/gemini/save/telegram statuses
- **Telegram delivery**: Brief sent to your personal Telegram chat; fails alert on script crash
- **Optional backup**: Save markdown locally (configurable directory)

## Quick start

1. Copy `.env.example` to `.env` and fill in:
   ```
   GEMINI_API_KEY=<your-key-from-aistudio.google.com>
   TELEGRAM_BOT_TOKEN=<your-bot-token-from-@BotFather>
   TELEGRAM_CHAT_ID=<your-personal-chat-id-from-@userinfobot>
   ```

2. Run once (test):
   ```bash
   python3 global_brief.py --dry-run
   ```

3. Run for real:
   ```bash
   python3 global_brief.py
   ```

4. Schedule daily at 09:00 Bangkok time (UTC+7):
   ```bash
   # Add to crontab (crontab -e)
   0 2 * * * python3 /full/path/to/global_brief.py >> /full/path/to/run.log 2>&1
   ```

## Configuration

See `.env.example` for all optional vars:
- `SAVE_MARKDOWN` — save `.md` backup locally (default: false)
- `BRIEF_OUTPUT_DIR` — where to save backups (default: `~/Documents/Finance`)
- `DRY_RUN` — generate but skip Telegram (default: false)

## What the brief includes

- **Market pulse**: key overnight drivers and upcoming events
- **Index dashboard**: US, Asia-Pacific, Europe with sentiment
- **Bonds & rates**: 10Y yield, Fed Funds rate, yield curve status
- **Commodities**: gold, crude oil, silver, copper with narratives
- **FX watch**: USD/THB, EUR/USD, USD/JPY, CNY, DXY
- **Top stories**: 5–7 market-moving news with bullish/bearish/neutral tags
- **Thailand spotlight**: BOT policy, THB moves, SET rotation, upcoming data
- **Sentiment scorecard**: asset class by asset class with key drivers
- **Weekly events**: scheduled releases to watch
- **Cross-asset signals**: are gold, yields, equities aligned?
- **Sector impact**: global and APAC sector rotation; Thai SET segments
- **Execution metadata**: timestamp, data sources

## Architecture

- Single file: `global_brief.py`
- Fetches Yahoo Finance data → builds Gemini prompt → calls Gemini → saves (optional) + sends Telegram
- All error handling and retry logic baked in
- See `CLAUDE.md` for detailed architecture

## Logs

`run.log` tracks execution:
```
2026-05-02 15:42:55 | START    | LIVE
2026-05-02 15:42:55 | FETCH    | 27/27 tickers OK
2026-05-02 15:42:55 | GEMINI   | 12036 chars
2026-05-02 15:42:55 | SAVE     | /path/to/backup.md
2026-05-02 15:42:55 | DONE     | OK · LIVE
```

## Requirements

- Python 3.7+
- Active internet (Yahoo Finance, Gemini API)
- Telegram account + bot + chat ID
- Google Gemini API key (free tier works)

## License

MIT
