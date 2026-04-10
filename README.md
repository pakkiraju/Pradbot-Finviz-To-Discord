# PradBot-Finviz-To-Discord

A Discord bot and webhook toolkit that brings FinViz data into your Discord server.

## Features

### Discord Bot (`bot.py`)

A persistent Discord bot that responds to **slash commands** in real time. All commands use Discord's native `/` command system with autocomplete and dropdown menus.

| Command | What it does |
|---|---|
| `/chart AAPL` | Posts a **daily** FinViz candlestick chart for AAPL |
| `/chart MSFT Weekly` | Posts a **weekly** chart (timeframe dropdown: Daily, Weekly, Monthly) |
| `/gex AAPL` | Posts **GEX analysis** for AAPL (nearest future expiry with gamma data) |
| `/gex SPY 2025-07-18` | GEX for a **specific expiration date** |
| `/zerodte AAPL` | **0DTE analysis** for today's expiry (OI walls, volume, P/C ratio) |
| `/news AAPL` | Latest **5 news articles** with clickable links |
| `/quote AAPL` | **Quick quote panel** — chart, OHLCV, change, recent days, and top 3 headlines |
| `/groups Sector` | **Sector** aggregate data (market cap, P/E, change, volume, etc.) |
| `/groups Industry Valuation` | **Industry** data with a specific view preset (dropdown menus) |

Charts are fetched from FinViz Elite as full-size PNG images. `/gex` pulls the full options chain CSV from FinViz Elite, computes dealer gamma exposure per strike, and shows call walls, put walls, gamma flip point, put/call ratio, and a top-strikes table. `/zerodte` targets today's expiry specifically for same-day OI-based analysis (gamma is zero at expiration, so OI walls, volume, and P/C ratio are shown instead). `/news` fetches the latest headlines from the FinViz Elite news export and posts them as clickable links with dates and sources. `/quote` posts a combined quote panel with the daily chart, OHLCV data, daily change, a 5-day history table, and the 3 latest news headlines — all in one embed.

### Webhook Posters

Scheduled scripts that post FinViz screener scan results to Discord channels via webhooks. Each scan gets its own channel and webhook, and results are formatted as clean embedded tables.

