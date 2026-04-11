"""Post FinViz-style daily treemap (sector → industry → stocks) to Discord.

One FinViz Elite v=152 full-universe CSV export, nested squarify treemap PNG,
multipart webhook POST. Default universe is S&P 500. Requires FINVIZ_API_KEY in .env.

Usage:
  python post_heatmaps_elite.py
  python post_heatmaps_elite.py --config webhooks.json
  python post_heatmaps_elite.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from discord_payload import post_files_to_webhook
from fetch_v152_universe import FINVIZ_V152_SCREENER_URL
from heatmap_pipeline import build_daily_heatmaps

logger = logging.getLogger("heatmap_poster.elite")

EMBED_COLOR_HEATMAP = 0x06B6D4


def _load_heatmap_webhook(path: Path) -> str | None:
    """Return webhook URL for key ``heatmaps`` or env HEATMAP_WEBHOOK_URL."""
    import os

    env_url = os.environ.get("HEATMAP_WEBHOOK_URL", "").strip()
    if env_url.startswith("https://discord.com/api/webhooks/"):
        return env_url
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    v = raw.get("heatmaps")
    if isinstance(v, str) and v.startswith("https://discord.com/api/webhooks/"):
        return v
    if isinstance(v, dict) and v.get("enabled", True):
        u = v.get("url", "")
        if u.startswith("https://discord.com/api/webhooks/"):
            return u
    return None


def run(webhook_url: str | None, dry_run: bool = False) -> bool:
    logger.info("Building heatmaps from v=152 export (may take a few minutes)...")
    images, as_of = build_daily_heatmaps()
    if not images or as_of is None:
        logger.error("Heatmap build failed — check FINVIZ_API_KEY, timeout, and logs.")
        return False

    if dry_run:
        total = sum(len(b) for _, b in images)
        logger.info("[DRY-RUN] Would post %d file(s), %d total bytes", len(images), total)
        return True

    if not webhook_url:
        logger.error("No webhook URL for POST.")
        return False

    embed = {
        "title": "Daily performance treemap (S&P 500 default)",
        "description": (
            f"**Size** = market cap · **Color** = change % (FinViz session, delayed). "
            f"As-of **{as_of.isoformat()}**. "
            f"[Open v=152 screener]({FINVIZ_V152_SCREENER_URL})"
        ),
        "color": EMBED_COLOR_HEATMAP,
        "footer": {"text": "Pradly Portal • FinViz Elite • /heatmap in PradBot"},
    }
    ok = post_files_to_webhook(webhook_url, images, embed=embed)
    if ok:
        logger.info("Posted %d image(s) to Discord.", len(images))
    else:
        logger.error("Discord webhook post failed.")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Post FinViz v=152 daily heatmaps to Discord")
    parser.add_argument("--config", default="webhooks.json", help="JSON containing \"heatmaps\" webhook URL")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and build PNGs but do not POST")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path

    webhook_url = _load_heatmap_webhook(config_path)
    if not webhook_url and not args.dry_run:
        logger.error(
            "No heatmap webhook URL. Set HEATMAP_WEBHOOK_URL in the environment or add "
            '"heatmaps": "https://discord.com/api/webhooks/..." to %s',
            config_path,
        )
        sys.exit(1)

    ok = run(webhook_url, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
