"""Load ``.env`` files into the process. Host-provided env always wins for existing keys."""

from __future__ import annotations

import os
from pathlib import Path

_CONFIGURED = False


def _merge_env_file(path: Path) -> None:
    """Set keys from a simple KEY=value file only if the key is not already in ``os.environ``."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        cur = os.environ.get(key)
        if cur is not None and str(cur).strip():
            continue
        os.environ[key] = val


def configure_environment() -> None:
    """Call once at process startup (before reading secrets).

    Railway and other hosts inject variables into ``os.environ`` before Python
    starts. Some setups also place a ``.env`` file under ``/app``. We load
    dotenv and a manual fallback with **override disabled** for existing keys,
    so dashboard env always wins; missing keys can be filled from files.

    The old behavior skipped *all* file loading when ``RAILWAY_*`` was present,
    which could leave the process without variables that only existed on disk.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore[misc, assignment]

    root = Path(__file__).resolve().parent
    candidates: list[Path] = []
    for p in (root / ".env", Path("/app") / ".env", Path.cwd() / ".env"):
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp not in candidates:
            candidates.append(rp)

    if load_dotenv:
        for env_path in candidates:
            if env_path.is_file():
                load_dotenv(env_path, override=False)

    for env_path in candidates:
        _merge_env_file(env_path)
