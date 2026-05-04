"""
global_brief.py  (v2 — live data)
──────────────────────────────────
Global Financial Brief — Claude Code routine
Engine  : Gemini 2.5 Flash (free tier)
Data    : Yahoo Finance (live, stdlib only — no pip install needed)
Outputs : Telegram message (required) + local .md file (optional)
Schedule: cron 09:00 Bangkok (UTC+7)  →  "0 2 * * * python3 /path/to/global_brief.py"

ENV VARS required:
  GEMINI_API_KEY      — from aistudio.google.com
  TELEGRAM_BOT_TOKEN  — from @BotFather on Telegram
  TELEGRAM_CHAT_ID    — your personal chat ID (get from @userinfobot)

Optional:
  SAVE_MARKDOWN       — true/false  (default: false)
  BRIEF_OUTPUT_DIR    — folder for .md backup (default: ~/Documents/Finance)
  DRY_RUN             — true/false  generate brief but skip Telegram (default: false)

Changes from v1:
  + Live price fetch from Yahoo Finance before building prompt
  + Retry logic for Gemini (3 attempts, exponential backoff)
  + Fail alert to Telegram if script crashes
  + --dry-run flag for local testing without sending
  + run.log file tracking each execution
"""

import os
import sys
import time
import datetime
import pathlib
import textwrap
import json
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar

# ── CLI flags ──────────────────────────────────────────────────────────────────
DRY_RUN_FLAG = "--dry-run" in sys.argv

# ── Load .env ──────────────────────────────────────────────────────────────────
def load_dotenv(path=".env"):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
SAVE_MARKDOWN      = os.environ.get("SAVE_MARKDOWN", "false").lower() == "true"
BRIEF_OUTPUT_DIR   = os.environ.get("BRIEF_OUTPUT_DIR",
                        str(pathlib.Path.home() / "Documents" / "Finance"))
DRY_RUN            = DRY_RUN_FLAG or os.environ.get("DRY_RUN", "false").lower() == "true"

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL   = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

BKK_OFFSET = datetime.timezone(datetime.timedelta(hours=7))
NOW_BKK    = datetime.datetime.now(BKK_OFFSET)
TODAY      = NOW_BKK.strftime("%Y-%m-%d")
RUN_TS     = NOW_BKK.strftime("%Y-%m-%d %H:%M:%S")

# ── Validate env ───────────────────────────────────────────────────────────────
def validate():
    missing = []
    if not GEMINI_API_KEY:     missing.append("GEMINI_API_KEY")
    if not TELEGRAM_BOT_TOKEN: missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:   missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print(f"[ERROR] Missing env vars: {', '.join(missing)}")
        sys.exit(1)

# ── run.log ────────────────────────────────────────────────────────────────────
LOG_PATH = pathlib.Path(__file__).parent / "run.log"

def log(status: str, detail: str = ""):
    line = f"{RUN_TS} | {status:<8} | {detail}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())

# ── Yahoo Finance live fetch ───────────────────────────────────────────────────
# Tickers: Yahoo Finance symbol format
TICKERS = {
    # US indexes
    "^GSPC":  "S&P 500",
    "^NDX":   "Nasdaq 100",
    "^DJI":   "Dow Jones",
    "^VIX":   "VIX",
    # APAC
    "^SET.BK": "SET Index",
    "^N225":  "Nikkei 225",
    "^HSI":   "Hang Seng",
    "000300.SS": "CSI 300",
    "^STI":   "STI Singapore",
    "^AXJO":  "ASX 200",
    # Europe
    "^FTSE":  "FTSE 100",
    "^GDAXI": "DAX",
    "^FCHI":  "CAC 40",
    "^STOXX50E": "Euro Stoxx 50",
    # Commodities
    "GC=F":   "Gold (XAU/USD)",
    "BZ=F":   "Brent Crude",
    "CL=F":   "WTI Crude",
    "SI=F":   "Silver",
    "HG=F":   "Copper",
    # FX
    "USDTHB=X": "USD/THB",
    "EURUSD=X": "EUR/USD",
    "USDJPY=X": "USD/JPY",
    "USDCNY=X": "USD/CNY",
    "DX-Y.NYB": "DXY",
    # Bonds (ETF proxies — actual yield data requires paid API)
    "^TNX":   "US 10Y Yield",
    "^FVX":   "US 5Y Yield",
    "^IRX":   "US 3M Yield",
}