- **Elite** (`post_scans_elite.py`) — Uses a FinViz Elite API key for fast, reliable CSV exports. Recommended if you have a paid FinViz subscription.
- **Free** (`post_scans_free.py`) — Scrapes the public finviz.com screener pages using the [unofficial finviz Python API](https://github.com/mariostoev/finviz). No API key needed, but runs slower due to rate-limit-safe delays.

## Included Scans

| Scan ID | Name |
|---|---|
| `qulla_episodic` | Qullamaggie — Episodic Pivot |
| `qulla_breakouts` | Qullamaggie — Breakouts |
| `qulla_parabolic_short` | Qullamaggie — Parabolic Short |
| `jeff_sun_canslim` | Jeff Sun — CANSLIM |
| `jeff_sun_high_adr` | Jeff Sun — High ADR% Hottest Stock |
| `jeff_sun_extended_bases` | Jeff Sun — Extended Bases |
| `jeff_sun_1w20` | Jeff Sun — Strongest 1-Week +20% |
| `jeff_sun_4w30` | Jeff Sun — Strongest 1-Month +30% |
| `jeff_sun_4w50` | Jeff Sun — Strongest 1-Month +50% |
| `jeff_sun_13w50` | Jeff Sun — Strongest 3-Month +50% |
| `jeff_sun_26w100` | Jeff Sun — Strongest 6-Month +100% |
| `jeff_sun_ipo` | Jeff Sun — IPO |
| `jeff_sun_high_short_float` | Jeff Sun — High Short Float |
| `jeff_sun_liquid_etfs` | Jeff Sun — Liquid ETFs |
| `julian_komar_strongest` | Julian Komar — Strongest Stocks |
| `earnings_calendar_week` | Earnings Calendar — This Week |

Each scan is sorted by daily change % (descending) and capped at the top 50 results.

## Setup

### 1. Clone and install dependencies

```bash
git clone <your-repo-url>
cd "PradBot-Finviz-To-Discord"
pip install -r requirements.txt
```

### 2. Create Discord webhooks

For each scan you want to post, create a webhook in your Discord server:

1. Open your Discord server settings.
2. Go to **Integrations > Webhooks**.
3. Click **New Webhook**, pick the target channel, and copy the webhook URL.
4. Repeat for each scan/channel you want.

### 3. Configure webhooks.json

Copy the example file and fill in your webhook URLs:

```bash
cp webhooks.example.json webhooks.json
```

Open `webhooks.json` and replace the placeholder URLs with your real webhook URLs:

```json
{
    "qulla_episodic":       "https://discord.com/api/webhooks/1234567890/abcdef...",
    "jeff_sun_canslim":     "https://discord.com/api/webhooks/1234567890/ghijkl...",
    "earnings_calendar_week": "https://discord.com/api/webhooks/1234567890/mnopqr..."
}
```

You only need to include the scans you want. Any scan ID not present in `webhooks.json` will be skipped.

### 4. Configure .env (Elite version only)

If using the Elite version, copy the example and add your FinViz API key:

```bash
cp .env.example .env
```

Open `.env` and set your key:

```
FINVIZ_API_KEY=your_finviz_elite_api_key_here
```

The free version does not need a `.env` file.

### 5. Create a Discord bot application and get the token

Follow these steps to create the bot that will respond to `/chart`, `/gex`, `/zerodte`, `/news`, and other slash commands.

#### 5a. Create the application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** in the top-right corner.
3. Give it a name (e.g. `PradBot`) and click **Create**.

#### 5b. Get the bot token

1. In the left sidebar, click **Bot**.
2. Click **Reset Token** (or **Copy** if this is a fresh app) to reveal the bot token.
3. Copy the token and paste it into your `.env` file:

```
DISCORD_BOT_TOKEN=paste_your_token_here
```

> **Keep this token secret.** Anyone with the token can control your bot. If it leaks, click **Reset Token** immediately to generate a new one.

#### 5c. Invite the bot with full server access

Use one invite that grants the bot everything it needs (charts, embeds, `/purge`, channel tools, slash commands):

1. In the left sidebar, click **OAuth2 > URL Generator**.
2. Under **Scopes**, check **`bot`** and **`applications.commands`** (required for slash commands).
3. Under **Bot Permissions**, enable **Administrator**. That single toggle covers posting charts and embeds, reading history, managing messages for `/purge`, and all other bot features without micromanaging individual checkboxes.
4. Copy the generated URL at the bottom and open it in your browser.
5. Pick your server and click **Authorize**. Discord may ask you to confirm **bot** and **application commands** access for that server — approve both so `/` commands register and autocomplete.

The bot will appear in your member list (offline until you start `bot.py`).

> **Note:** The bot does not need the **Message Content Intent** (slash commands and `/purge all` button confirmations do not require reading normal message text).

#### 5d. Slash command sync

When `bot.py` starts, it registers slash commands **globally**. The first time you add or change commands, Discord can take **up to about an hour** for them to show up in every server; after that, updates are usually quick. If `/` commands already appear and autocomplete, you are set.

## Usage

### Free version (no API key)

```bash
python post_scans_free.py
```

### Elite version (requires FinViz Elite API key)

```bash
python post_scans_elite.py
```

### Discord bot

```bash
python bot.py
```

The bot connects to Discord and stays running, listening for slash commands in any text channel it has access to. You should see `Logged in as PradBot#1234` in the console when it's ready.

#### Bot commands

All commands use Discord's `/` slash command system. Parameters with dropdowns are shown in **bold**.

| Command | Description |
|---|---|
| `/chart <symbol> [timeframe]` | Post a FinViz candlestick chart (**timeframe** dropdown: Daily, Weekly, Monthly) |
| `/gex <symbol> [expiry]` | GEX / options analysis for nearest future expiry (or specific YYYY-MM-DD) |
| `/zerodte <symbol>` | 0DTE analysis for today's expiry (OI walls, volume, P/C ratio) |
| `/news <symbol>` | Latest 5 news articles with clickable links, dates, and sources |
| `/quote <symbol>` | Quick quote panel: chart + OHLCV + change + 5-day history + 3 latest headlines |
| `/purge <amount>` | Delete messages in the channel (number or **all**; **all** uses **Yes / Cancel** buttons to confirm; requires Manage Messages) |
| `/groups <group> [preset]` | Group screener data (**group** dropdown: Sector, Industry, Country, Market Cap; **preset** dropdown: Custom, Overview, Valuation, Performance) |

**Examples:**

```
/chart symbol:AAPL
/chart symbol:MSFT timeframe:Weekly
/chart symbol:TSLA timeframe:Monthly
/gex symbol:AAPL
/gex symbol:SPY expiry:2025-07-18
/zerodte symbol:AAPL
/zerodte symbol:SPY
/news symbol:AAPL
/news symbol:TSLA
/quote symbol:MSFT
/quote symbol:AAPL
/purge amount:10
/purge amount:all
/groups group:Sector
/groups group:Industry preset:Valuation
/groups group:Country preset:Performance
/groups group:Market Cap preset:Overview
```

The bot replies with an embedded image (charts) or an embed with analysis fields (options). `/quote` posts a combined panel with chart, price data, and news in one message. `/groups` posts aggregate metrics for sectors, industries, countries, or market cap tiers with dropdown selection. If the symbol is invalid or data can't be fetched, the bot replies with an ephemeral error message (only visible to you).

**What `/gex` shows:**
- **Net GEX** — total dealer gamma exposure across all strikes.
- **Call Wall** — strike with the largest positive gamma exposure (resistance).
- **Put Wall** — strike with the largest negative gamma exposure (support).
- **Gamma Flip** — strike where cumulative GEX crosses zero (trend inflection point).
- **P/C Ratio** — put-to-call open interest ratio.
- **Top Strikes** — table of the most significant strikes by GEX magnitude.

If gamma data is not available in the Finviz export, the bot falls back to **OI-based walls** and labels them accordingly.

**What `/zerodte` shows:**
- **Call OI Wall** — strike with the highest call open interest (resistance).
- **Put OI Wall** — strike with the highest put open interest (support).
- **P/C Ratio** — put-to-call open interest ratio.
- **Total OI** — total call and put open interest.
- **Top Strikes** — table of the most significant strikes by open interest.

Since gamma is always zero at expiration, 0DTE uses OI-based analysis rather than GEX.

**What `/news` shows:**
- The **5 most recent** news articles related to the ticker.
- Each article is a **clickable link** that opens directly in your browser.
- **Date** and **source** (e.g. Reuters, Bloomberg) are shown for each article.

**What `/quote` shows (quick quote panel):**
- **Daily chart** — full-size candlestick chart from FinViz Elite.
- **Last close** with daily change (dollar and percent) in the title.
- **OHLCV** — open, high, low, volume for the latest bar.
- **Recent Days** — 5-day OHLCV history table.
- **Latest News** — 3 most recent headlines as clickable links.

This combines `/chart`, price data, and `/news` into a single response.

**What `/groups` shows:**
- Aggregate metrics for groups of stocks organized by **sector**, **industry**, **country**, or **market cap** tier.
- Available view presets (selectable via dropdown) control which columns are returned:
  - `Overview` — stocks, market cap, dividend yield, P/E, forward P/E, PEG, debt ratios, analyst recom, change, volume.
  - `Valuation` — market cap, P/E, forward P/E, PEG, P/S, P/B, P/C, P/FCF, EPS growth, sales growth, change, volume.
  - `Performance` — weekly/monthly/quarterly/half-year/yearly/YTD performance, avg volume, relative volume, change, volume.
  - `Custom` (default) — market cap, P/E, dividend yield, avg volume, change, volume, stocks.
- Small groups (e.g. sector with ~11 rows) are displayed inline as a monospace table. Large groups (e.g. industry with ~140+ rows) include a preview table and attach the full data as a CSV file.

### Options (webhook scripts)

Both scripts accept the same flags:

| Flag | Description |
|---|---|
| `--config PATH` | Path to webhooks JSON file (default: `webhooks.json` in the script's folder) |
| `--dry-run` | Fetch data and build embeds, but don't actually post to Discord |
| `--verbose` / `-v` | Enable debug-level logging |

Examples:

```bash
# Dry run to verify everything works without posting
python post_scans_free.py --dry-run --verbose

# Use a custom webhooks file
python post_scans_elite.py --config my_webhooks.json

# Verbose output for troubleshooting
python post_scans_free.py -v
```

## Scheduling (optional)

The scripts run once and exit. To post daily, set up a scheduler:

**Windows Task Scheduler:**
1. Open Task Scheduler and create a new task.
2. Set the trigger to your preferred time (e.g. 4:30 PM ET on weekdays).
3. Set the action to run `python post_scans_free.py` (or `post_scans_elite.py`) with the working directory set to this folder.

**Linux/macOS cron:**

```bash
# Post at 4:30 PM ET every weekday (adjust timezone as needed)
30 16 * * 1-5 cd /path/to/PradBot-Finviz-To-Discord && python post_scans_free.py
```

## Free vs Elite: Differences

| | Free | Elite |
|---|---|---|
| **API key required** | No | Yes (FinViz Elite subscription) |
| **Data source** | Scrapes finviz.com HTML pages | Direct CSV export from elite.finviz.com |
| **Speed** | ~3-5 min for all 16 scans | ~30-60 sec for all 16 scans |
| **Rate limiting** | Aggressive (8s+ between scans, retries with backoff) | Mild (1.5s between scans) |
| **Elite-only filters** | `tad_*` custom filters are stripped; results may differ slightly | Full filter support |
| **Reliability** | May break if FinViz changes their HTML layout | Stable CSV format |

## File Structure

```
PradBot-Finviz-To-Discord/
  bot.py                 # Discord bot entry point (slash commands: /chart, /gex, /zerodte, /news, /quote, /groups, /purge)
  finviz_chart.py        # Fetches chart images from FinViz Elite
  finviz_groups.py       # Fetches group screener data from FinViz Elite groups export
  finviz_options.py      # Fetches options-chain CSV from FinViz Elite export
  finviz_news.py         # Fetches news articles from FinViz Elite news export
  finviz_quote.py        # Fetches OHLCV quote history from FinViz Elite quote export
  gex_compute.py         # Computes GEX metrics (walls, gamma flip, net GEX)
  post_scans_elite.py    # Webhook poster — Elite version
  post_scans_free.py     # Webhook poster — Free version
  scan_registry.py       # All scan definitions (IDs, titles, URLs)
  fetch_elite.py         # Fetches scan data via FinViz Elite CSV export
  fetch_free.py          # Fetches scan data via finviz Python library (HTML scraping)
  discord_payload.py     # Builds Discord embeds and posts to webhooks
  webhooks.example.json  # Template — copy to webhooks.json
  .env.example           # Template — copy to .env
  .gitignore             # Excludes webhooks.json, .env, __pycache__
  requirements.txt       # Python dependencies
```

## Troubleshooting

**"Webhook config not found"** — You need to create `webhooks.json`. Copy `webhooks.example.json` and fill in your URLs.

**"No valid webhook URLs found"** — Your `webhooks.json` has placeholder URLs. Replace them with real Discord webhook URLs starting with `https://discord.com/api/webhooks/...`.

**429 rate limit errors (free version)** — Free FinViz rate-limits aggressively. The script retries automatically with exponential backoff. You can increase the delay between requests by setting `FINVIZ_FREE_DELAY_SEC` in your environment (default is 5 seconds).

**"No results for this scan"** — Some scans may legitimately return zero results on a given day depending on market conditions. This is normal.

**Discord 400 Bad Request** — Usually means the embed payload is too large. Each scan is capped at 50 results and embeds are sent one at a time to stay within Discord's limits, so this should be rare.

**Slash commands not showing up** — Wait up to about an hour after the first sync, restart `bot.py`, and confirm the invite included **`applications.commands`** (OAuth2 URL Generator) and that the bot is still in the server. Re-authorize the invite URL if you add new scopes later.

**"Could not fetch chart"** — The bot received a non-image response from FinViz. Verify your `FINVIZ_API_KEY` is valid and that your Elite subscription is active.

**"No options data returned"** — The symbol may not have listed options, or the specific expiry date you entered doesn't exist for that ticker. Try without a date (`/gex AAPL`) to auto-select the nearest future expiry. The bot fetches all available expirations from the Finviz Elite export and picks the closest one after today.

**"No 0DTE options data"** — The symbol doesn't have options expiring today. Not every ticker has daily expirations — most only have weekly or monthly. Try `/gex SYMBOL` instead to see the nearest available expiry.

**"No news found"** — The symbol may not have any recent news articles in the FinViz database, or the API key may be invalid. Verify your `FINVIZ_API_KEY` is correct and the Elite subscription is active.

**"Gamma data not available"** — The Finviz options export didn't include gamma values for the selected expiry (common for 0DTE or very near-term expirations). The bot will show OI-based walls instead. If you want gamma-based GEX, try a further-out expiry (e.g. `/gex AAPL 2025-07-18`).

## License

This project uses data from [FinViz](https://finviz.com). Please review their [Terms of Service](https://finviz.com/terms.ashx) before use. The unofficial `finviz` Python library used by the free version is maintained at [mariostoev/finviz](https://github.com/mariostoev/finviz).
