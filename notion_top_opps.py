"""Create Notion database pages from /top_opps — properties match Discord embed order + chart PNGs on the page.

Uses NOTION_API_VERSION (default 2026-03-11) for database pages and file uploads (chart images).

Optional: python notion_top_opps.py --create-db
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_DATABASES_URL = "https://api.notion.com/v1/databases"
NOTION_DATA_SOURCES_URL = "https://api.notion.com/v1/data_sources"
NOTION_BLOCKS_URL = "https://api.notion.com/v1/blocks"
FILE_UPLOADS_URL = "https://api.notion.com/v1/file_uploads"

# Empty visible title — Notion still requires a title property; we use zero-width space.
_EMPTY_TITLE = "\u200b"

# Column order: matches /top_opps Discord flow — study line (Symbol → … Notes), then snapshot (Open … Recent Days).
_DEF: dict[str, str] = {
    "title": "Name",
    "symbol": "Symbol",
    "date": "Date",
    "entry": "Entry",
    "stop": "Stop",
    "exit": "Exit",
    "exit_note": "Exit note",
    "rr": "R:R",
    "ev": "EV",
    "side": "Side",
    "notes": "Notes",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "avg_vol": "Avg Vol",
    "rel_vol": "Rel Vol",
    "change": "Change",
    "gap": "Gap",
    "pe": "P/E",
    "share_float": "Share Float",
    "short_float": "Short Float",
    "news": "News",
    "recent_days": "Recent Days",
}

# Keys in DB column creation / PATCH order (excluding title — handled separately).
_ORDER = [
    "symbol",
    "date",
    "entry",
    "stop",
    "exit",
    "exit_note",
    "rr",
    "ev",
    "side",
    "notes",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "avg_vol",
    "rel_vol",
    "change",
    "gap",
    "pe",
    "share_float",
    "short_float",
    "news",
    "recent_days",
]


def _schema_fragment(internal: str) -> dict[str, Any]:
    if internal == "date":
        return {"date": {}}
    if internal in ("entry", "stop", "exit", "rr", "ev", "open", "high", "low", "close"):
        return {"number": {}}
    return {"rich_text": {}}


_PATCH_SCHEMA: dict[str, dict[str, Any]] = {k: _schema_fragment(k) for k in _ORDER}

# Bump when columns / order change so cached name maps are not reused across incompatible versions.
_SCHEMA_CACHE_TAG = "2026-04-14-v4-ds"
_schema_cache: dict[str, dict[str, str]] = {}


def _prop(key: str) -> str:
    env_key = f"NOTION_PROP_{key.upper()}"
    return (os.environ.get(env_key) or _DEF[key]).strip()


def _notion_version() -> str:
    return (os.environ.get("NOTION_API_VERSION") or "2026-03-11").strip()


def notion_top_opps_ready() -> bool:
    key = (os.environ.get("NOTION_API_KEY") or "").strip()
    db = (os.environ.get("NOTION_TOP_OPPS_DATABASE_ID") or "").strip()
    return bool(key and db)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _notion_version(),
        "Content-Type": "application/json",
    }


def _headers_auth_only(token: str) -> dict[str, str]:
    """Multipart file send must not force application/json (boundary is set by requests)."""
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _notion_version(),
    }


def _format_db_id(db_hex: str) -> str:
    db_id = re.sub(r"[^0-9a-fA-F]", "", db_hex.strip())
    if len(db_id) != 32:
        raise ValueError("expected 32 hex chars")
    return f"{db_id[:8]}-{db_id[8:12]}-{db_id[12:16]}-{db_id[16:20]}-{db_id[20:]}"


def _iter_block_children(block_id: str, token: str):
    """Paginate GET /blocks/{id}/children."""
    bid = block_id if re.match(r"^[0-9a-fA-F-]{36}$", block_id) else _format_db_id(block_id)
    cursor: str | None = None
    while True:
        url = f"{NOTION_BLOCKS_URL}/{bid}/children"
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(url, headers=_headers(token), params=params, timeout=60)
        if r.status_code >= 400:
            return
        data = r.json()
        for b in data.get("results") or []:
            yield b
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")


def _find_first_child_database_id(token: str, root_block_id: str) -> str | None:
    """BFS block tree for type child_database; return its id (usable as database id)."""
    queue: list[str] = [root_block_id]
    seen: set[str] = set()
    while queue:
        bid = queue.pop(0)
        if bid in seen:
            continue
        seen.add(bid)
        for b in _iter_block_children(bid, token):
            if b.get("type") == "child_database":
                return b.get("id")
            if b.get("has_children") and b.get("id"):
                queue.append(b["id"])
    return None


def resolve_database_id_if_page(token: str, fmt_id: str) -> tuple[bool, str, str]:
    """If NOTION_TOP_OPPS_DATABASE_ID is a page URL/id, resolve to an embedded database id.

    Returns (ok, error_message, database_fmt_id). On success error_message is "".
    """
    try:
        r = requests.get(f"{NOTION_DATABASES_URL}/{fmt_id}", headers=_headers(token), timeout=60)
    except requests.RequestException as e:
        return False, f"Notion GET database failed: {e}", fmt_id

    if r.status_code == 200:
        return True, "", fmt_id

    msg_low = ""
    try:
        err = r.json()
        msg_low = (err.get("message") or "").lower()
    except Exception:
        pass

    looks_like_page = (
        r.status_code == 400
        and ("page" in msg_low and "database" in msg_low)
    ) or ("not a database" in msg_low)

    if not looks_like_page and r.status_code != 404:
        return False, f"Notion GET database {r.status_code}: {r.text[:800]}", fmt_id

    try:
        pr = requests.get(f"{NOTION_PAGES_URL}/{fmt_id}", headers=_headers(token), timeout=60)
    except requests.RequestException as e:
        return False, f"Notion GET page failed: {e}", fmt_id

    if pr.status_code != 200:
        return (
            False,
            f"Notion ID is neither a database nor a page ({r.status_code} / {pr.status_code}). "
            "Use **Share → Copy link** on a **full-page database**, or a page that contains an embedded database.",
            fmt_id,
        )

    child_db = _find_first_child_database_id(token, fmt_id)
    if not child_db:
        return (
            False,
            "That Notion URL/id is a **page**, not a **database**, and no **embedded database** was found on it. "
            "Open your table as a **full page** (or create `/top_opps` DB via `python notion_top_opps.py --create-db`) "
            "and set NOTION_TOP_OPPS_DATABASE_ID to the **database** id from the browser URL.",
            fmt_id,
        )

    plain = re.sub(r"[^0-9a-fA-F]", "", child_db)
    if len(plain) != 32:
        return False, "Resolved child_database id was invalid.", fmt_id

    resolved = _format_db_id(plain)
    logger.info("Notion: resolved page/container id to database id %s", resolved)
    return True, "", resolved


def _find_title_property_name(properties: dict[str, Any]) -> str | None:
    for pname, pmeta in properties.items():
        if isinstance(pmeta, dict) and pmeta.get("type") == "title":
            return pname
    return None


def _data_source_ids_from_database(data: dict[str, Any]) -> list[str]:
    """Notion 2025+ databases expose schema on data source(s), not always on the database object."""
    out: list[str] = []
    raw = data.get("data_sources")
    if not raw:
        return out
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and len(item) >= 32:
                out.append(item)
            elif isinstance(item, dict):
                iid = item.get("id")
                if iid:
                    out.append(str(iid))
    return out


def _normalize_uuid(s: str) -> str:
    plain = re.sub(r"[^0-9a-fA-F]", "", s)
    if len(plain) != 32:
        return s
    return _format_db_id(plain)


def _fetch_data_source(token: str, data_source_id: str) -> dict[str, Any] | None:
    ds = _normalize_uuid(data_source_id)
    try:
        r = requests.get(f"{NOTION_DATA_SOURCES_URL}/{ds}", headers=_headers(token), timeout=60)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _load_schema_properties_and_title(
    token: str, db_json: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (properties dict, title column name, data_source_id or None if schema is on database).

    New Notion databases often return empty `database.properties` and put columns on a data source.
    """
    props = db_json.get("properties") or {}
    title = _find_title_property_name(props)
    if title and props:
        return props, title, None

    for ds_id in _data_source_ids_from_database(db_json):
        dsj = _fetch_data_source(token, ds_id)
        if not dsj:
            continue
        p2 = dsj.get("properties") or {}
        t2 = _find_title_property_name(p2)
        if t2:
            logger.info("Notion: using data source %s for schema (inline / multi-source database).", ds_id[:8])
            return p2, t2, _normalize_uuid(ds_id)

    if title:
        return props, title, None
    return None, None, None


