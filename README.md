# PradBot-Finviz-To-Discord

This repository ships **three** standalone products that live in the same folder and share library code. Use any combination; they do not depend on each other at runtime.

| Product | What it is | How it talks to Discord |
|--------|------------|-------------------------|
| **PradBot** | Long-running Discord **application** (`bot.py`) | Bot user + slash commands in channels |
| **Scan webhook posters** | One-shot **CLI scripts** (`post_scans_elite.py` / `post_scans_free.py`) | Incoming webhooks per channel (URLs in `webhooks.json`) |
| **Daily heatmaps poster** | **`post_heatmaps_elite.py`** — v=152 full-universe CSV, nested sector/industry/stock treemap | One incoming webhook (`heatmaps` key or `HEATMAP_WEBHOOK_URL`), PNG attachments |

**Shared code (not a third product):** The Elite webhook script and PradBot’s **`/scans`** command both use the same pipeline: **`fetch_elite.fetch_scan`** / **`fetch_scan_with_screener`** (for the correct FinViz link, including Top Gainers/Losers), **`scan_registry`**, and **`discord_payload.build_embeds`**. PradBot does **not** execute `post_scans_elite.py`; it calls the same Python functions directly so tables match the Elite poster.

### Recent changes (at a glance)

- **`/earnings`** — FinViz Elite **v=152** export with **`earningsdate_today`** / **`earningsdate_thisweek`**; monospace tables (ticker, **time** with BMO/AMC-style text from FinViz, price, volume, avg vol, change %). **Weekly** view groups rows under **`— Apr 10 —`**-style day headers. Embed links to the matching screener; footer notes delayed quotes.
- **`/heatmap`** — Same **nested treemap** pipeline as **`post_heatmaps_elite.py`** (sector → industry → stocks; size = market cap, color = change %). **Universe** dropdown only: **S&P 500** (default), **NASDAQ 100**, **Dow**, **Russell 2000** (stocks and ETFs in that index column). Can take **1–3 minutes** (large CSV).
- **`/inplay`** — FinViz Elite screener: **news today or yesterday**, price **>$1**, avg vol **>1M**, current vol **>500K**, relative vol **>1.5**, sorted by **change %**. Embed lists symbol, price, change, volume, and a per-row **`[news](…)`** link (v=152 export for **News URL**; screener link uses **v=151**).
- **Slash sync** — **`GUILD_ID`** accepts **comma-separated** IDs for instant guild registration; **default when `GUILD_ID` is set** is **guild-only** (no duplicate slash lines). Set **`SLASH_SYNC_GLOBAL_ALSO=1`** for **guild + global** (other servers within ~1 hour; test guild may briefly show duplicates). **`SLASH_CLEAR_GLOBAL_FOR_DEDUPE`** clears stale globals when not using global sync (see **§5**).
- **`top_gainers` / `top_losers`** — Registered in **`scan_registry.py`** (`ta_topgainers` / `ta_toplosers`); **`fetch_scan_with_screener`** supplies **v=152** screener URLs for embeds. Webhook posters and **`/scans`** share the same pipeline; slash movers are top **10** with optional filters; batch presets cap at **50** rows (**Included Scans**).

---

## Product 1 — PradBot (`bot.py`)

Interactive **slash-command** bot: charts, options, news, quotes, channel purge, and on-demand screener tables.

### PradBot — command overview

| Command | What it does |
|---|---|
| `/chart AAPL` | FinViz candlestick chart (**default: Daily**) |
| `/chart MSFT` | **Timeframe** dropdown: **1m, 3m, 5m, 15m, 30m, 1h**, **Daily**, **Weekly**, **Monthly** |
| `/markets` | **Eight** futures/index charts (NQ, ES, YM, ER2, VX, NKD, EX, DY); same timeframe options as `/chart` (default **Daily**) |
| `/gex AAPL` | **GEX** (nearest future expiry or optional date) |
| `/zerodte AAPL` | **0DTE** OI-style analysis |
| `/news AAPL` | Latest **5** news links |
| `/quote AAPL` | Chart + OHLCV + change + recent days + headlines |
| `/scans` | **All scans** or **one** preset (FinViz Elite CSV + same embed style as Elite webhook poster) |
| `/top_gainers` | Today's **top 10 gaining** stocks by change %; optional price/volume filters |
| `/top_losers` | Today's **top 10 losing** stocks by change %; optional price/volume filters |
| `/earnings` | **Today** or **this week** earnings table (FinViz Elite); time, price, volumes, change % |
| `/inplay` | **In play** screen: news + liquidity + rel vol; table with clickable **news** links |
| `/heatmap` | **Nested treemap** by index universe (S&P 500 default); slow full-export pull |
| `/evsize` | **EV grade** + **position sizing** for a trade (entry, target, stop, win prob, daily risk budget) |
| `/purge` | Delete messages (count or **all**, buttons for **all**) |

