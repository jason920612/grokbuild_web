"""Session-less ACP control client.

One shared leader-attached ``grok agent --leader stdio`` process used for global
queries that don't belong to any one session: subscription/auth info, billing
usage, the live session list, and the agent's model/command catalog (from the
``initialize`` response).
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

from . import config

_READ_LIMIT = 32 * 1024 * 1024


class ControlClient:
    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.init_meta: dict[str, Any] = {}  # _meta of initialize (modelState, version…)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def ensure(self) -> "ControlClient":
        async with self._lock:
            if not self.alive:
                await self._start()
        return self

    async def _start(self) -> None:
        args = [config.GROK_EXE, "agent"]
        if config.USE_LEADER:
            args.append("--leader")
        args.append("stdio")
        env = dict(os.environ)
        env["GROK_HOME"] = str(config.GROK_HOME)
        self.proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=_READ_LIMIT,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        init = await self.request(
            "initialize",
            {
                "protocolVersion": "1",
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "grokweb-control", "version": "0.1.0"},
            },
        )
        self.init_meta = (init or {}).get("_meta", {}) or {}

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(json.dumps(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
                elif "id" in msg:
                    # We host no session; politely decline agent-initiated requests.
                    await self._send(
                        {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601, "message": "control client"}}
                    )
        except asyncio.CancelledError:
            return

    async def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any], timeout: float = 30) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return await asyncio.wait_for(fut, timeout)

    # ---- convenience wrappers -------------------------------------------
    async def subscription(self) -> Any:
        return await self.request("_x.ai/auth/check_subscription", {})

    async def billing(self) -> Any:
        return await self.request("_x.ai/billing", {})

    async def live_sessions(self) -> list[dict[str, Any]]:
        r = await self.request("_x.ai/sessions/list", {})
        return ((r or {}).get("result") or {}).get("sessions", [])

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass


control = ControlClient()