def _schema_cache_key(fmt_id: str, data_source_id: str | None) -> str:
    ds = data_source_id or "database"
    return f"{fmt_id}:{ds}:{_SCHEMA_CACHE_TAG}"


def ensure_top_opps_schema(
    fmt_id: str, token: str
) -> tuple[bool, str, dict[str, str] | None, str | None, str | None]:
    """GET database (+ data source if needed), PATCH missing properties.

    Returns (ok, err, name_map, database_id, data_source_id_or_none).
    When data_source_id is set, schema and PATCH target the data source (Notion 2025+ inline DBs).
    """
    ok_resolve, err_resolve, fmt_id = resolve_database_id_if_page(token, fmt_id)
    if not ok_resolve:
        return False, err_resolve, None, None, None

    auto = (os.environ.get("NOTION_TOP_OPPS_AUTO_SCHEMA", "1") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "",
    )

    try:
        r = requests.get(f"{NOTION_DATABASES_URL}/{fmt_id}", headers=_headers(token), timeout=60)
    except requests.RequestException as e:
        return False, f"Notion GET database failed: {e}", None, None, None

    if r.status_code >= 400:
        return False, f"Notion GET database {r.status_code}: {r.text[:800]}", None, None, None

    data = r.json()
    props_existing, title_name, data_source_id = _load_schema_properties_and_title(token, data)
    if not props_existing or not title_name:
        return (
            False,
            "Could not find a title column on this database (or its data source). "
            "Share the database with the integration, or run `python notion_top_opps.py --create-db`.",
            None,
            None,
            None,
        )

    ck = _schema_cache_key(fmt_id, data_source_id)
    if ck in _schema_cache:
        return True, "", _schema_cache[ck], fmt_id, data_source_id

    want_names: dict[str, str] = {"title": title_name}
    for key in _DEF:
        if key == "title":
            continue
        want_names[key] = _prop(key)

    if not auto:
        missing = [want_names[k] for k in _ORDER if want_names[k] not in props_existing]
        if missing:
            return (
                False,
                f"Notion DB is missing columns: {', '.join(missing)}. Set NOTION_TOP_OPPS_AUTO_SCHEMA=1.",
                None,
                None,
                None,
            )
        _schema_cache[ck] = want_names
        return True, "", want_names, fmt_id, data_source_id

    to_add: dict[str, Any] = {}
    for internal in _ORDER:
        col = want_names[internal]
        if col not in props_existing:
            to_add[col] = _PATCH_SCHEMA[internal]

    if to_add:
        patch_url = (
            f"{NOTION_DATA_SOURCES_URL}/{data_source_id}"
            if data_source_id
            else f"{NOTION_DATABASES_URL}/{fmt_id}"
        )
        try:
            pr = requests.patch(
                patch_url,
                headers=_headers(token),
                json={"properties": to_add},
                timeout=60,
            )
        except requests.RequestException as e:
            return False, f"Notion PATCH schema failed: {e}", None, None, None

        if pr.status_code >= 400:
            logger.warning("Notion PATCH schema %s: %s", pr.status_code, pr.text[:2000])
            try:
                err = pr.json()
                msg = err.get("message", pr.text[:500])
            except Exception:
                msg = pr.text[:500]
            return False, f"Could not add columns to Notion: {msg}", None, None, None

        logger.info("Notion: added %d column(s) via %s", len(to_add), "data_source" if data_source_id else "database")

    _schema_cache[ck] = want_names
    return True, "", want_names, fmt_id, data_source_id