_YF_SESSION = None  # (opener, crumb, ua)

_YF_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def _get_yahoo_session():
    global _YF_SESSION
    if _YF_SESSION:
        return _YF_SESSION
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    # Seed cookies
    try:
        opener.open(urllib.request.Request(
            "https://finance.yahoo.com/",
            headers={"User-Agent": _YF_UA, "Accept": "text/html"}
        ), timeout=15)
    except Exception:
        pass
    # Get crumb
    crumb = ""
    try:
        req = urllib.request.Request(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers={"User-Agent": _YF_UA}
        )
        with opener.open(req, timeout=15) as r:
            crumb = r.read().decode().strip()
    except Exception:
        pass
    _YF_SESSION = (opener, crumb)
    return _YF_SESSION

def fetch_yahoo(symbol: str) -> dict:
    """
    Fetch quote from Yahoo Finance v8 API (no key required).
    Returns dict with price, change_pct, currency, market_state.
    Falls back to empty dict on any error.
    """
    opener, crumb = _get_yahoo_session()
    qs  = "?interval=1d&range=2d"
    qs += f"&crumb={urllib.parse.quote(crumb)}" if crumb else ""
    url = (
        "https://query2.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + qs
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": _YF_UA,
        "Accept":     "application/json",
    })
    try:
        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read())
        meta  = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price and prev and prev != 0:
            chg_pct = round((price - prev) / prev * 100, 2)
        else:
            chg_pct = None
        return {
            "price":      round(price, 4) if price else None,
            "change_pct": chg_pct,
            "currency":   meta.get("currency", ""),
            "state":      meta.get("marketState", ""),
        }
    except Exception as e:
        return {"error": str(e)}

def fetch_all_prices() -> dict:
    """Fetch all tickers. Returns {label: {price, change_pct, ...}}"""
    results = {}
    total = len(TICKERS)
    for i, (symbol, label) in enumerate(TICKERS.items(), 1):
        print(f"  [{i:02d}/{total}] {label:<25}", end=" ", flush=True)
        data = fetch_yahoo(symbol)
        results[label] = data
        if data.get("error"):
            print(f"FAIL — {data['error']}")
        else:
            p   = data.get("price", "?")
            chg = data.get("change_pct")
            chg_str = f"{chg:+.2f}%" if chg is not None else "n/a"
            print(f"{p}  {chg_str}")
        time.sleep(0.3)   # gentle rate limiting
    return results

