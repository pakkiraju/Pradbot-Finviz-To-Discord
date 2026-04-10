"""PradBot — Discord bot for Finviz charts and more.

Run with:  python bot.py
Requires DISCORD_BOT_TOKEN and FINVIZ_API_KEY in .env.
"""

import asyncio
import io
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

import discord
from discord.ext import commands

from finviz_chart import fetch_chart, validate_symbol, TIMEFRAMES
from finviz_news import fetch_news
from finviz_options import fetch_options
from finviz_quote import fetch_quote
from gex_compute import compute_gex

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
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


_TICKER_RE = re.compile(r"^!([A-Za-z][A-Za-z0-9.]{0,9})$")


@bot.event
async def on_ready():
    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    text = message.content.strip()
    m = _TICKER_RE.match(text)

    if m:
        token = m.group(1).lower()
        # Let registered commands through normally
        if token not in {cmd.name for cmd in bot.commands}:
            ticker = validate_symbol(m.group(1))
            if ticker:
                await _handle_ticker_quote(message, ticker)
                return

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# !chart command
# ---------------------------------------------------------------------------

@bot.command(name="chart")
async def chart_command(ctx: commands.Context, symbol: str | None = None, timeframe: str = "d"):
    """Post a FinViz chart for a ticker.

    Usage:
        !chart AAPL          — daily chart
        !chart MSFT w        — weekly chart
        !chart TSLA m        — monthly chart
    """
    if symbol is None:
        await ctx.reply("Usage: `!chart <SYMBOL> [d|w|m]`\nExample: `!chart AAPL w`")
        return

    ticker = validate_symbol(symbol)
    if ticker is None:
        await ctx.reply(f"`{symbol}` doesn't look like a valid ticker symbol.")
        return

    tf = timeframe.lower()
    if tf not in TIMEFRAMES:
        await ctx.reply(f"Unknown timeframe `{timeframe}`. Use `d` (daily), `w` (weekly), or `m` (monthly).")
        return

    tf_label = {"d": "Daily", "w": "Weekly", "m": "Monthly"}[tf]
    async with ctx.typing():
        data = await asyncio.to_thread(fetch_chart, ticker, tf)

    if data is None:
        await ctx.reply(f"Could not fetch chart for **{ticker}**. Check the logs for details.")
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

    await ctx.reply(embed=embed, file=file)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@chart_command.error
