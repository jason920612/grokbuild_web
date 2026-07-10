"""Keeps one shared :class:`AcpBridge` per session id.

Multiple browser tabs/phones attach to the same bridge so they all drive and
observe one live session.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from .acp_bridge import AcpBridge
from . import session_store


class BridgeManager:
    def __init__(self) -> None:
        self._bridges: dict[str, AcpBridge] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def get(self, session_id: str) -> Optional[AcpBridge]:
        async with self._lock_for(session_id):
            bridge = self._bridges.get(session_id)
            if bridge and bridge.alive:
                return bridge

            meta = session_store.get_session_meta(session_id)
            if not meta:
                return None
            bridge = AcpBridge(session_id, meta["cwd"])
            await bridge.start()
            self._bridges[session_id] = bridge
            return bridge

    async def shutdown(self) -> None:
        for bridge in list(self._bridges.values()):
            await bridge.stop()
        self._bridges.clear()


manager = BridgeManager()