def format_price_block(prices: dict) -> str:
    """Format fetched prices into a readable block to inject into prompt."""
    lines = ["LIVE MARKET SNAPSHOT (fetched just now from Yahoo Finance):"]
    lines.append(f"Fetch time: {RUN_TS} Bangkok (UTC+7)")
    lines.append("")

    sections = {
        "US INDEXES":    ["S&P 500", "Nasdaq 100", "Dow Jones", "VIX"],
        "ASIA-PACIFIC":  ["SET Index", "Nikkei 225", "Hang Seng", "CSI 300",
                          "STI Singapore", "ASX 200"],
        "EUROPE":        ["FTSE 100", "DAX", "CAC 40", "Euro Stoxx 50"],
        "COMMODITIES":   ["Gold (XAU/USD)", "Brent Crude", "WTI Crude",
                          "Silver", "Copper"],
        "FX":            ["USD/THB", "EUR/USD", "USD/JPY", "USD/CNY", "DXY"],
        "US BOND YIELDS (^TNX=10Y, ^FVX=5Y, ^IRX=3M)":
                         ["US 10Y Yield", "US 5Y Yield", "US 3M Yield"],
    }

    for section, labels in sections.items():
        lines.append(f"[{section}]")
        for label in labels:
            d = prices.get(label, {})
            if d.get("error") or not d.get("price"):
                lines.append(f"  {label:<28} : DATA UNAVAILABLE")
            else:
                p   = d["price"]
                chg = d.get("change_pct")
                chg_str = f"{chg:+.2f}%" if chg is not None else "n/a"
                state = d.get("state", "")
                note = f" [{state}]" if state and state != "REGULAR" else ""
                lines.append(f"  {label:<28} : {p}  {chg_str}{note}")
        lines.append("")

    lines.append("NOTE: Thai 10Y gov bond yield — fetch manually or use BOT data.")
    lines.append("NOTE: Fed Funds Rate — current target range is in your knowledge.")
    return "\n".join(lines)

