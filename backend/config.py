"""Runtime configuration for the grokweb bridge.

All values can be overridden with environment variables so the same code runs
on another machine without edits.
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path


def _default_grok_home() -> Path:
    env = os.environ.get("GROK_HOME")
    if env:
        return Path(env)
    return Path.home() / ".grok"


def _default_grok_exe(grok_home: Path) -> str:
    # Prefer an explicit override, then the bundled binary, then PATH.
    env = os.environ.get("GROK_EXE")
    if env:
        return env
    for candidate in (grok_home / "bin" / "grok.exe", grok_home / "bin" / "grok"):
        if candidate.exists():
            return str(candidate)
    found = shutil.which("grok")
    return found or "grok"


GROK_HOME: Path = _default_grok_home()
GROK_EXE: str = _default_grok_exe(GROK_HOME)
SESSIONS_DIR: Path = GROK_HOME / "sessions"

# Web server
HOST: str = os.environ.get("GROKWEB_HOST", "0.0.0.0")
PORT: int = int(os.environ.get("GROKWEB_PORT", "8787"))

# Whether the ACP client should attach to the shared leader process so that a
# terminal TUI and the web share one live session. Disable to run standalone.
USE_LEADER: bool = os.environ.get("GROKWEB_USE_LEADER", "1") not in ("0", "false", "False")

FRONTEND_DIR: Path = Path(__file__).resolve().parent.parent / "frontend"

# Access token gating remote (tunnel/LAN) requests. Local loopback is exempt.
# Fixed via env so the same URL keeps working across restarts; otherwise random.
ACCESS_TOKEN: str = os.environ.get("GROKWEB_TOKEN") or secrets.token_urlsafe(18)
COOKIE_NAME: str = "grokweb_key"