async def chart_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!chart <SYMBOL> [d|w|m]`\nExample: `!chart AAPL`")
    else:
        logger.exception("Unhandled error in !chart: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# !gex command
# ---------------------------------------------------------------------------

def _fmt_gex(value: float) -> str:
    """Format a GEX value into a readable string with K/M suffix."""
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:,.2f}M"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:,.1f}K"
    return f"{sign}{abs_v:,.0f}"


def _fmt_oi(value: float) -> str:
    """Format an OI count with comma separators."""
    return f"{int(value):,}"


def _build_gex_embed(summary) -> discord.Embed:
    """Build a Discord embed from a GexSummary."""
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

    # Top strikes table
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
        # Discord field value max is 1024 chars; truncate if needed
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


@bot.command(name="gex")
async def gex_command(ctx: commands.Context, symbol: str | None = None, expiry: str | None = None):
    """Post GEX / options analysis for a ticker.

    Usage:
        !gex AAPL               — nearest expiry
        !gex AAPL 2025-07-18    — specific expiry
    """
    if symbol is None:
        await ctx.reply("Usage: `!gex <SYMBOL> [YYYY-MM-DD]`\nExample: `!gex AAPL` or `!gex SPY 2025-07-18`")
        return

    ticker = validate_symbol(symbol)
    if ticker is None:
        await ctx.reply(f"`{symbol}` doesn't look like a valid ticker symbol.")
        return

    if expiry is not None:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", expiry):
            await ctx.reply(f"Invalid date format `{expiry}`. Use `YYYY-MM-DD` (e.g. `2025-07-18`).")
            return

    async with ctx.typing():
        rows = await asyncio.to_thread(fetch_options, ticker, expiry)

    if not rows:
        msg = f"No options data returned for **{ticker}**"
        if expiry:
            msg += f" (expiry {expiry})"
        msg += ". Check that the symbol has listed options and the expiry date is valid."
        await ctx.reply(msg)
        return

    used_expiry = expiry or rows[0].expiry or "unknown"
    summary = compute_gex(ticker, used_expiry, rows)

    if summary is None:
        await ctx.reply(f"Could not compute GEX for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    await ctx.reply(embed=embed)


@gex_command.error
async def gex_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!gex <SYMBOL> [YYYY-MM-DD]`\nExample: `!gex AAPL`")
    else:
        logger.exception("Unhandled error in !gex: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# !0dte command
# ---------------------------------------------------------------------------

@bot.command(name="0dte")
async def zero_dte_command(ctx: commands.Context, symbol: str | None = None):
    """Post 0DTE options analysis (OI walls, volume, P/C ratio) for a ticker.

    Usage:
        !0dte AAPL
    """
    if symbol is None:
        await ctx.reply("Usage: `!0dte <SYMBOL>`\nExample: `!0dte AAPL`")
        return

    ticker = validate_symbol(symbol)
    if ticker is None:
        await ctx.reply(f"`{symbol}` doesn't look like a valid ticker symbol.")
        return

    today = date.today().isoformat()

    async with ctx.typing():
        rows = await asyncio.to_thread(fetch_options, ticker, today)

    if not rows:
        await ctx.reply(
            f"No 0DTE options data for **{ticker}** today ({today}). "
            "The symbol may not have options expiring today."
        )
        return

    summary = compute_gex(ticker, today, rows)
    if summary is None:
        await ctx.reply(f"Could not compute 0DTE analysis for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    embed.title = f"{ticker} — 0DTE Analysis"
    embed.color = 0xE67E22
    await ctx.reply(embed=embed)


@zero_dte_command.error
async def zero_dte_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!0dte <SYMBOL>`\nExample: `!0dte AAPL`")
    else:
        logger.exception("Unhandled error in !0dte: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# !news command
# ---------------------------------------------------------------------------

@bot.command(name="news")
async def news_command(ctx: commands.Context, symbol: str | None = None):
    """Post the latest news articles for a ticker.

    Usage:
        !news AAPL
    """
    if symbol is None:
        await ctx.reply("Usage: `!news <SYMBOL>`\nExample: `!news AAPL`")
        return

    ticker = validate_symbol(symbol)
    if ticker is None:
        await ctx.reply(f"`{symbol}` doesn't look like a valid ticker symbol.")
        return

    async with ctx.typing():
        articles = await asyncio.to_thread(fetch_news, ticker, 5)

    if not articles:
        await ctx.reply(f"No news found for **{ticker}**.")
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
    await ctx.reply(embed=embed)


@news_command.error
async def news_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!news <SYMBOL>`\nExample: `!news AAPL`")
    else:
        logger.exception("Unhandled error in !news: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# !purge command
# ---------------------------------------------------------------------------

@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True, read_message_history=True)
async def purge_command(ctx: commands.Context, amount: str | None = None):
    """Delete messages from the current channel.

    Usage:
        !purge 10       — delete the last 10 messages
        !purge all       — delete ALL messages in the channel
    """
    if amount is None:
        await ctx.reply("Usage: `!purge <number|all>`\nExample: `!purge 10` or `!purge all`")
        return

    if amount.lower() == "all":
        confirm_msg = await ctx.reply(
            "Are you sure you want to delete **all** messages in this channel? "
            "Reply `yes` within 15 seconds to confirm."
        )
        try:
            reply = await bot.wait_for(
                "message",
                check=lambda m: (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and m.content.strip().lower() in ("yes", "y")
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            await confirm_msg.edit(content="Purge cancelled (timed out).")
            return

        deleted = await ctx.channel.purge(limit=None)
        info = await ctx.channel.send(f"Purged **{len(deleted)}** messages.")
        await info.delete(delay=5)
        logger.info("purge all: %d messages deleted in #%s by %s", len(deleted), ctx.channel.name, ctx.author)
        return

    try:
        count = int(amount)
    except ValueError:
        await ctx.reply("Please provide a number or `all`.\nExample: `!purge 25` or `!purge all`")
        return

    if count < 1:
        await ctx.reply("Amount must be at least 1.")
        return
    if count > 500:
        await ctx.reply("Maximum purge is 500 messages at a time. Use `!purge all` to clear the channel.")
        return

    # +1 to include the !purge command message itself
    deleted = await ctx.channel.purge(limit=count + 1)
    info = await ctx.channel.send(f"Purged **{len(deleted) - 1}** messages.")
    await info.delete(delay=5)
    logger.info("purge %d: %d messages deleted in #%s by %s", count, len(deleted) - 1, ctx.channel.name, ctx.author)


@purge_command.error
async def purge_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("You need the **Manage Messages** permission to use `!purge`.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.reply("I need **Manage Messages** and **Read Message History** permissions to purge.")
    else:
        logger.exception("Unhandled error in !purge: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# Dynamic !SYMBOL quote panel
# ---------------------------------------------------------------------------

def _fmt_vol(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:,.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:,.1f}K"
    return f"{v:,}"


async def _handle_ticker_quote(message: discord.Message, ticker: str):
    """Compose and send a quote panel: chart + OHLCV + latest news."""
    channel = message.channel

    async with channel.typing():
        bars, chart_data, articles = await asyncio.gather(
            asyncio.to_thread(fetch_quote, ticker, 5),
            asyncio.to_thread(fetch_chart, ticker, "d"),
            asyncio.to_thread(fetch_news, ticker, 3),
        )

    if not bars:
        await message.reply(f"No quote data found for **{ticker}**.")
        return

    latest = bars[0]
    prev_close = bars[1].close if len(bars) > 1 else None

    # Calculate change from previous close
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

    # Recent days table
    if len(bars) > 1:
        lines = ["Date       |  Close   |  Volume"]
        lines.append("-" * len(lines[0]))
        for b in bars:
            lines.append(f"{b.date} | ${b.close:>8,.2f} | {_fmt_vol(b.volume):>8}")
        table = "\n".join(lines)
        if len(table) <= 1000:
            embed.add_field(name="Recent Days", value=f"```\n{table}\n```", inline=False)

    # Latest news (3 headlines)
    if articles:
        news_lines = []
        for a in articles:
            src = f" — {a.source}" if a.source else ""
            news_lines.append(f"[{a.title}]({a.url}){src}")
        embed.add_field(name="Latest News", value="\n".join(news_lines), inline=False)

    # Attach chart image
    file = None
    if chart_data:
        filename = f"{ticker}_daily.png"
        file = discord.File(io.BytesIO(chart_data), filename=filename)
        embed.set_image(url=f"attachment://{filename}")

    embed.set_footer(text="Data from FinViz Elite")

    if file:
        await message.reply(embed=embed, file=file)
    else:
        await message.reply(embed=embed)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
