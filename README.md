# PradBot-Finviz-To-Discord

A Discord bot and webhook toolkit that brings FinViz data into your Discord server.

## Features

### Discord Bot (`bot.py`)

A persistent Discord bot that responds to commands in real time:

| Command | What it does |
|---|---|
| `!chart AAPL` | Posts a **daily** FinViz candlestick chart for AAPL |
| `!chart MSFT w` | Posts a **weekly** chart (`d` = daily, `w` = weekly, `m` = monthly) |
| `!chart TSLA m` | Posts a **monthly** chart |

Charts are fetched from FinViz Elite as full-size PNG images and posted as embedded attachments with a link back to the stock's FinViz page. More commands will be added in future updates.

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

Follow these steps to create the bot that will respond to `!chart` commands.

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

#### 5c. Enable the Message Content Intent

The bot uses prefix commands (`!chart`) which require reading message content. This is a privileged intent that must be turned on manually:

1. Still on the **Bot** page, scroll down to **Privileged Gateway Intents**.
2. Toggle **Message Content Intent** to **ON**.
3. Click **Save Changes**.

Without this, the bot will connect but silently ignore all `!` commands.

#### 5d. Invite the bot to your server

1. In the left sidebar, click **OAuth2 > URL Generator**.
2. Under **Scopes**, check **`bot`**.
3. Under **Bot Permissions**, check:
   - **Send Messages**
   - **Attach Files**
   - **Embed Links**
   - **Read Message History**
4. Copy the generated URL at the bottom and open it in your browser.
5. Select your Discord server from the dropdown and click **Authorize**.

The bot will now appear in your server's member list (offline until you start `bot.py`).

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

The bot connects to Discord and stays running, listening for commands in any text channel it has access to. You should see `Logged in as PradBot#1234` in the console when it's ready.

#### Bot commands

| Command | Description |
|---|---|
| `!chart <SYMBOL>` | Post a daily FinViz candlestick chart for the given ticker |
| `!chart <SYMBOL> d` | Daily chart (same as above — `d` is the default) |
| `!chart <SYMBOL> w` | Weekly chart |
| `!chart <SYMBOL> m` | Monthly chart |

**Examples:**

```
!chart AAPL
!chart MSFT w
!chart TSLA m
!chart BRK.B
```

The bot replies with an embedded image and a link to the ticker's FinViz page. If the symbol is invalid or the chart can't be fetched, it replies with an error message instead.

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
  bot.py                 # Discord bot entry point (!chart and future commands)
  finviz_chart.py        # Fetches chart images from FinViz Elite
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

**Bot ignores `!chart` messages** — Make sure the **Message Content Intent** is enabled in the Discord Developer Portal for your bot application.

**"Could not fetch chart"** — The bot received a non-image response from FinViz. Verify your `FINVIZ_API_KEY` is valid and that your Elite subscription is active.

## License

This project uses data from [FinViz](https://finviz.com). Please review their [Terms of Service](https://finviz.com/terms.ashx) before use. The unofficial `finviz` Python library used by the free version is maintained at [mariostoev/finviz](https://github.com/mariostoev/finviz).