def build_create_database_properties() -> dict[str, Any]:
    """Ordered schema for POST /v1/databases (Name title + fields in Discord order)."""
    out: dict[str, Any] = {_prop("title"): {"title": {}}}
    for internal in _ORDER:
        out[_prop(internal)] = _PATCH_SCHEMA[internal]
    return out


def create_top_opps_database(parent_page_id: str, token: str) -> tuple[bool, str]:
    try:
        pid = _format_db_id(parent_page_id)
    except ValueError:
        return False, "Invalid NOTION_TOP_OPPS_PARENT_PAGE_ID (need 32 hex chars)."

    body = {
        "parent": {"type": "page_id", "page_id": pid},
        "title": [
            {
                "type": "text",
                "text": {"content": "PradBot /top_opps"},
            }
        ],
        "properties": build_create_database_properties(),
    }

    try:
        r = requests.post(NOTION_DATABASES_URL, headers=_headers(token), json=body, timeout=60)
    except requests.RequestException as e:
        return False, str(e)

    if r.status_code >= 400:
        logger.warning("Notion POST database %s: %s", r.status_code, r.text[:2000])
        try:
            err = r.json()
            msg = err.get("message", r.text[:500])
        except Exception:
            msg = r.text[:500]
        return False, msg

    try:
        data = r.json()
        did = data.get("id", "")
        if did:
            plain = re.sub(r"[^0-9a-fA-F]", "", did)
            return True, plain
        return False, "No database id in response"
    except Exception as e:
        return False, str(e)