Charts and FinViz data require a **FinViz Elite** subscription and **`FINVIZ_API_KEY`** in `.env`. **`/purge`** and **`/evsize`** only need Discord permissions (no FinViz key).

### PradBot — setup

#### 1) Clone repo and install dependencies (if you have not already)

```bash
git clone <your-repo-url>
cd "PradBot-Finviz-To-Discord"
pip install -r requirements.txt
```

#### 2) Environment file for PradBot

```bash
cp .env.example .env
```

Put these in `.env`:

- **`DISCORD_BOT_TOKEN`** — required. From the Developer Portal (**Bot** → token).
- **`FINVIZ_API_KEY`** — required for FinViz-backed commands (`/chart`, `/markets`, `/gex`, `/zerodte`, `/news`, `/quote`, `/scans`, `/top_gainers`, `/top_losers`, `/earnings`, `/inplay`, `/heatmap`, …). Not needed if you only use **`/purge`** and **`/evsize`** (the bot still needs the Discord token to start).
- **`GUILD_ID`** (optional) — **test server ID(s)** for **instant** slash updates; **by default** the bot registers **only on those guilds** (no global) so you do not see duplicate slash commands (see **§5**). Set **`SLASH_SYNC_GLOBAL_ALSO=1`** to also sync globally. Use **`SLASH_GUILD_ONLY=1`** to force guild-only if you use **`SLASH_SYNC_GLOBAL_ALSO`** but need to override. Leave **`GUILD_ID`** blank for **global‑only** registration.

#### 3) Discord application (you are the app owner)

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) while logged into the account that should **own** the application.
2. **Applications** → **New Application** → name it (e.g. `PradBot`) → **Create**.
3. Left sidebar → **Bot** → **Reset Token** (or **Copy**) → paste into `.env` as `DISCORD_BOT_TOKEN`.

> **Keep the token secret.** Reset it immediately if it leaks.

#### 4) Invite PradBot to your **server (guild)** with Administrator

PradBot is installed **per guild** using an OAuth2 invite URL (not the same thing as “webhook only”).

1. In the Developer Portal, left sidebar → **OAuth2** → **URL Generator**.
2. **Scopes:** enable **`bot`** and **`applications.commands`** (slash commands will not register without `applications.commands`).
3. **Bot permissions:** enable **`Administrator`**. That covers posting embeds and files, reading history, **`/purge`** (Manage Messages), and avoids missing toggles as features grow.
4. Copy the **generated URL**, open it in a browser, sign in as a Discord user who can **manage that server** (e.g. **Manage Server** or **Administrator** on the guild).
5. Choose the **server** → **Authorize**. Approve **bot** and **application commands** access when Discord asks.

After this, PradBot appears in the member list (offline until you run `bot.py`). You do **not** need **Message Content Intent** for these commands (slash + buttons).

#### 5) Slash command sync (global vs guild — instant updates)

**You do not need to re-invite the bot** when you add or change commands. The delay people hit is Discord’s **global** command propagation, not the invite.

| Mode | `.env` | Behavior |
|------|--------|----------|
| **Global only** | No `GUILD_ID` | Commands register for **all servers**; updates can take **up to ~1 hour** everywhere. |
| **Guild only (default when `GUILD_ID` set)** | `GUILD_ID=<test id(s)>` | **Guild** sync only: instant on listed server(s). **No** global — other servers **won’t** get commands unless you add **`SLASH_SYNC_GLOBAL_ALSO=1`**. |
| **Dual sync (test + global)** | `GUILD_ID=…` and `SLASH_SYNC_GLOBAL_ALSO=1` | Guild sync (instant) **and** global sync (other servers within ~**1 hour**). Your test guild **may show duplicate** slash entries (guild + global). |
| **Force guild-only** | `GUILD_ID=…` and `SLASH_GUILD_ONLY=1` | Overrides **`SLASH_SYNC_GLOBAL_ALSO`**: commands **only** on listed guilds. |

**How to get your server (guild) ID**

1. In Discord, open **User Settings** (gear) → **App Settings** → **Advanced**.
2. Turn **Developer Mode** **On**.
3. Close settings, **right‑click your server icon** (or server name in the list) → **Copy Server ID**.
4. Add one line to `.env`:

