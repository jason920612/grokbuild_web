"""FastAPI application: REST session index + history, and a WebSocket that
bridges a browser to a live grok session.

Browser <-> server WebSocket protocol
-------------------------------------
Server -> browser:
    {"type": "history",  "items": [...]}                 once, on connect
    {"type": "session_update", "update": {...}}          live ACP update
    {"type": "permission_request", "requestId", "options", "toolCall", "toolCallId"}
    {"type": "question_request", "requestId", "toolCallId", "questions": [
        {"question", "options": [{"label", "description"}], "multiSelect"}]}
    {"type": "interaction_done", "requestId"}            answered/resolved (maybe elsewhere)
    {"type": "turn_end", "stopReason"}                   fires for web- AND terminal-driven turns
    {"type": "error", "message"}
    {"type": "agent_exit"}
Browser -> server:
    {"type": "ping"}                                     heartbeat (server replies {"type":"pong"})
    {"type": "prompt", "text": "...", "attachments": [{path,name,mime,isImage}]?}
    {"type": "permission_response", "requestId", "optionId"|null, "cancelled"?}
    {"type": "question_response", "requestId", "answers": {<question>: <label|[labels]>}, "skipped"?}
    {"type": "cancel"}
"""
from __future__ import annotations

import asyncio
import contextlib

import mimetypes
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, grok_config, session_store
from .bridge_manager import manager
from .control import control

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

app = FastAPI(title="grokweb")
auth.add_gate(app)


@app.get("/api/sessions")
async def api_sessions() -> JSONResponse:
    return JSONResponse(session_store.list_sessions())


@app.get("/api/sessions/{session_id}")
async def api_session(session_id: str) -> JSONResponse:
    meta = session_store.get_session_meta(session_id)
    if not meta:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(meta)


@app.get("/api/sessions/{session_id}/history")
async def api_history(session_id: str) -> JSONResponse:
    return JSONResponse({"items": session_store.load_history(session_id)})


@app.get("/api/status")
async def api_status() -> JSONResponse:
    """Global status: subscription, usage/billing, agent version, live sessions."""
    out: dict = {}
    try:
        c = await control.ensure()
        for name, coro in (
            ("subscription", c.subscription()),
            ("billing", c.billing()),
            ("liveSessions", c.live_sessions()),
        ):
            try:
                out[name] = await coro
            except Exception as exc:  # noqa: BLE001 - partial status is fine
                out[name] = {"error": str(exc)}
        out["agent"] = {
            "version": c.init_meta.get("agentVersion"),
            "hostname": c.init_meta.get("hostname"),
            "modelState": c.init_meta.get("modelState"),
        }
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return JSONResponse(out)


@app.get("/api/sessions/{session_id}/settings")
async def api_session_settings(session_id: str) -> JSONResponse:
    """Per-session live settings + context usage + command catalog."""
    bridge = await manager.get(session_id)
    if bridge is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    out: dict = {"state": bridge.state()}
    try:
        out["info"] = await bridge.session_info()
    except Exception as exc:  # noqa: BLE001
        out["info"] = {"error": str(exc)}
    try:
        r = await bridge.ext("_x.ai/commands/list")
        out["commands"] = (r or {}).get("commands", [])
    except Exception:  # noqa: BLE001
        out["commands"] = []
    try:
        c = await control.ensure()
        for s in await c.live_sessions():
            if s.get("sessionId") == session_id:
                out["live"] = s
                break
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse(out)


@app.post("/api/sessions/{session_id}/settings")
async def api_session_settings_set(session_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    bridge = await manager.get(session_id)
    if bridge is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        if body.get("modelId"):
            await bridge.set_model(body["modelId"])
        if body.get("modeId"):
            await bridge.set_mode(body["modeId"])
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=502)
    return JSONResponse({"state": bridge.state()})


_TEXT_MIMES = {"application/json", "application/xml", "application/javascript", "application/toml"}