def _rich_text_segments(text: str | None) -> list[dict[str, Any]]:
    if text is None:
        return []
    s = str(text)
    if not s.strip():
        return []
    out: list[dict[str, Any]] = []
    chunk = 1900
    for i in range(0, len(s), chunk):
        part = s[i : i + chunk]
        out.append({"type": "text", "text": {"content": part}})
    return out


def _rich_prop(val: str | None) -> dict[str, Any] | None:
    segs = _rich_text_segments(val)
    if not segs:
        return None
    return {"rich_text": segs}


def _num_prop(v: float | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {"number": float(v)}


@dataclass
class TopOppsNotionPayload:
    symbol: str
    date_iso: str
    entry: float | None
    stop: float | None
    exit_target: float | None
    exit_note: str | None
    rr: float | None
    ev: float | None
    side: str | None
    notes: str | None
    open_: float
    high: float
    low: float
    close: float
    volume: str
    avg_vol: str
    rel_vol: str
    change_str: str
    gap: str
    pe: str
    share_float: str
    short_float: str
    news: str
    recent_days: str | None


def _fmt_vol_local(v: int) -> str:
    try:
        from finviz_earnings import _fmt_shares_compact

        return _fmt_shares_compact(float(v))
    except Exception:
        return str(v)


def _recent_days_plain(bars) -> str | None:
    if len(bars) <= 1:
        return None
    lines = ["Date       |  Close   |  Volume"]
    lines.append("-" * len(lines[0]))
    for b in bars:
        lines.append(f"{b.date} | ${b.close:>8,.2f} | {_fmt_vol_local(b.volume):>8}")
    table = "\n".join(lines)
    if len(table) > 2000:
        return table[:1997] + "..."
    return table


def build_top_opps_payload(
    *,
    ticker: str,
    study_mode: bool,
    entry: float | None,
    stop: float | None,
    study_exit: float | None,
    exit_default_kind: str | None,
    study_metrics: dict[str, Any] | None,
    notes: str | None,
    latest,
    snapshot,
    change_str: str,
    gap_str: str,
    bars,
    articles,
) -> TopOppsNotionPayload:
    from datetime import datetime, timezone

    rr: float | None = None
    ev: float | None = None
    side: str | None = None
    if study_metrics:
        rr = float(study_metrics["rr"]) if study_metrics.get("rr") is not None else None
        ev = float(study_metrics["ev50"]) if study_metrics.get("ev50") is not None else None
        s = str(study_metrics.get("side", "")).lower()
        side = {"long": "Long", "short": "Short", "mixed": "Mixed"}.get(s, s.title() if s else None)

    exit_note: str | None = None
    if study_mode:
        if exit_default_kind == "last_trade":
            exit_note = "Exit default: last traded"
        elif exit_default_kind == "rth_close":
            exit_note = "Exit default: regular session close"

    news = "—"
    if articles:
        a = articles[0]
        news = f"{a.title}\n{a.url}"
        if getattr(a, "source", None):
            news += f"\n{a.source}"

    recent = _recent_days_plain(bars)
    date_iso = datetime.now(timezone.utc).date().isoformat()

    return TopOppsNotionPayload(
        symbol=ticker,
        date_iso=date_iso,
        entry=float(entry) if entry is not None else None,
        stop=float(stop) if stop is not None else None,
        exit_target=float(study_exit) if study_exit is not None and study_mode else None,
        exit_note=exit_note,
        rr=rr,
        ev=ev,
        side=side,
        notes=(notes.strip() if notes and str(notes).strip() else None),
        open_=float(latest.open),
        high=float(latest.high),
        low=float(latest.low),
        close=float(latest.close),
        volume=str(_fmt_vol_local(latest.volume)),
        avg_vol=snapshot.avg_vol_display if snapshot else "—",
        rel_vol=snapshot.rel_vol_display if snapshot else "—",
        change_str=change_str,
        gap=gap_str,
        pe=snapshot.pe if snapshot else "—",
        share_float=snapshot.shares_float_display if snapshot else "—",
        short_float=snapshot.short_float_display if snapshot else "—",
        news=news,
        recent_days=recent,
    )


def _payload_to_properties(p: TopOppsNotionPayload, names: dict[str, str]) -> dict[str, Any]:
    """Build properties dict in Discord order (Python 3.7+ preserves insertion order)."""

    def N(key: str) -> str:
        return names[key]

    props: dict[str, Any] = {}

    props[N("title")] = {"title": [{"type": "text", "text": {"content": _EMPTY_TITLE}}]}
    props[N("symbol")] = _rich_prop(p.symbol) or {
        "rich_text": [{"type": "text", "text": {"content": p.symbol}}]
    }
    props[N("date")] = {"date": {"start": p.date_iso}}

    n = _num_prop(p.entry)
    if n:
        props[N("entry")] = n
    n = _num_prop(p.stop)
    if n:
        props[N("stop")] = n
    n = _num_prop(p.exit_target)
    if n:
        props[N("exit")] = n

    rp = _rich_prop(p.exit_note)
    if rp:
        props[N("exit_note")] = rp

    n = _num_prop(p.rr)
    if n:
        props[N("rr")] = n
    n = _num_prop(p.ev)
    if n:
        props[N("ev")] = n

    rp = _rich_prop(p.side)
    if rp:
        props[N("side")] = rp
    rp = _rich_prop(p.notes)
    if rp:
        props[N("notes")] = rp

    props[N("open")] = {"number": float(p.open_)}
    props[N("high")] = {"number": float(p.high)}
    props[N("low")] = {"number": float(p.low)}
    props[N("close")] = {"number": float(p.close)}

    rp = _rich_prop(p.volume)
    if rp:
        props[N("volume")] = rp
    for key, val in [
        ("avg_vol", p.avg_vol),
        ("rel_vol", p.rel_vol),
        ("change", p.change_str),
        ("gap", p.gap),
        ("pe", p.pe),
        ("share_float", p.share_float),
        ("short_float", p.short_float),
        ("news", p.news),
    ]:
        rp = _rich_prop(val)
        if rp:
            props[N(key)] = rp
    rp = _rich_prop(p.recent_days)
    if rp:
        props[N("recent_days")] = rp

    return props


def _upload_png_file_upload_id(token: str, filename: str, png: bytes) -> str | None:
    """Notion direct upload: create file_upload, POST multipart to /send, return file_upload id.

    See https://developers.notion.com/docs/uploading-small-files — raw PUT is wrong; send uses
    multipart/form-data with field name ``file``.
    """
    h = _headers(token)
    try:
        r = requests.post(
            FILE_UPLOADS_URL,
            headers=h,
            json={
                "filename": filename[:900],
                "content_type": "image/png",
            },
            timeout=120,
        )
    except requests.RequestException as e:
        logger.warning("file_upload create failed: %s", e)
        return None

    if r.status_code >= 400:
        logger.warning("file_upload create %s: %s", r.status_code, r.text[:800])
        return None

    try:
        j = r.json()
    except Exception:
        return None

    uid = j.get("id")
    upload_url = j.get("upload_url")
    if not uid:
        logger.warning("file_upload response missing id")
        return None

    send_url = upload_url
    if not send_url:
        plain = re.sub(r"[^0-9a-fA-F]", "", str(uid))
        if len(plain) == 32:
            send_url = f"{FILE_UPLOADS_URL}/{_format_db_id(plain)}/send"

    try:
        # POST multipart — not PUT raw bytes
        send = requests.post(
            send_url,
            headers=_headers_auth_only(token),
            files={"file": (filename[:900], png, "image/png")},
            timeout=120,
        )
    except requests.RequestException as e:
        logger.warning("file_upload send failed: %s", e)
        return None

    if send.status_code >= 400:
        logger.warning("file_upload send %s: %s", send.status_code, send.text[:800])
        return None

    try:
        sj = send.json()
        if sj.get("status") == "uploaded":
            return str(sj.get("id", uid))
    except Exception:
        pass

    for _ in range(15):
        try:
            gr = requests.get(f"{FILE_UPLOADS_URL}/{uid}", headers=_headers(token), timeout=30)
            if gr.status_code == 200:
                st = (gr.json() or {}).get("status")
                if st == "uploaded":
                    break
        except requests.RequestException:
            pass
        time.sleep(0.2)

    return str(uid)


def _append_chart_blocks(token: str, page_id: str, charts: list[tuple[str, str, bytes]]) -> None:
    """Append heading + image blocks (caption = timeframe label) to the database row page."""
    if not charts:
        return

    plain = re.sub(r"[^0-9a-fA-F]", "", page_id)
    if len(plain) != 32:
        return
    bid = f"{plain[:8]}-{plain[8:12]}-{plain[12:16]}-{plain[16:20]}-{plain[20:]}"

    children: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": "Charts"}}],
            },
        }
    ]

    for label, filename, png in charts:
        fid = _upload_png_file_upload_id(token, filename, png)
        if not fid:
            continue
        cap = [{"type": "text", "text": {"content": label[:2000]}}]
        children.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "caption": cap,
                    "type": "file_upload",
                    "file_upload": {"id": fid},
                },
            }
        )

    if len(children) <= 1:
        return

    url = f"{NOTION_BLOCKS_URL}/{bid}/children"
    try:
        ar = requests.patch(url, headers=_headers(token), json={"children": children}, timeout=120)
        if ar.status_code >= 400:
            logger.warning("append chart blocks %s: %s", ar.status_code, ar.text[:1200])
    except requests.RequestException as e:
        logger.warning("append chart blocks failed: %s", e)