```
GUILD_ID=123456789012345678
```

For **multiple servers** with instant sync, use commas (spaces optional):

```
GUILD_ID=111111111111111111,222222222222222222
```

(Use real IDs; they are usually 17–19 digits.)

5. Restart `bot.py`. With **`GUILD_ID` only**, logs show **guild** sync (instant). Add **`SLASH_SYNC_GLOBAL_ALSO=1`** if you also want **global** sync logs and updates on other servers (~1 hour).

**Production without a test guild:** Remove `GUILD_ID` so only **global** sync runs.

**Other servers need commands too:** Set **`SLASH_SYNC_GLOBAL_ALSO=1`** alongside **`GUILD_ID`**, or remove **`GUILD_ID`** and rely on global-only sync.

**Re-invited the bot or joined a new server and see no commands?** With **global** or **dual** sync, wait for **global** propagation (~1 hour). With **guild-only** (`GUILD_ID` without **`SLASH_SYNC_GLOBAL_ALSO`**), only listed guilds have commands — add that server’s ID to **`GUILD_ID`** or enable global sync.

**Duplicate `/command` lines:** Usually from **dual** sync (guild + global) or **stale globals** after changing modes. Prefer **guild-only** (default with **`GUILD_ID`**) or run **once** with **`SLASH_CLEAR_GLOBAL_FOR_DEDUPE=1`** while **not** using **`SLASH_SYNC_GLOBAL_ALSO`** to wipe old global registrations. **Do not** use dedupe when **global** sync is enabled — it would remove commands from servers that only have globals.

#### 6) Run PradBot

```bash
python bot.py
```

You should see `Logged in as …` in the console.

### PradBot — slash reference (detail)

All commands use `/`. Dropdown parameters are shown in **bold**.

| Command | Description |
|---|---|
| `/chart <symbol> [timeframe]` | FinViz chart (**timeframe:** 1m–1h intraday, Daily, Weekly, Monthly) |
| `/markets [timeframe]` | Eight futures snapshot PNGs (see **What `/markets` shows**); needs `FINVIZ_API_KEY` |
| `/gex <symbol> [expiry]` | GEX / options (optional YYYY-MM-DD) |
| `/zerodte <symbol>` | 0DTE analysis |
| `/news <symbol>` | 5 articles with links |
| `/quote <symbol>` | Quote panel + chart + news |
| `/top_gainers [min_price] [min_volume]` | Top 10 gainers today; optional price/volume floor; needs `FINVIZ_API_KEY` |
| `/top_losers [min_price] [min_volume]` | Top 10 losers today; optional price/volume floor; needs `FINVIZ_API_KEY` |
| `/earnings [period]` | **Today** or **Weekly** earnings from FinViz Elite (**v=152**); monospace table: time (incl. BMO/AMC text), price, volume, avg vol, change %; needs `FINVIZ_API_KEY` |
| `/inplay` | **In play** — news today/yesterday, price >$1, avg vol >1M, vol >500K, rel vol >1.5; symbol, price, change, vol, **`[news](url)`** per row; needs `FINVIZ_API_KEY` |
| `/heatmap [universe]` | Nested performance treemap: **S&P 500** (default), **NASDAQ 100**, **Dow**, **Russell 2000**; needs `FINVIZ_API_KEY` |
| `/evsize <side> <entry> <target> <stop> <probability> <daily_risk>` | EV grade (A+ … D) + Kelly-based position sizing (ephemeral reply) |
| `/purge <amount>` | Purge count or **all** (buttons for **all**); needs Manage Messages |
| `/scans <scan>` | **All scans** or one preset (**Included Scans**); needs `FINVIZ_API_KEY` |

**Examples:**

```
/chart symbol:AAPL
/chart symbol:MSFT timeframe:Weekly
/chart symbol:SPY timeframe:5 minute
/chart symbol:TSLA timeframe:1 hour
/markets
/markets timeframe:5 minute
/gex symbol:AAPL
/zerodte symbol:SPY
/news symbol:TSLA
/quote symbol:MSFT
/purge amount:10
/purge amount:all
/evsize side:Long entry:185.00 target:195.00 stop:182.00 probability:55 daily_risk:1000
/evsize side:Short entry:420.00 target:400.00 stop:430.00 probability:60 daily_risk:2000
/top_gainers
/top_gainers min_price:5 min_volume:500000
/top_losers
/top_losers min_price:10
/inplay
/earnings period:Today
/earnings period:Weekly (this week)
/heatmap
/heatmap universe:NASDAQ 100
/scans scan:all
/scans scan:jeff_sun_canslim
```