def _resolve_session_file(session_id: str, raw_path: str) -> Optional[Path]:
    """Resolve a requested path and require it to live inside the session cwd."""
    meta = session_store.get_session_meta(session_id)
    if not meta or not meta.get("cwd"):
        return None
    try:
        target = Path(raw_path).resolve()
        root = Path(meta["cwd"]).resolve()
    except OSError:
        return None
    if not target.is_file() or root not in (target, *target.parents):
        return None
    return target


@app.get("/api/sessions/{session_id}/file")
async def api_session_file(session_id: str, path: str, meta: int = 0):
    """Serve a file from the session workspace (for inline previews).

    ``?meta=1`` returns JSON {name, size, mime, isImage, isText} instead of bytes.
    """
    target = _resolve_session_file(session_id, path)
    if target is None:
        return JSONResponse({"error": "not found or outside workspace"}, status_code=404)
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    is_image = mime in IMAGE_MIMES
    is_text = mime.startswith("text/") or mime in _TEXT_MIMES
    if not is_text and not is_image:
        try:  # unknown types: sniff a small chunk for utf-8 text
            with target.open("rb") as fh:
                fh.read(4096).decode("utf-8")
            is_text = True
        except (UnicodeDecodeError, OSError):
            pass
    if meta:
        return JSONResponse(
            {
                "name": target.name,
                "size": target.stat().st_size,
                "mime": mime,
                "isImage": is_image,
                "isText": is_text,
            }
        )
    return FileResponse(target, media_type=mime if (is_image or is_text) else "application/octet-stream",
                        filename=None if (is_image or is_text) else target.name)


@app.post("/api/sessions/{session_id}/upload")
async def api_upload(session_id: str, file: UploadFile) -> JSONResponse:
    """Save an attachment into the session's workspace so agent tools can read it."""
    meta = session_store.get_session_meta(session_id)
    if not meta or not meta.get("cwd"):
        return JSONResponse({"error": "session not found"}, status_code=404)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "file too large (max 25MB)"}, status_code=413)

    safe = re.sub(r"[^\w.\-一-鿿]", "_", file.filename or "file")
    updir = Path(meta["cwd"]) / ".grokweb" / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    dest = updir / f"{int(time.time())}-{safe}"
    dest.write_bytes(data)

    mime = file.content_type or "application/octet-stream"
    return JSONResponse(
        {
            "path": str(dest),
            "name": file.filename,
            "size": len(data),
            "mime": mime,
            "isImage": mime in IMAGE_MIMES,
        }
    )


@app.get("/api/config")
async def api_config_get() -> JSONResponse:
    return JSONResponse(grok_config.read())


@app.put("/api/config")
async def api_config_put(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        grok_config.write(body["section"], body["key"], body.get("value"))
    except (KeyError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(grok_config.read())


@app.websocket("/ws/{session_id}")
async def ws_session(ws: WebSocket, session_id: str) -> None:
    if auth.is_external(ws) and not auth.has_valid_token(ws):
        await ws.close(code=1008)
        return
    await ws.accept()

    bridge = await manager.get(session_id)
    if bridge is None:
        await ws.send_json({"type": "error", "message": f"session {session_id} not found"})
        await ws.close()
        return

    # Subscribe before sending history so no live event is missed.
    queue = bridge.subscribe()
    await ws.send_json({"type": "history", "items": session_store.load_history(session_id)})
    # Replay any unanswered question/permission popups to this late joiner.
    for msg in list(bridge.pending_interactions.values()):
        await ws.send_json(msg)

    async def pump_updates() -> None:
        while True:
            message = await queue.get()
            await ws.send_json(message)

    pump_task = asyncio.create_task(pump_updates())
    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "prompt":
                text = (data.get("text") or "").strip()
                attachments = data.get("attachments") or []
                if text or attachments:
                    await bridge.prompt(text, attachments)
            elif kind == "permission_response":
                await bridge.respond_permission(
                    data.get("requestId"),
                    data.get("optionId"),
                    bool(data.get("cancelled")),
                )
            elif kind == "question_response":
                await bridge.respond_question(
                    data.get("requestId"),
                    data.get("answers"),
                    bool(data.get("skipped")),
                )
            elif kind == "cancel":
                await bridge.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
        bridge.unsubscribe(queue)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="static")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await manager.shutdown()
    await control.stop()
