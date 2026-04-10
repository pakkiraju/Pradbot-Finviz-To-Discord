"""PradBot — Discord bot for Finviz charts and more (slash commands).

Run with:  python bot.py
Requires DISCORD_BOT_TOKEN and FINVIZ_API_KEY in .env.
Slash commands sync globally when the bot starts (new registrations may take up to ~1 hour to appear everywhere).

/scans uses fetch_elite.fetch_scan (same pipeline as post_scans_elite.py).
"""

import asyncio
import csv
import io
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import discord
from discord import app_commands

from discord_payload import build_embeds
from fetch_elite import fetch_scan
from finviz_chart import fetch_chart, validate_symbol, TIMEFRAMES
from finviz_groups import fetch_groups, VALID_GROUPS, VIEW_PRESETS
from finviz_news import fetch_news
from finviz_options import fetch_options
from finviz_quote import fetch_quote
from gex_compute import compute_gex
from scan_registry import SCAN_BY_ID, SCANS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pradbot")

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------

def _load_env():
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

_load_env()

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not DISCORD_BOT_TOKEN:
    logger.critical(
        "DISCORD_BOT_TOKEN not set. Add it to .env in %s",
        Path(__file__).resolve().parent,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

_SCAN_ALL_VALUE = "all"

_SCAN_CHOICES = [
    app_commands.Choice(name="All scans (every preset)", value=_SCAN_ALL_VALUE),
    *[app_commands.Choice(name=s.title[:100], value=s.scan_id) for s in SCANS],
]

# Match post_scans_elite.py spacing between webhook posts
_SCANS_ALL_DELAY_SEC = 1.5


async def _followup_scan_embeds(interaction: discord.Interaction, scan_def) -> tuple[int, int]:
    """Fetch one scan and post embed(s). Returns (row_count, embed_count)."""
    rows = await asyncio.to_thread(fetch_scan, scan_def)
    embed_dicts = build_embeds(scan_def.title, rows, screener_url=scan_def.screener_url)
    embeds = [_webhook_embed_dict_to_discord(d) for d in embed_dicts]
    await interaction.followup.send(embed=embeds[0])
    for emb in embeds[1:]:
        await interaction.followup.send(embed=emb)
    return len(rows), len(embeds)


def _webhook_embed_dict_to_discord(em: dict) -> discord.Embed:
    """Convert webhook-style embed dict from discord_payload.build_embeds to discord.Embed."""
    color = em.get("color")
    if color is None:
        color = 0x06B6D4
    embed = discord.Embed(
        title=em.get("title"),
        description=em.get("description"),
        color=color,
    )
    if em.get("url"):
        embed.url = em["url"]
    ts = em.get("timestamp")
    if isinstance(ts, str) and ts:
        try:
            embed.timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            embed.timestamp = datetime.now(timezone.utc)
    foot = em.get("footer") or {}
    if foot.get("text"):
        embed.set_footer(text=foot["text"])
    return embed


@bot.event
async def on_ready():
    await tree.sync()
    logger.info("Synced slash commands globally (new commands may take up to ~1 hour to propagate)")
    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id)


# ---------------------------------------------------------------------------
# /chart
# ---------------------------------------------------------------------------

_TIMEFRAME_CHOICES = [
    app_commands.Choice(name="Daily", value="d"),
    app_commands.Choice(name="Weekly", value="w"),
    app_commands.Choice(name="Monthly", value="m"),
]


@tree.command(name="chart", description="Post a FinViz candlestick chart for a ticker")
@app_commands.describe(
    symbol="Ticker symbol (e.g. AAPL, MSFT, BRK.B)",
    timeframe="Chart timeframe",
)
@app_commands.choices(timeframe=_TIMEFRAME_CHOICES)
async def chart_command(interaction: discord.Interaction, symbol: str, timeframe: app_commands.Choice[str] | None = None):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    tf = timeframe.value if timeframe else "d"
    tf_label = {"d": "Daily", "w": "Weekly", "m": "Monthly"}[tf]

    await interaction.response.defer()
    data = await asyncio.to_thread(fetch_chart, ticker, tf)

    if data is None:
        await interaction.followup.send(f"Could not fetch chart for **{ticker}**. Check the logs for details.")
        return

    filename = f"{ticker}_{tf_label.lower()}.png"
    file = discord.File(io.BytesIO(data), filename=filename)

    embed = discord.Embed(
        title=f"{ticker} — {tf_label} Chart",
        color=0x2ECC71,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text="Data from FinViz")

    await interaction.followup.send(embed=embed, file=file)


