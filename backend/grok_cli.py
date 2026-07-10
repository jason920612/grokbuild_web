"""Helpers for locating grok sessions on disk and talking to the grok CLI.

Session data lives under ``$GROK_HOME/sessions/<url-encoded-cwd>/<session-id>/``.
Each session directory contains, among others:

* ``summary.json``  -- index entry (title, cwd, model, timestamps)
* ``updates.jsonl`` -- authoritative ACP ``session/update`` log (drives replay)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from . import config


def encode_cwd(cwd: str) -> str:
    """Reproduce grok's directory-name encoding for a working directory.

    grok percent-encodes the path and uppercases the hex digits, e.g.
    ``C:\\Users\\me`` -> ``C%3A%5CUsers%5Cme``.
    """
    out = []
    for ch in cwd:
        if ch.isalnum() or ch in "-._~":
            out.append(ch)
        else:
            out.append("%" + format(ord(ch), "02X"))
    return "".join(out)


def iter_session_dirs() -> Iterator[Path]:
    """Yield every ``<session-id>`` directory under the sessions root."""
    root = config.SESSIONS_DIR
    if not root.exists():
        return
    for cwd_dir in root.iterdir():
        if not cwd_dir.is_dir():
            continue
        for sess_dir in cwd_dir.iterdir():
            if sess_dir.is_dir() and (sess_dir / "summary.json").exists():
                yield sess_dir


def read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def find_session_dir(session_id: str) -> Optional[Path]:
    """Locate the on-disk directory for a session id (scans the sessions tree)."""
    for sess_dir in iter_session_dirs():
        if sess_dir.name == session_id:
            return sess_dir
    return None