def create_notion_page(
    p: TopOppsNotionPayload,
    chart_pngs: list[tuple[str, str, bytes]] | None = None,
) -> tuple[bool, str]:
    """POST database row; then attach chart PNGs as page content when provided."""
    if not notion_top_opps_ready():
        return False, "Notion is not configured (NOTION_API_KEY / NOTION_TOP_OPPS_DATABASE_ID)."

    token = os.environ["NOTION_API_KEY"].strip()
    db_id_raw = os.environ["NOTION_TOP_OPPS_DATABASE_ID"].strip()
    try:
        fmt_id = _format_db_id(db_id_raw)
    except ValueError:
        return False, "Invalid NOTION_TOP_OPPS_DATABASE_ID (expected 32 hex chars)."

    ok, err, names, db_fmt_id, data_source_id = ensure_top_opps_schema(fmt_id, token)
    if not ok or not names or not db_fmt_id:
        return False, err or "Schema resolution failed."

    if data_source_id:
        parent: dict[str, Any] = {
            "type": "data_source_id",
            "data_source_id": data_source_id,
        }
    else:
        parent = {"type": "database_id", "database_id": db_fmt_id}

    body = {
        "parent": parent,
        "properties": _payload_to_properties(p, names),
    }

    try:
        r = requests.post(NOTION_PAGES_URL, json=body, headers=_headers(token), timeout=60)
    except requests.RequestException as e:
        logger.exception("Notion request failed")
        return False, f"Network error: {e}"

    if r.status_code >= 400:
        logger.warning("Notion API %s: %s", r.status_code, r.text[:2000])
        try:
            err = r.json()
            msg = err.get("message", r.text[:500])
        except Exception:
            msg = r.text[:500]
        return False, f"Notion API error: {msg}"

    page_id = ""
    page_url = ""
    try:
        data = r.json()
        page_id = data.get("id", "")
        page_url = data.get("url", "")
    except Exception:
        pass

    if chart_pngs and page_id:
        _append_chart_blocks(token, page_id, chart_pngs)

    if page_url:
        return True, page_url
    return True, "Saved to Notion."


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    from pathlib import Path

    base = Path(__file__).resolve().parent
    for candidate in (base / ".env", base.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate)
            return


if __name__ == "__main__":
    _load_dotenv_if_present()
    ap = argparse.ArgumentParser(description="Notion /top_opps database helpers")
    ap.add_argument(
        "--create-db",
        action="store_true",
        help="Create a new database under NOTION_TOP_OPPS_PARENT_PAGE_ID; print NOTION_TOP_OPPS_DATABASE_ID",
    )
    args = ap.parse_args()
    token = (os.environ.get("NOTION_API_KEY") or "").strip()
    if not token:
        print("Set NOTION_API_KEY", file=sys.stderr)
        sys.exit(1)
    if args.create_db:
        parent = (os.environ.get("NOTION_TOP_OPPS_PARENT_PAGE_ID") or "").strip()
        if not parent:
            print("Set NOTION_TOP_OPPS_PARENT_PAGE_ID to a Notion page ID (Share → Copy link).", file=sys.stderr)
            sys.exit(1)
        ok, out = create_top_opps_database(parent, token)
        if not ok:
            print(out, file=sys.stderr)
            sys.exit(1)
        print("Created database. Add to .env / Railway:")
        print(f"NOTION_TOP_OPPS_DATABASE_ID={out}")
        print("Invite your Notion integration to this new database if needed.")
        sys.exit(0)
    ap.print_help()