# ---------------------------------------------------------------------------
# GEX helpers (shared by /gex and /zerodte)
# ---------------------------------------------------------------------------

def _fmt_gex(value: float) -> str:
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:,.2f}M"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:,.1f}K"
    return f"{sign}{abs_v:,.0f}"


def _fmt_oi(value: float) -> str:
    return f"{int(value):,}"


def _build_gex_embed(summary) -> discord.Embed:
    title = f"{summary.symbol} — GEX" if summary.has_gamma else f"{summary.symbol} — OI Analysis"
    embed = discord.Embed(
        title=title,
        color=0x3498DB,
        url=f"https://finviz.com/quote.ashx?t={summary.symbol}&ty=oc",
    )
    embed.add_field(name="Expiry", value=summary.expiry, inline=True)

    if summary.put_call_ratio is not None:
        embed.add_field(name="P/C Ratio", value=str(summary.put_call_ratio), inline=True)

    embed.add_field(
        name="Total OI",
        value=f"Calls: {_fmt_oi(summary.total_call_oi)}  |  Puts: {_fmt_oi(summary.total_put_oi)}",
        inline=False,
    )

    if summary.has_gamma:
        embed.add_field(name="Net GEX", value=_fmt_gex(summary.total_net_gex), inline=True)
        if summary.gamma_flip is not None:
            embed.add_field(name="Gamma Flip", value=f"${summary.gamma_flip:,.2f}", inline=True)
        if summary.call_wall is not None:
            embed.add_field(name="Call Wall", value=f"${summary.call_wall:,.2f}  ({_fmt_gex(summary.call_wall_value)})", inline=True)
        if summary.put_wall is not None:
            embed.add_field(name="Put Wall", value=f"${summary.put_wall:,.2f}  ({_fmt_gex(summary.put_wall_value)})", inline=True)
    else:
        if summary.call_wall is not None:
            embed.add_field(name="Call OI Wall", value=f"${summary.call_wall:,.2f}  ({_fmt_oi(summary.call_wall_value)} OI)", inline=True)
        if summary.put_wall is not None:
            embed.add_field(name="Put OI Wall", value=f"${summary.put_wall:,.2f}  ({_fmt_oi(summary.put_wall_value)} OI)", inline=True)

    if summary.top_strikes:
        header = "Strike     | Call OI  | Put OI   |"
        if summary.has_gamma:
            header = "Strike     | Net GEX    | Call OI  | Put OI   |"

        lines = [header, "-" * len(header)]
        for s in sorted(summary.top_strikes, key=lambda x: x.strike):
            if summary.has_gamma:
                lines.append(
                    f"${s.strike:<9,.2f} | {_fmt_gex(s.net_gex):>10} | {s.call_oi:>8,} | {s.put_oi:>8,} |"
                )
            else:
                lines.append(
                    f"${s.strike:<9,.2f} | {s.call_oi:>8,} | {s.put_oi:>8,} |"
                )

        table = "\n".join(lines)
        if len(table) > 1000:
            table = table[:997] + "..."
        label = "Top Strikes by GEX" if summary.has_gamma else "Top Strikes by OI"
        embed.add_field(name=label, value=f"```\n{table}\n```", inline=False)

    if not summary.has_gamma:
        embed.add_field(
            name="Note",
            value="Gamma data not available — showing open interest walls only.",
            inline=False,
        )

    embed.set_footer(text="Data from FinViz Elite")
    return embed


# ---------------------------------------------------------------------------
# /gex
# ---------------------------------------------------------------------------