# ── Gemini call with retry ─────────────────────────────────────────────────────
def call_gemini(prompt: str, max_tokens: int = 8192, retries: int = 3) -> str:
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.3,
        }
    }).encode("utf-8")

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            GEMINI_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"[WARN] Gemini attempt {attempt}/{retries} — HTTP {e.code}: {body[:120]}")
            if e.code in (429, 503) and attempt < retries:
                wait = 2 ** attempt
                label = "Rate limited" if e.code == 429 else "Service unavailable"
                print(f"       {label} — waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            print(f"[WARN] Gemini attempt {attempt}/{retries} — {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                raise

# ── Build prompt ───────────────────────────────────────────────────────────────
def build_prompt(price_block: str) -> str:
    return textwrap.dedent(f"""
    You are a financial research assistant for a retail investor based in Thailand.
    Base currency: THB. Today: {TODAY} (Bangkok time, UTC+7).

    Return ONLY the final markdown brief — no preamble, no commentary outside it.

    ═══════════════════════════════════════
    STEP 1 — LIVE MARKET DATA (already fetched)
    ═══════════════════════════════════════
    Use ONLY the numbers below. Do NOT substitute with your own memory.
    If a value shows DATA UNAVAILABLE, mark that cell as "n/a" in the table.

    {price_block}

    Additional data to fill in from your knowledge:
    - Thai 10Y government bond yield (approximate)
    - Fed Funds Rate current target range
    - MSCI World and MSCI EM sentiment (directional, based on US/global moves above)

    ═══════════════════════════════════════
    STEP 2 — SCAN FINANCIAL NEWS (past 24h)
    ═══════════════════════════════════════
    Identify top 5–7 market-moving stories covering:
    - US macro: Fed policy, CPI/PCE, jobs, GDP
    - China economy: stimulus, property, trade data
    - EM Asia: India, Vietnam, Indonesia — any moves affecting SET or THB
    - Thailand macro: BOT policy, THB, SET, foreign fund flows
    - Geopolitics: oil supply, gold safe-haven, Asian trade routes
    - Currency wars: CNY devaluation, BOJ intervention, DXY moves
    - Supply chain: semiconductor, shipping disruption → SET industrials
    - Earnings: major S&P 500 or SET blue-chip surprises
    - Crypto: BTC/ETH headline only (one line)

    ═══════════════════════════════════════
    STEP 3 — WRITE THE BRIEF
    ═══════════════════════════════════════
    Use this EXACT format. Fill every cell and every section.

    ---

    # 🌐 GLOBAL FINANCIAL BRIEF · {TODAY} · {{OVERALL SENTIMENT EMOJI + LABEL}}

    > **Sentiment scale:** 🟢 Bullish · 🟡 Cautious · 🔴 Defensive · ⚫ Mixed
    > **Data source:** Yahoo Finance live fetch · {RUN_TS} BKK

    ---

    ## 🌍 MARKET PULSE
    [3–4 sentences: biggest macro driver overnight, risk-on/off narrative,
    key event or data release to watch today or this week.]

    ---

    ## 📊 INDEX DASHBOARD

    ### 🇺🇸 United States
    | Index | Level | Chg% | Sentiment |
    |-------|-------|-------|-----------|
    | S&P 500 | | | |
    | Nasdaq 100 | | | |
    | Dow Jones | | | |
    | VIX | | | |

    **Segment note:** [1 sentence on US equity mood]

    ---

    ### 🌏 Asia-Pacific
    | Index | Level | Chg% | Sentiment |
    |-------|-------|-------|-----------|
    | SET (Thailand) | | | |
    | Nikkei 225 | | | |
    | Hang Seng | | | |
    | CSI 300 | | | |
    | STI (Singapore) | | | |
    | ASX 200 | | | |

    **Segment note:** [1 sentence — biggest Asian mover and why]

    ---

    ### 🇪🇺 Europe
    | Index | Level | Chg% | Sentiment |
    |-------|-------|-------|-----------|
    | FTSE 100 | | | |
    | DAX | | | |
    | CAC 40 | | | |
    | Euro Stoxx 50 | | | |

    **Segment note:** [1 sentence on European drivers]

    ---

    ## 💰 BONDS & RATES
    | Instrument | Level | Change | Note |
    |------------|-------|--------|------|
    | US 10Y Yield | | | |
    | US 2Y Yield | | | |
    | Thai 10Y Yield | | | |
    | Fed Funds Rate | | | |

    **Yield curve:** [inverted / flat / normal — and what it signals]
    **Impact:** [1 sentence — yield move effect on equities and THB assets]

    ---

    ## 🪙 COMMODITIES
    | Asset | Price | Chg% | Sentiment |
    |-------|-------|-------|-----------|
    | Gold (XAU/USD) | | | |
    | Brent Crude | | | |
    | WTI Crude | | | |
    | Silver | | | |
    | Copper | | | |

    **Copper signal:** [rising = global growth optimism / falling = slowdown concern]
    **Impact:** [1 sentence — oil impact on Thai energy stocks, gold as safe haven?]

    ---

    ## 💱 FX WATCH
    | Pair | Rate | Chg% |
    |------|------|-------|
    | USD/THB | | |
    | EUR/USD | | |
    | USD/JPY | | |
    | USD/CNY | | |
    | DXY | | |

    **THB trend:** [strengthening / weakening vs USD — 1 sentence]
    **CNY watch:** [any devaluation pressure or BOC action — 1 sentence]
    **THB Impact:** [effect on Thai imports, exports, and USD-denominated assets]

    ---

    ## 📰 TOP STORIES
    [Stories 1–7. Each on its own block:]
    **[Headline]** · [Source] · [Region] · [BULLISH / BEARISH / NEUTRAL]
    [2–3 sentences: what happened, market reaction, relevance to Thai investor.]

    ---

    ## 🔦 THAILAND SPOTLIGHT
    [3–4 sentences covering:]
    - BOT: rate decision, statement, or silence
    - THB: move and driver (tourism flows, exports, capital flows)
    - SET: sector rotation, foreign net buy/sell direction
    - Upcoming Thai data this week (GDP, CPI, trade balance, etc.)

    ---

    ## 📐 SENTIMENT SCORECARD
    | Asset Class | Sentiment | Key Driver |
    |-------------|-----------|------------|
    | US Equities | 🟢/🟡/🔴/⚫ | |
    | Asian Equities | 🟢/🟡/🔴/⚫ | |
    | Thai SET | 🟢/🟡/🔴/⚫ | |
    | Gold | 🟢/🟡/🔴/⚫ | |
    | Oil | 🟢/🟡/🔴/⚫ | |
    | Copper | 🟢/🟡/🔴/⚫ | |
    | USD/THB | 🟢/🟡/🔴/⚫ | |
    | Thai Bonds | 🟢/🟡/🔴/⚫ | |
    | **Overall** | 🟢/🟡/🔴/⚫ | |

    ---

    ## 📅 THIS WEEK'S KEY EVENTS
    | Date | Event | Expected Impact |
    |------|-------|----------------|
    | [day] | [event] | [high/medium/low — and on what] |
    [List 3–5 scheduled releases: FOMC, CPI, GDP, BOT MPC, Thai data, major earnings]

    ---

    ## 🔗 CROSS-ASSET SIGNALS
    [2–3 sentences: are gold, yields, and equities telling the same story or diverging?
    Example: "Gold up + yields down + VIX rising = genuine risk-off, not just rotation."
    Call out any conflicting signals that suggest the narrative may be misleading.]

    ---

    ## 🏭 INDUSTRY SEGMENT IMPACT

    ### 🌐 Global Sectors
    | Sector | Sentiment | Key Driver Today |
    |--------|-----------|-----------------|
    | Technology | 🟢/🟡/🔴/⚫ | |
    | Financials | 🟢/🟡/🔴/⚫ | |
    | Energy | 🟢/🟡/🔴/⚫ | |
    | Healthcare | 🟢/🟡/🔴/⚫ | |
    | Consumer Discretionary | 🟢/🟡/🔴/⚫ | |
    | Consumer Staples | 🟢/🟡/🔴/⚫ | |
    | Industrials | 🟢/🟡/🔴/⚫ | |
    | Materials | 🟢/🟡/🔴/⚫ | |
    | Real Estate (REITs) | 🟢/🟡/🔴/⚫ | |
    | Utilities | 🟢/🟡/🔴/⚫ | |

    **Global sector note:** [rotation theme: growth vs value / defensive vs cyclical?]

    ---

    ### 🌏 Asia-Pacific Sectors
    | Sector | Sentiment | Region Driver |
    |--------|-----------|---------------|
    | Semiconductors / Tech Hardware | 🟢/🟡/🔴/⚫ | |
    | Chinese Consumer / E-commerce | 🟢/🟡/🔴/⚫ | |
    | Asian Financials / Banks | 🟢/🟡/🔴/⚫ | |
    | Asian Energy | 🟢/🟡/🔴/⚫ | |
    | Shipping / Logistics | 🟢/🟡/🔴/⚫ | |
    | Tourism / Aviation | 🟢/🟡/🔴/⚫ | |

    **APAC sector note:** [biggest cross-regional sector theme in Asia today]

    ---

    ### 🇹🇭 Thailand (SET) Industry Segments
    | SET Sector | Sentiment | Today's Driver |
    |------------|-----------|----------------|
    | Energy & Utilities (PTT, GULF, GPSC) | 🟢/🟡/🔴/⚫ | |
    | Banking & Finance (KBANK, SCB, BBL) | 🟢/🟡/🔴/⚫ | |
    | Property & Construction (CPN, LH, SIRI) | 🟢/🟡/🔴/⚫ | |
    | Tourism & Hospitality (MINT, ERW, AOT) | 🟢/🟡/🔴/⚫ | |
    | Retail & Consumer (CPALL, HMPRO, BJC) | 🟢/🟡/🔴/⚫ | |
    | Healthcare (BDMS, BCH, BH) | 🟢/🟡/🔴/⚫ | |
    | Industrials & Auto Parts (SAT, AH, STANLY) | 🟢/🟡/🔴/⚫ | |
    | Agro & Food (TU, CPF, GFPT) | 🟢/🟡/🔴/⚫ | |
    | Tech & Telecom (ADVANC, INTUCH, TRUE) | 🟢/🟡/🔴/⚫ | |
    | REITs & Infrastructure Funds | 🟢/🟡/🔴/⚫ | |

    **SET sector note:** [2 sentences — which SET sectors are most exposed to today's macro;
    expected rotation or pressure from oil price, THB move, and global risk mood.]

    ---

    *Brief generated: {TODAY} 09:00 Bangkok (UTC+7)*
    *Prices: Yahoo Finance live fetch at {RUN_TS} · News: Gemini knowledge*
    *Thai 10Y yield and Fed Funds Rate: from Gemini knowledge (approximate)*

    ---
    """).strip()

# ── Save .md ───────────────────────────────────────────────────────────────────
def save_markdown(brief: str) -> str:
    out_dir = pathlib.Path(BRIEF_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    filepath = out_dir / f"global-brief-{TODAY}.md"
    filepath.write_text(brief, encoding="utf-8")
    return str(filepath)

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_MAX = 4096

def _tg_send_chunk(text: str, parse_mode: str = "Markdown"):
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     text,
        "parse_mode":               parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def send_telegram(text: str):
    if DRY_RUN:
        print(f"[DRY-RUN] Telegram skipped ({len(text)} chars brief ready)")
        return
    chunks, buf = [], text
    while len(buf) > TELEGRAM_MAX:
        cut = buf.rfind("\n", 0, TELEGRAM_MAX)
        if cut == -1:
            cut = TELEGRAM_MAX
        chunks.append(buf[:cut])
        buf = buf[cut:].lstrip("\n")
    chunks.append(buf)

    for i, chunk in enumerate(chunks, 1):
        try:
            result = _tg_send_chunk(chunk)
            if result.get("ok"):
                print(f"[OK] Telegram chunk {i}/{len(chunks)} sent")
            else:
                print(f"[WARN] Telegram chunk {i} failed: {result}")
        except Exception as e:
            print(f"[ERROR] Telegram send failed (chunk {i}): {e}")

def send_fail_alert(error_msg: str):
    """Send a short failure alert to Telegram if the main script crashes."""
    if DRY_RUN or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    alert = f"⚠️ global-brief FAILED · {TODAY}\n\n{error_msg[:300]}"
    try:
        _tg_send_chunk(alert, parse_mode="")
        print("[ALERT] Fail notification sent to Telegram")
    except Exception as e:
        print(f"[ERROR] Could not send fail alert: {e}")

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    validate()
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    print(f"\n[{RUN_TS}] global_brief.py v2 starting ({mode})...")
    log("START", mode)

    try:
        # ── Step 1: fetch live prices ──────────────────────────────────────────
        print("\n[1/3] Fetching live prices from Yahoo Finance...")
        prices      = fetch_all_prices()
        price_block = format_price_block(prices)
        fetched_ok  = sum(1 for d in prices.values() if d.get("price"))
        print(f"[1/3] {fetched_ok}/{len(TICKERS)} tickers fetched successfully")
        log("FETCH", f"{fetched_ok}/{len(TICKERS)} tickers OK")

        # ── Step 2–3: generate brief via Gemini ───────────────────────────────
        print("\n[2/3] Calling Gemini to generate brief...")
        prompt = build_prompt(price_block)
        brief  = call_gemini(prompt)
        print(f"[2/3] Brief generated ({len(brief):,} chars)")
        log("GEMINI", f"{len(brief)} chars")

        # ── Step 4a: save .md (optional) ──────────────────────────────────────
        if SAVE_MARKDOWN:
            filepath = save_markdown(brief)
            print(f"[opt] Markdown saved → {filepath}")
            log("SAVE", filepath)

        # ── Step 4b: send to Telegram ─────────────────────────────────────────
        print("\n[3/3] Sending to Telegram...")
        send_telegram(brief)

        print(f"\n✅  Brief complete: {TODAY} ({mode})")
        log("DONE", f"OK · {mode}")

    except Exception as e:
        err = str(e)
        print(f"\n[FATAL] {err}")
        log("FAIL", err[:120])
        send_fail_alert(err)
        sys.exit(1)

if __name__ == "__main__":
    main()
