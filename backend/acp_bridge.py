"""ACP (Agent Client Protocol) bridge to the grok agent.

One :class:`AcpBridge` owns a child ``grok agent --leader stdio`` process attached
to a single session. Because it connects through grok's shared *leader* process,
a terminal TUI and this web bridge observe and drive the very same live session
-- true simultaneous mirroring, no terminal scraping required.

The bridge:
  * speaks JSON-RPC (newline-delimited) over the child's stdio,
  * fans out ``session/update`` notifications to any number of subscribers,
  * answers agent-initiated ``fs/*`` requests against the local filesystem,
  * forwards ``session/request_permission`` to the browser and relays the choice,
  * forwards ``_x.ai/ask_user_question`` (the option-picker the TUI shows) to the
    browser; the answer format is ``{"outcome": "accepted", "answers": {<question
    text>: <label or [labels]>}}``,
  * turns the ``_x.ai/session/prompt_complete`` notification into ``turn_end`` so
    the UI also settles when the *terminal* drove the turn.

Interactive requests are cached in ``pending_interactions`` until answered, so a
browser that connects after the TUI popped a question still sees it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Optional

from . import config

_READ_LIMIT = 32 * 1024 * 1024  # tool outputs can be large


class AcpBridge:
    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._ready = asyncio.Event()  # set once session/load completes
        self._perm_requests: dict[str, Any] = {}  # our requestId -> jsonrpc id
        # requestId -> broadcast message, replayed to late-joining websockets
        self.pending_interactions: dict[str, dict[str, Any]] = {}
        # populated from the session/load response and kept current afterwards
        self.models: dict[str, Any] = {}
        self.session_config: dict[str, Any] = {}  # x.ai/sessionConfig (selected flags)
        self.current_model: Optional[str] = None
        self.current_mode: Optional[str] = None
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._closed = False

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
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
            cwd=self.cwd or None,
            env=env,
            limit=_READ_LIMIT,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        await self._request(
            "initialize",
            {
                "protocolVersion": "1",
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": False,
                },
                "clientInfo": {"name": "grokweb", "version": "0.1.0"},
            },
        )
        if self.session_id:
            # Load an existing session. Updates replayed during load are treated
            # as history (rendered from updates.jsonl) and dropped until the load
            # response resolves.
            result = await self._request(
                "session/load",
                {"sessionId": self.session_id, "cwd": self.cwd, "mcpServers": []},
            )
        else:
            # Create a brand-new session in the given working directory.
            result = await self._request(
                "session/new", {"cwd": self.cwd, "mcpServers": []}
            )
            self.session_id = (result or {}).get("sessionId")
            if not self.session_id:
                raise RuntimeError("session/new returned no sessionId")
        self._capture_state(result or {})
        self._ready.set()

    def _capture_state(self, result: dict[str, Any]) -> None:
        models = result.get("models") or {}
        if models:
            self.models = models
            self.current_model = models.get("currentModelId")
        meta = result.get("_meta") or {}
        cfg = meta.get("x.ai/sessionConfig") or {}
        if cfg:
            self.session_config = cfg
            for opt in cfg.get("options", []):
                if opt.get("selected"):
                    if opt.get("category") == "model":
                        self.current_model = opt.get("id")
                    elif opt.get("category") == "mode":
                        self.current_mode = opt.get("id")

    def state(self) -> dict[str, Any]:
        return {
            "modelId": self.current_model,
            "modeId": self.current_mode,
            "models": self.models,
            "sessionConfig": self.session_config,
        }

    async def stop(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    # ---- pub/sub -------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(message)

    # ---- outbound requests / notifications -----------------------------
    async def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        async with self._lock:
            req_id = self._next_id
            self._next_id += 1
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return await future

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _respond(self, req_id: Any, result: Any = None, error: Any = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        await self._send(msg)

    # ---- public actions ------------------------------------------------
    @staticmethod
    def _attachment_blocks(text: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build the prompt content list: user text + image blocks + path notes."""
        blocks: list[dict[str, Any]] = []
        notes: list[str] = []
        for att in attachments:
            path = Path(att.get("path", ""))
            if not path.is_file():
                notes.append(f"(附件遺失: {att.get('name') or path})")
                continue
            mime = att.get("mime") or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            if att.get("isImage") or mime.startswith("image/"):
                blocks.append(
                    {
                        "type": "image",
                        "data": base64.b64encode(path.read_bytes()).decode(),
                        "mimeType": mime,
                    }
                )
                notes.append(f"[附加圖片: {path}]")
            else:
                notes.append(f"[附加檔案: {path} ({mime}, {path.stat().st_size} bytes)] 請用你的檔案工具讀取。")
        body = text if not notes else (text + "\n\n" + "\n".join(notes)).strip()
        blocks.insert(0, {"type": "text", "text": body})
        return blocks

    async def prompt(self, text: str, attachments: Optional[list[dict[str, Any]]] = None) -> None:
        await self._ready.wait()
        content = self._attachment_blocks(text, attachments or [])

        async def run() -> None:
            try:
                result = await self._request(
                    "session/prompt",
                    {
                        "sessionId": self.session_id,
                        "prompt": content,
                    },
                )
                self._broadcast(
                    {"type": "turn_end", "stopReason": (result or {}).get("stopReason", "end")}
                )
            except Exception as exc:  # noqa: BLE001 - surface to UI
                self._broadcast({"type": "error", "message": str(exc)})

        asyncio.create_task(run())

    async def respond_permission(
        self, request_id: str, option_id: Optional[str], cancelled: bool = False
    ) -> None:
        jsonrpc_id = self._perm_requests.pop(request_id, None)
        self.pending_interactions.pop(request_id, None)
        if jsonrpc_id is None:
            return
        if cancelled or option_id is None:
            outcome = {"outcome": "cancelled"}
        else:
            outcome = {"outcome": "selected", "optionId": option_id}
        await self._respond(jsonrpc_id, {"outcome": outcome})
        self._broadcast({"type": "interaction_done", "requestId": request_id})

    async def respond_question(
        self, request_id: str, answers: Optional[dict[str, Any]], skipped: bool = False
    ) -> None:
        """Answer an ask_user_question. ``answers`` maps the question text to the
        chosen label (string) or labels (list) for multi-select."""
        jsonrpc_id = self._perm_requests.pop(request_id, None)
        self.pending_interactions.pop(request_id, None)
        if jsonrpc_id is None:
            return
        if skipped or not answers:
            result: dict[str, Any] = {"outcome": "skip_interview", "answers": {}}
        else:
            result = {"outcome": "accepted", "answers": answers}
        await self._respond(jsonrpc_id, result)
        self._broadcast({"type": "interaction_done", "requestId": request_id})

    async def cancel(self) -> None:
        if self._ready.is_set():
            await self._notify("session/cancel", {"sessionId": self.session_id})

    async def ext(self, method: str, params: Optional[dict[str, Any]] = None, timeout: float = 30) -> Any:
        """Call any (extension) method with the sessionId filled in."""
        await self._ready.wait()
        p = dict(params or {})
        p.setdefault("sessionId", self.session_id)
        return await asyncio.wait_for(self._request(method, p), timeout)

    async def session_info(self) -> Any:
        r = await self.ext("_x.ai/session/info")
        return (r or {}).get("result") or r

    async def set_model(self, model_id: str) -> None:
        await self.ext("session/set_model", {"modelId": model_id})
        self.current_model = model_id
        self._broadcast({"type": "session_state", "state": self.state()})

    async def set_mode(self, mode_id: str) -> None:
        await self.ext("session/set_mode", {"modeId": mode_id})
        self.current_mode = mode_id
        self._broadcast({"type": "session_state", "state": self.state()})

    # ---- inbound handling ----------------------------------------------
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
                await self._dispatch(msg)
        except asyncio.CancelledError:
            return
        finally:
            if not self._closed:
                self._broadcast({"type": "agent_exit"})

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        # Response to one of our requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            future = self._pending.pop(msg["id"], None)
            if future and not future.done():
                if "error" in msg:
                    future.set_exception(RuntimeError(json.dumps(msg["error"])))
                else:
                    future.set_result(msg.get("result"))
            return

        method = msg.get("method")
        params = msg.get("params", {}) or {}

        # Agent-initiated request (has id) -> we must respond.
        if "id" in msg:
            await self._handle_server_request(msg["id"], method, params)
            return

        # Notification.
        if method == "session/update":
            if not self._ready.is_set():
                return  # drop replay during load
            update = params.get("update", {})
            self._broadcast({"type": "session_update", "update": update})
            # A completed/failed tool call resolves any interaction tied to it
            # (e.g. the question was answered in the terminal instead).
            if update.get("sessionUpdate") == "tool_call_update" and update.get("status") in (
                "completed",
                "failed",
            ):
                tool_id = update.get("toolCallId")
                for req_id, pending in list(self.pending_interactions.items()):
                    if pending.get("toolCallId") == tool_id:
                        self._perm_requests.pop(req_id, None)
                        self.pending_interactions.pop(req_id, None)
                        self._broadcast({"type": "interaction_done", "requestId": req_id})
            # Mode changed from any client (e.g. the terminal TUI).
            elif update.get("sessionUpdate") == "current_mode_update":
                self.current_mode = update.get("currentModeId") or self.current_mode
                self._broadcast({"type": "session_state", "state": self.state()})
        elif method == "_x.ai/session/prompt_complete":
            if self._ready.is_set() and params.get("sessionId") in (None, self.session_id):
                self._broadcast({"type": "turn_end", "stopReason": "end_turn"})

    async def _handle_server_request(
        self, req_id: Any, method: Optional[str], params: dict[str, Any]
    ) -> None:
        if method == "session/request_permission":
            request_key = str(req_id)
            self._perm_requests[request_key] = req_id
            msg = {
                "type": "permission_request",
                "requestId": request_key,
                "toolCall": params.get("toolCall", {}),
                "toolCallId": (params.get("toolCall") or {}).get("toolCallId"),
                "options": params.get("options", []),
            }
            self.pending_interactions[request_key] = msg
            self._broadcast(msg)
            return

        if method == "_x.ai/ask_user_question":
            if params.get("sessionId") not in (None, self.session_id):
                await self._respond(req_id, error={"code": -32600, "message": "wrong session"})
                return
            request_key = str(req_id)
            self._perm_requests[request_key] = req_id
            msg = {
                "type": "question_request",
                "requestId": request_key,
                "toolCallId": params.get("toolCallId"),
                "questions": params.get("questions", []),
            }
            self.pending_interactions[request_key] = msg
            self._broadcast(msg)
            return

        if method == "fs/read_text_file":
            await self._respond(req_id, self._fs_read(params))
            return

        if method == "fs/write_text_file":
            await self._respond(req_id, self._fs_write(params))
            return

        # Unknown request: reply with a JSON-RPC "method not found".
        await self._respond(req_id, error={"code": -32601, "message": f"unsupported: {method}"})

    # ---- filesystem capability (same-machine passthrough) --------------
    @staticmethod
    def _fs_read(params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params.get("path", ""))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(str(exc))
        line = params.get("line")
        limit = params.get("limit")
        if line is not None or limit is not None:
            lines = text.splitlines(keepends=True)
            start = (line - 1) if line else 0
            end = (start + limit) if limit else len(lines)
            text = "".join(lines[start:end])
        return {"content": text}

    @staticmethod
    def _fs_write(params: dict[str, Any]) -> None:
        path = Path(params.get("path", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(params.get("content", ""), encoding="utf-8")
        return None