@tree.command(name="gex", description="GEX / options analysis for a ticker (nearest future expiry)")
@app_commands.describe(
    symbol="Ticker symbol (e.g. AAPL, SPY)",
    expiry="Specific expiry date in YYYY-MM-DD format (optional)",
)
async def gex_command(interaction: discord.Interaction, symbol: str, expiry: str | None = None):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    if expiry is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", expiry):
        await interaction.response.send_message(
            f"Invalid date format `{expiry}`. Use `YYYY-MM-DD` (e.g. `2025-07-18`).",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    rows = await asyncio.to_thread(fetch_options, ticker, expiry)

    if not rows:
        msg = f"No options data returned for **{ticker}**"
        if expiry:
            msg += f" (expiry {expiry})"
        msg += ". Check that the symbol has listed options and the expiry date is valid."
        await interaction.followup.send(msg)
        return

    used_expiry = expiry or rows[0].expiry or "unknown"
    summary = compute_gex(ticker, used_expiry, rows)

    if summary is None:
        await interaction.followup.send(f"Could not compute GEX for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /zerodte
# ---------------------------------------------------------------------------

@tree.command(name="zerodte", description="0DTE options analysis (OI walls, volume, P/C ratio)")
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, SPY)")
async def zerodte_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    today = date.today().isoformat()

    await interaction.response.defer()
    rows = await asyncio.to_thread(fetch_options, ticker, today)

    if not rows:
        await interaction.followup.send(
            f"No 0DTE options data for **{ticker}** today ({today}). "
            "The symbol may not have options expiring today."
        )
        return

    summary = compute_gex(ticker, today, rows)
    if summary is None:
        await interaction.followup.send(f"Could not compute 0DTE analysis for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    embed.title = f"{ticker} — 0DTE Analysis"
    embed.color = 0xE67E22
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /news
# ---------------------------------------------------------------------------

@tree.command(name="news", description="Latest news articles for a ticker")
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, TSLA)")
async def news_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    await interaction.response.defer()
    articles = await asyncio.to_thread(fetch_news, ticker, 5)

    if not articles:
        await interaction.followup.send(f"No news found for **{ticker}**.")
        return

    embed = discord.Embed(
        title=f"{ticker} — Latest News",
        color=0x9B59B6,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )

    for article in articles:
        date_str = article.date.split(" ")[0] if " " in article.date else article.date
        source_tag = f"  *— {article.source}*" if article.source else ""
        embed.add_field(
            name=date_str,
            value=f"[{article.title}]({article.url}){source_tag}",
            inline=False,
        )

    embed.set_footer(text="Data from FinViz Elite")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /purge
# ---------------------------------------------------------------------------


class PurgeAllConfirmView(discord.ui.View):
    """Confirm delete-all using buttons. Text 'yes' replies fail without Message Content intent."""

    def __init__(self, invoker_id: int):
        super().__init__(timeout=15.0)
        self.invoker_id = invoker_id
        self.choice: bool | None = None  # True=purge, False=cancel, None=timeout

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran `/purge` can use these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes, delete all", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = False
        await interaction.response.edit_message(content="Purge cancelled.", view=None)
        self.stop()


@tree.command(name="purge", description="Delete messages from the current channel")
@app_commands.describe(amount='Number of messages to delete, or "all"')
@app_commands.default_permissions(manage_messages=True)
async def purge_command(interaction: discord.Interaction, amount: str):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("This command can only be used in text channels.", ephemeral=True)
        return

    bot_perms = channel.permissions_for(channel.guild.me)
    if not bot_perms.manage_messages or not bot_perms.read_message_history:
        await interaction.response.send_message(
            "I need **Manage Messages** and **Read Message History** permissions to purge.",
            ephemeral=True,
        )
        return

    if amount.lower() == "all":
        view = PurgeAllConfirmView(interaction.user.id)
        await interaction.response.send_message(
            "Are you sure you want to delete **all** messages in this channel?\n"
            "Click **Yes, delete all** within 15 seconds to confirm.",
            view=view,
        )
        await view.wait()
        if view.choice is None:
            try:
                await interaction.edit_original_response(
                    content="Purge cancelled (timed out).", view=None
                )
            except discord.HTTPException:
                pass
            return
        if view.choice is False:
            return

        deleted = await channel.purge(limit=None)
        info = await channel.send(f"Purged **{len(deleted)}** messages.")
        await info.delete(delay=5)
        logger.info("purge all: %d messages deleted in #%s by %s", len(deleted), channel.name, interaction.user)
        return

    try:
        count = int(amount)
    except ValueError:
        await interaction.response.send_message(
            'Please provide a number or `all`.\nExample: `/purge 25` or `/purge all`',
            ephemeral=True,
        )
        return

    if count < 1:
        await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
        return
    if count > 500:
        await interaction.response.send_message(
            "Maximum purge is 500 messages at a time. Use `/purge all` to clear the channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await channel.purge(limit=count)
    await interaction.followup.send(f"Purged **{len(deleted)}** messages.", ephemeral=True)
    logger.info("purge %d: %d messages deleted in #%s by %s", count, len(deleted), channel.name, interaction.user)


# ---------------------------------------------------------------------------
# /scans  (fetch_elite.fetch_scan + same embeds as webhook poster)
# ---------------------------------------------------------------------------


@tree.command(name="scans", description="Fetch FinViz Elite screener scan(s) (same pipeline as post_scans_elite)")
@app_commands.describe(scan="One preset, or All scans to run every preset in order")
@app_commands.choices(scan=_SCAN_CHOICES)
async def scans_command(interaction: discord.Interaction, scan: app_commands.Choice[str]):
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/scans`.",
            ephemeral=True,
        )
        return

    if scan.value == _SCAN_ALL_VALUE:
        await interaction.response.defer()
        n = len(SCANS)
        await interaction.followup.send(
            f"Running **{n}** FinViz Elite scans — results post below in order (several minutes; FinViz spacing between scans)."
        )
        ok = 0
        errors = 0
        for idx, scan_def in enumerate(SCANS):
            try:
                row_count, emb_count = await _followup_scan_embeds(interaction, scan_def)
                ok += 1
                logger.info(
                    "scans all [%s]: %d rows, %d embed(s) — %s",
                    scan_def.scan_id,
                    row_count,
                    emb_count,
                    interaction.user,
                )
            except Exception as e:
                logger.exception("fetch_scan failed for %s (all)", scan_def.scan_id)
                errors += 1
                await interaction.followup.send(
                    f"**{scan_def.title}** — failed: `{e}`"
                )
            if idx < n - 1:
                await asyncio.sleep(_SCANS_ALL_DELAY_SEC)
        await interaction.followup.send(
            f"**All scans finished.** Completed **{ok}**/{n}" + (f", **{errors}** error(s)." if errors else ".")
        )
        return

    scan_def = SCAN_BY_ID.get(scan.value)
    if scan_def is None:
        await interaction.response.send_message("Unknown scan — try again.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        row_count, emb_count = await _followup_scan_embeds(interaction, scan_def)
    except Exception as e:
        logger.exception("fetch_scan failed for %s", scan.value)
        await interaction.followup.send(f"Fetch failed: `{e}`")
        return

    logger.info(
        "scans %s: %d rows, %d embed(s) for %s",
        scan_def.scan_id,
        row_count,
        emb_count,
        interaction.user,
    )


# ---------------------------------------------------------------------------
# /groups
# ---------------------------------------------------------------------------

_GROUPS_INLINE_MAX = 20

_GROUP_CHOICES = [
    app_commands.Choice(name="Sector", value="sector"),
    app_commands.Choice(name="Industry", value="industry"),
    app_commands.Choice(name="Country", value="country"),
    app_commands.Choice(name="Market Cap", value="cap"),
]

_PRESET_CHOICES = [
    app_commands.Choice(name="Custom", value="custom"),
    app_commands.Choice(name="Overview", value="overview"),
    app_commands.Choice(name="Valuation", value="valuation"),
    app_commands.Choice(name="Performance", value="performance"),
]


def _fmt_mcap(raw: str) -> str:
    try:
        val = float(raw.replace(",", ""))
    except (ValueError, TypeError):
        return raw
    if val >= 1_000_000:
        return f"{val / 1_000_000:,.2f}T"
    if val >= 1_000:
        return f"{val / 1_000:,.1f}B"
    return f"{val:,.0f}M"


def _build_groups_table(columns: list[str], rows: list[dict[str, str]], limit: int | None = None) -> str:
    display_rows = rows[:limit] if limit else rows
    cols = [c for c in columns if c != "No."]

    widths = {c: len(c) for c in cols}
    formatted: list[dict[str, str]] = []
    for row in display_rows:
        fmt = {}
        for c in cols:
            val = row.get(c, "")
            if c == "Market Cap":
                val = _fmt_mcap(val)
            elif c in ("Volume", "Average Volume", "Stocks"):
                try:
                    val = f"{float(val.replace(',', '')):,.0f}"
                except (ValueError, TypeError):
                    pass
            fmt[c] = val
            widths[c] = max(widths[c], len(val))
        formatted.append(fmt)

    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines = [header, sep]
    for fmt in formatted:
        lines.append(" | ".join(fmt.get(c, "").ljust(widths[c]) for c in cols))

    return "\n".join(lines)


def _rebuild_csv(columns: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row.get(c, "") for c in columns)
    return buf.getvalue().encode("utf-8")


@tree.command(name="groups", description="Finviz group screener data (sector, industry, country, cap)")
@app_commands.describe(
    group="Group type",
    preset="View preset (columns)",
)
@app_commands.choices(group=_GROUP_CHOICES, preset=_PRESET_CHOICES)
async def groups_command(
    interaction: discord.Interaction,
    group: app_commands.Choice[str],
    preset: app_commands.Choice[str] | None = None,
):
    group_val = group.value
    view_name = preset.value if preset else "custom"
    view_code = VIEW_PRESETS[view_name]

    await interaction.response.defer()
    columns, rows = await asyncio.to_thread(fetch_groups, group_val, view_code)

    if not rows:
        await interaction.followup.send(f"No data returned for **{group_val}** ({view_name}). Check the logs for details.")
        return

    title = f"{group_val.title()} — {view_name.title()}"
    embed = discord.Embed(
        title=title,
        color=0xF39C12,
        url=f"https://elite.finviz.com/groups.ashx?g={group_val}&v={view_code}",
    )

    file = None
    if len(rows) <= _GROUPS_INLINE_MAX:
        table = _build_groups_table(columns, rows)
        if len(table) + 8 <= 4090:
            embed.description = f"```\n{table}\n```"
        else:
            short = _build_groups_table(columns, rows, limit=10)
            embed.description = f"```\n{short}\n```"
            csv_bytes = _rebuild_csv(columns, rows)
            fname = f"groups_{group_val}_{view_name}.csv"
            file = discord.File(io.BytesIO(csv_bytes), filename=fname)
            embed.add_field(name="Full data", value=f"See attached `{fname}`", inline=False)
    else:
        preview = _build_groups_table(columns, rows, limit=10)
        if len(preview) + 8 <= 4090:
            embed.description = f"```\n{preview}\n```"
        embed.add_field(
            name="Rows",
            value=f"{len(rows)} {group_val} groups — full data attached as CSV",
            inline=False,
        )
        csv_bytes = _rebuild_csv(columns, rows)
        fname = f"groups_{group_val}_{view_name}.csv"
        file = discord.File(io.BytesIO(csv_bytes), filename=fname)

    embed.set_footer(text="Data from FinViz Elite")

    if file:
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /quote (replaces the old !SYMBOL shortcut)
# ---------------------------------------------------------------------------

def _fmt_vol(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:,.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:,.1f}K"
    return f"{v:,}"


@tree.command(name="quote", description="Quick quote panel — chart, OHLCV, change, and latest news")
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, MSFT, BRK.B)")
async def quote_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    await interaction.response.defer()

    bars, chart_data, articles = await asyncio.gather(
        asyncio.to_thread(fetch_quote, ticker, 5),
        asyncio.to_thread(fetch_chart, ticker, "d"),
        asyncio.to_thread(fetch_news, ticker, 3),
    )

    if not bars:
        await interaction.followup.send(f"No quote data found for **{ticker}**.")
        return

    latest = bars[0]
    prev_close = bars[1].close if len(bars) > 1 else None

    if prev_close and prev_close != 0:
        chg = latest.close - prev_close
        chg_pct = (chg / prev_close) * 100
        sign = "+" if chg >= 0 else ""
        change_str = f"{sign}{chg:,.2f} ({sign}{chg_pct:.2f}%)"
    else:
        change_str = "N/A"

    embed = discord.Embed(
        title=f"{ticker} — ${latest.close:,.2f}  {change_str}",
        color=0x1ABC9C,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )
    embed.add_field(name="Open", value=f"${latest.open:,.2f}", inline=True)
    embed.add_field(name="High", value=f"${latest.high:,.2f}", inline=True)
    embed.add_field(name="Low", value=f"${latest.low:,.2f}", inline=True)
    embed.add_field(name="Volume", value=_fmt_vol(latest.volume), inline=True)
    embed.add_field(name="Date", value=latest.date, inline=True)

    if len(bars) > 1:
        lines = ["Date       |  Close   |  Volume"]
        lines.append("-" * len(lines[0]))
        for b in bars:
            lines.append(f"{b.date} | ${b.close:>8,.2f} | {_fmt_vol(b.volume):>8}")
        table = "\n".join(lines)
        if len(table) <= 1000:
            embed.add_field(name="Recent Days", value=f"```\n{table}\n```", inline=False)

    if articles:
        news_lines = []
        for a in articles:
            src = f" — {a.source}" if a.source else ""
            news_lines.append(f"[{a.title}]({a.url}){src}")
        embed.add_field(name="Latest News", value="\n".join(news_lines), inline=False)

    file = None
    if chart_data:
        filename = f"{ticker}_daily.png"
        file = discord.File(io.BytesIO(chart_data), filename=filename)
        embed.set_image(url=f"attachment://{filename}")

    embed.set_footer(text="Data from FinViz Elite")

    if file:
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I'm missing the required permissions for this command."
    else:
        logger.exception("Unhandled slash command error: %s", error)
        msg = "Something went wrong. Please try again later."

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