**What `/chart` shows:** Downloads a **candlestick PNG** from **`elite.finviz.com/chart.ashx`** (`ty=c`, `ta=1`, `s=l`) with **`p=`** set from the timeframe: **1 / 3 / 5 / 15 / 30 minute** (`i1`–`i30`), **1 hour** (`h`), **Daily / Weekly / Monthly** (`d` / `w` / `m`). Default is **Daily**. Intraday charts need **FinViz Elite** (real-time / extended-hours behavior per FinViz). Requires `FINVIZ_API_KEY`.

**What `/markets` shows:** Fetches **eight** charts in order: **Nasdaq 100** (`NQ`), **S&P 500** (`ES`), **DJIA** (`YM`), **Russell 2000** (`ER2`), **VIX** (`VX`), **Nikkei 225** (`NKD`), **Euro Stoxx 50** (`EX`), **DAX** (`DY`). Uses the same **`p=`** timeframes as **`/chart`** (default **Daily**). Requests are spaced by **`FINVIZ_ELITE_DELAY_SEC`** between symbols. Posts one embed plus up to **eight** PNG attachments; symbols that fail are listed in the embed. Requires `FINVIZ_API_KEY`.

**What `/scans` does:** Uses **`fetch_elite.fetch_scan_with_screener`** (rows + FinViz URL for the embed link), **`discord_payload.build_embeds`** — the **same building blocks** as **`post_scans_elite.py`**, but posts into the channel via the bot. **All scans** sends many messages over several minutes.

**What `/gex` shows:** Net GEX, call/put walls, gamma flip, P/C ratio, top strikes (OI fallback if no gamma).

**What `/zerodte` shows:** Call/put OI walls, P/C, total OI, top strikes.

**What `/top_gainers` / `/top_losers` show:** A monospace table of the **top 10** stocks by daily change % (gainers sorted highest first, losers most negative first). Columns: ticker, price, change %, volume. Data is pulled from the Elite CSV export using the same column layout as other scans in this repo (`v=141`); the embed **link** opens the **v=152** screener view. Optional **`min_price`** and **`min_volume`** filter before slicing to 10. **`min_volume`** is in **shares** (e.g. `1000000` for one million); the CSV volume column is treated as **thousands** by default and converted to shares for filtering and display. Override with **`FINVIZ_MOVERS_VOLUME_CSV_UNIT=shares`** in `.env` if your export uses full shares. Requires `FINVIZ_API_KEY`.

**What `/earnings` shows:** Pulls the Elite **export.ashx** for **`earningsdate_today`** or **`earningsdate_thisweek`** (sorted by volume). Tables list **Ticker**, **Time** (clock times normalized; session hints like BMO/AMC stay in the FinViz text), **Price**, **Volume**, **AvgVol**, **Chg%**. Volumes are shown compactly (K/M/B) with the same thousands-vs-shares heuristic as movers. **Weekly** mode inserts **`— Apr 10 —`**-style section lines between days (month + day, no year). Title and embed **URL** match the period. Requires `FINVIZ_API_KEY`.

**What `/inplay` shows:** Applies the FinViz filters **news today|yesterday**, **sh_avgvol_o1000** (avg vol >1M), **sh_curvol_o500** (>500K current volume), **sh_price_o1** (>$1), **sh_relvol_o1.5** (rel vol >1.5), ordered by **change %** (descending). Fetches a **v=152** Elite export (full column set so **News URL** is present); the embed **title URL** opens the **v=151** screener. Up to **20** rows in a **Markdown table** (Symbol, Price, Change, Vol, News); the News column is **`[news](…)`** (not inside a code block, so links stay clickable). If **News URL** is missing, the link falls back to the symbol’s FinViz quote **news** tab. Requires `FINVIZ_API_KEY`.

**What `/heatmap` shows:** One or more **PNG** treemap images built from the same **v=152** full-universe export as **`post_heatmaps_elite.py`**, filtered to tickers whose **Index** column matches the chosen benchmark. Embed describes size/color, **as-of** date, and links the FinViz screener. First run can take **1–3 minutes**; increase **`FINVIZ_V152_EXPORT_TIMEOUT_SEC`** if the HTTP fetch times out. Requires `FINVIZ_API_KEY`.

