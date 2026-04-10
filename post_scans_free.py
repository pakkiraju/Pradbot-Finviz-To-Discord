"""Post Finviz scan results to Discord — Free (unofficial) version.

No FinViz API key required. Uses public finviz.com with browser-like requests.
Fully self-contained — zero dependency on the Market Metrics Dashboard.
Rate-limited more aggressively than Elite; expect slower runs.

Usage:
  python post_scans_free.py
  python post_scans_free.py --config webhooks.json
  python post_scans_free.py --dry-run
  python post_scans_free.py --verbose
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from scan_registry import SCAN_BY_ID, SCANS
from fetch_free import fetch_scan_with_screener
from discord_payload import build_embeds, post_to_webhook

logger = logging.getLogger("discord_poster.free")


def _load_webhooks(path: Path) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    hooks: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, str) and v.startswith("https://"):
            hooks[k] = v
        elif isinstance(v, dict) and v.get("enabled", True):
            url = v.get("url", "")
            if url.startswith("https://"):
                hooks[k] = url
    return hooks


def _free_screener_url(scan_def) -> str:
    """Convert Elite screener URL to free finviz.com for the embed link."""
    url = scan_def.screener_url
    if not url:
        return ""
    return url.replace("elite.finviz.com", "finviz.com")


def run(webhooks: dict[str, str], dry_run: bool = False):
    posted = 0
    errors = 0

    for scan in SCANS:
        if scan.scan_id not in webhooks:
            continue

        logger.info("Fetching: %s (free)", scan.title)
        try:
            rows, _ = fetch_scan_with_screener(scan)
        except Exception as e:
            logger.error("Fetch failed for %s: %s", scan.scan_id, e)
            errors += 1
            continue

        logger.info("  -> %d rows", len(rows))
        url = _free_screener_url(scan)
        embeds = build_embeds(scan.title, rows, screener_url=url)

        if dry_run:
            for em in embeds:
                logger.info("  [DRY-RUN] embed title=%s, desc_len=%d", em.get("title"), len(em.get("description", "")))
            posted += 1
            continue

        webhook_url = webhooks[scan.scan_id]
        ok = post_to_webhook(webhook_url, embeds)
        if ok:
            posted += 1
            logger.info("  Posted to Discord.")
        else:
            errors += 1
            logger.error("  Failed to post to Discord.")

        time.sleep(8)

    logger.info("Done. Posted: %d, Errors: %d", posted, errors)


def main():
    parser = argparse.ArgumentParser(description="Post Finviz scans to Discord (free/unofficial)")
    parser.add_argument("--config", default="webhooks.json", help="Path to webhooks JSON (default: webhooks.json)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but don't POST to Discord")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    if not config_path.exists():
        logger.error("Webhook config not found: %s", config_path)
        logger.error("Copy webhooks.example.json to webhooks.json and fill in your webhook URLs.")
        sys.exit(1)

    webhooks = _load_webhooks(config_path)
    if not webhooks:
        logger.error("No valid webhook URLs found in %s", config_path)
        sys.exit(1)

    unknown = set(webhooks) - set(SCAN_BY_ID)
    if unknown:
        logger.warning("Unknown scan IDs in config (will be ignored): %s", ", ".join(sorted(unknown)))

    logger.info("Loaded %d webhook(s): %s", len(webhooks), ", ".join(sorted(webhooks)))

    run(webhooks, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
