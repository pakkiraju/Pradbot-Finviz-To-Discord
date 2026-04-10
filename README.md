# PradBot-Finviz-To-Discord

Posts FinViz stock screener results to Discord channels via webhooks. Each scan gets its own channel and webhook, and results are formatted as clean embedded tables.

Two versions are included:

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

### 5. Configure .env (Discord bot)

The bot also reads from `.env`. Add your Discord bot token:

```
DISCORD_BOT_TOKEN=your_discord_bot_token_here
```

To get a token:

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application**.
2. Under **Bot**, click **Reset Token** and copy the token into `.env`.
3. Enable the **Message Content Intent** toggle (required for `!` prefix commands).
4. Under **OAuth2 > URL Generator**, select the `bot` scope and the permissions **Send Messages**, **Attach Files**, **Embed Links**, and **Read Message History**. Use the generated URL to invite the bot to your server.

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

The bot stays running and listens for commands in any channel it can see:

| Command | Description |
|---|---|
| `!chart AAPL` | Post a daily FinViz chart for the given ticker |
| `!chart MSFT w` | Weekly chart (`d` = daily, `w` = weekly, `m` = monthly) |

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
30 16 * * 1-5 cd /path/to/Discord\ Finviz\ Poster && python post_scans_free.py
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