**What `/evsize` shows:** Takes **long/short**, **entry/target/stop**, **win probability** (0–100), and **daily risk budget** ($). Computes reward (R), risk (L), R:L ratio, EV per share, EV/R, full Kelly fraction, and applies **¼ Kelly** (capped at 50% of daily budget) to suggest a dollar risk for the trade and approximate share count. Grades the setup **A+ through D** based on EV/R. Reply is **ephemeral** (only visible to you). No FinViz key needed. Educational tool, not financial advice.

**What `/news` / `/quote` show:** Headlines and links (news); combined panel with chart, OHLCV, recent days, and headlines (quote).

---

## Product 2 — Scan webhook posters (`post_scans_elite.py` / `post_scans_free.py`)

Separate **batch programs**: no bot token. You configure **Discord incoming webhook URLs** in JSON, run the script (or schedule it), and each configured scan posts to its webhook channel.

- **`post_scans_elite.py`** — FinViz Elite CSV exports; needs **`FINVIZ_API_KEY`** in `.env`.
- **`post_scans_free.py`** — Scrapes public FinViz HTML via [mariostoev/finviz](https://github.com/mariostoev/finviz); **no** API key; slower, rate-limit friendly.

These scripts **do not** start PradBot and **do not** require `DISCORD_BOT_TOKEN`.

**Top Gainers / Top Losers:** Same **`scan_id`** keys as **`/scans`**: `top_gainers`, `top_losers`. Elite uses the authenticated CSV export for movers (needs **`FINVIZ_API_KEY`**). Free uses the public screener page via **`fetch_free`** (no key; may differ slightly from Elite). Each run posts up to **50** tickers; sorting matches the **Included Scans** notes below.

### Webhook posters — setup

#### 1) Same clone / `pip install` as above (one venv is fine)

#### 2) Create webhooks in Discord

Server **Settings** → **Integrations** → **Webhooks** → **New Webhook** per target channel → copy each URL.

#### 3) `webhooks.json`

```bash
cp webhooks.example.json webhooks.json
```

Map **scan IDs** (see **Included Scans** below) to URLs:

```json
{
    "qulla_episodic": "https://discord.com/api/webhooks/1234567890/abcdef...",
    "jeff_sun_canslim": "https://discord.com/api/webhooks/1234567890/ghijkl...",
    "top_gainers": "https://discord.com/api/webhooks/1234567890/...",
    "top_losers": "https://discord.com/api/webhooks/1234567890/..."
}
```

Only listed IDs run. **`webhooks.json`** is gitignored.

#### 4) `.env` for Elite poster only

For **`post_scans_elite.py`**, set **`FINVIZ_API_KEY`** in `.env`. The free poster does not need it.

### Webhook posters — run

```bash
python post_scans_elite.py
# or
python post_scans_free.py
```

### Webhook posters — CLI flags

| Flag | Description |
|---|---|
| `--config PATH` | Webhooks JSON (default: `webhooks.json`) |
| `--dry-run` | Fetch and build embeds; do not POST |
| `--verbose` / `-v` | Debug logging |

```bash
python post_scans_free.py --dry-run --verbose
python post_scans_elite.py --config my_webhooks.json
```

### Webhook posters — scheduling

Scripts exit after one run. Use Task Scheduler, cron, etc.:

**Windows:** Task → run `python post_scans_elite.py` (or `post_scans_free.py`) with **Start in** = this folder.

**Linux/macOS:**

```bash
30 16 * * 1-5 cd /path/to/PradBot-Finviz-To-Discord && python post_scans_elite.py
```

### Free vs Elite (webhook posters)

| | Free | Elite |
|---|---|---|
| **API key** | No | Yes (Elite) |
| **Source** | HTML scrape | `elite.finviz.com` CSV |
| **Speed** | ~3–5 min all scans | ~30–60 sec typical |
| **Rate limits** | Aggressive delays + retries | Milder (`FINVIZ_ELITE_DELAY_SEC`) |
| **`tad_*` filters** | Stripped | Full |

---

## Product 3 — Daily heatmaps (`post_heatmaps_elite.py`)

**Elite only.** Downloads the same **full v=152** custom export (all columns, full symbol universe in one `export.ashx` request — large CSV, **~2–3 minute** HTTP timeout by default). Builds a **nested treemap** (sector → industry → stocks; size = market cap, color = change %) and posts **one** PNG per run via webhook multipart upload. The same pipeline powers PradBot **`/heatmap`**.

- **PradBot `/heatmap`:** choose **universe** only — **S&P 500** (default), **NASDAQ 100**, **Dow**, or **Russell 2000** (FinViz Index column; includes both stocks and ETFs in that benchmark). Options for market-cap tier, asset class (stocks vs ETFs), and sector/theme substring were **removed** to keep the command simple.
- **Requirements:** `FINVIZ_API_KEY`, `pip install -r requirements.txt` (adds **pandas**, **matplotlib**). Optional: `FINVIZ_V152_EXPORT_TIMEOUT_SEC` (default **180**).
- **Webhook:** Add **`"heatmaps": "https://discord.com/api/webhooks/..."`** to **`webhooks.json`**, or set **`HEATMAP_WEBHOOK_URL`** in `.env` (overrides JSON).
- **Run:** `python post_heatmaps_elite.py` — use **`--dry-run`** to fetch and build images without posting.
- **Scheduling:** Run once per day after the cash session (FinViz quotes are delayed ~15 minutes). One run = one heavy FinViz pull; avoid overlapping cron jobs.

Data is **not** real-time; FinViz ToS applies.

---

## Included Scans (shared IDs)

Used as keys in **`webhooks.json`** and as **`/scans`** choices (except the synthetic **All scans** option).

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
| `top_gainers` | Top Gainers |
| `top_losers` | Top Losers |

Most presets are sorted by daily change % (descending) and capped at **50** tickers. **Top Losers** is sorted with the most negative changes first. **Top Gainers / Top Losers** use FinViz’s mover presets (`ta_topgainers` / `ta_toplosers`).

---

## File structure (by product)

```
PradBot-Finviz-To-Discord/

  # PradBot
  bot.py                 # PradBot entry (slash commands)
  ev_position_sizing.py  # EV grade + Kelly position sizing (/evsize)

  # Webhook posters
  post_scans_elite.py
  post_scans_free.py
  post_heatmaps_elite.py   # v=152 heatmaps → Discord PNGs
  webhooks.example.json  # Copy → webhooks.json (webhook product only)

  # Shared by /scans and post_scans_elite (fetch_scan / fetch_scan_with_screener)
  scan_registry.py
  fetch_elite.py
  fetch_v152_universe.py
  heatmap_aggregate.py
  heatmap_pipeline.py
  heatmap_treemap.py
  heatmap_figures.py
  discord_payload.py

  # PradBot + Elite poster helpers
  finviz_chart.py
  finviz_markets.py
  finviz_earnings.py
  finviz_inplay.py
  finviz_options.py
  finviz_news.py
  finviz_quote.py
  gex_compute.py

  # Free webhook poster only
  fetch_free.py

  .env.example
  .gitignore
  requirements.txt
```

---

## Troubleshooting

**Webhook posters — "Webhook config not found"** — Create `webhooks.json` from `webhooks.example.json`.

**Webhook posters — "No valid webhook URLs"** — Use real `https://discord.com/api/webhooks/...` URLs.

**Webhook posters — 429 (free)** — Increase `FINVIZ_FREE_DELAY_SEC` if needed.

**PradBot — slash commands missing** — With **global** or **dual** sync, wait up to ~1 hour on servers that only get **global** registration. With **`GUILD_ID`** and no **`SLASH_SYNC_GLOBAL_ALSO`**, commands exist **only** on listed guilds. Invite must include **`applications.commands`**. If globals were cleared earlier, restart after fixing `.env`; avoid **`SLASH_CLEAR_GLOBAL_FOR_DEDUPE`** when using **`SLASH_SYNC_GLOBAL_ALSO`**.

**PradBot — `/scans` asks for `FINVIZ_API_KEY`** — Set it in `.env` next to `DISCORD_BOT_TOKEN`.

**Heatmaps — `post_heatmaps_elite.py` times out or returns HTML** — Increase `FINVIZ_V152_EXPORT_TIMEOUT_SEC` (e.g. **300**). Confirm Elite auth and stable network; the v=152 export is a single large CSV.

**Heatmaps — Discord upload fails** — Check webhook URL; Discord allows up to **10** files per message (this script sends **one** PNG per run).

**PradBot — chart / FinViz errors** — Confirm Elite subscription and `FINVIZ_API_KEY`.

**"No results for this scan"** — Normal on some days for some screens.

**Discord 400 (embed too large)** — Rare; scans cap at 50 rows; large tables split.

**"Gamma data not available"** — Try a further expiry on `/gex`.

---

## License

Data from [FinViz](https://finviz.com) — see their [Terms of Service](https://finviz.com/terms.ashx). Free scraping uses [mariostoev/finviz](https://github.com/mariostoev/finviz).
