"""Session listing and history replay.

History is reconstructed from ``updates.jsonl`` -- the same ACP ``session/update``
event stream the live bridge produces -- and coalesced into display items so the
frontend can render one renderer for both replay and live streaming.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import grok_cli


def list_sessions() -> list[dict[str, Any]]:
    """Return session index entries, newest first."""
    sessions: list[dict[str, Any]] = []
    for sess_dir in grok_cli.iter_session_dirs():
        summary = grok_cli.read_json(sess_dir / "summary.json") or {}
        info = summary.get("info", {})
        session_id = info.get("id") or sess_dir.name
        sessions.append(
            {
                "id": session_id,
                "cwd": info.get("cwd", ""),
                "title": summary.get("generated_title")
                or summary.get("session_summary")
                or "(untitled)",
                "model": summary.get("current_model_id"),
                "created_at": summary.get("created_at"),
                "updated_at": summary.get("updated_at"),
                "num_messages": summary.get("num_chat_messages", 0),
                "branch": summary.get("head_branch"),
            }
        )
    sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return sessions


def get_session_meta(session_id: str) -> Optional[dict[str, Any]]:
    sess_dir = grok_cli.find_session_dir(session_id)
    if not sess_dir:
        return None
    summary = grok_cli.read_json(sess_dir / "summary.json") or {}
    info = summary.get("info", {})
    return {
        "id": info.get("id") or session_id,
        "cwd": info.get("cwd", ""),
        "title": summary.get("generated_title") or summary.get("session_summary"),
        "model": summary.get("current_model_id"),
        "dir": str(sess_dir),
    }


def _text_of(content: Any) -> str:
    """Extract text from an ACP content block (or list of them)."""
    if isinstance(content, dict):
        if content.get("type") == "image":
            return "🖼️ [圖片]"
        return content.get("text", "")
    if isinstance(content, list):
        return "".join(_text_of(c) for c in content)
    if isinstance(content, str):
        return content
    return ""


def load_history(session_id: str) -> list[dict[str, Any]]:
    """Replay ``updates.jsonl`` into coalesced display items.

    Item shapes:
        {"role": "user"|"assistant"|"thought", "text": str}
        {"role": "tool", "id", "title", "kind", "status", "content": [...]}
        {"role": "plan", "entries": [...]}
    """
    sess_dir = grok_cli.find_session_dir(session_id)
    if not sess_dir:
        return []
    updates_path = sess_dir / "updates.jsonl"
    if not updates_path.exists():
        return []

    items: list[dict[str, Any]] = []
    tools: dict[str, dict[str, Any]] = {}
    plan_item: Optional[dict[str, Any]] = None

    def last_text_item(role: str) -> Optional[dict[str, Any]]:
        if items and items[-1].get("role") == role and "text" in items[-1]:
            return items[-1]
        return None

    with updates_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            update = event.get("params", {}).get("update", {})
            kind = update.get("sessionUpdate")

            if kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk"):
                role = {
                    "user_message_chunk": "user",
                    "agent_message_chunk": "assistant",
                    "agent_thought_chunk": "thought",
                }[kind]
                text = _text_of(update.get("content"))
                item = last_text_item(role)
                if item is None:
                    items.append({"role": role, "text": text})
                else:
                    item["text"] += text

            elif kind == "tool_call":
                tool = {
                    "role": "tool",
                    "id": update.get("toolCallId"),
                    "title": update.get("title") or update.get("kind") or "tool",
                    "kind": update.get("kind"),
                    "status": update.get("status", "pending"),
                    "content": update.get("content") or [],
                }
                items.append(tool)
                if tool["id"]:
                    tools[tool["id"]] = tool

            elif kind == "tool_call_update":
                tid = update.get("toolCallId")
                tool = tools.get(tid)
                if tool is None:
                    tool = {
                        "role": "tool",
                        "id": tid,
                        "title": update.get("title") or "tool",
                        "kind": update.get("kind"),
                        "status": update.get("status", "pending"),
                        "content": update.get("content") or [],
                    }
                    items.append(tool)
                    if tid:
                        tools[tid] = tool
                else:
                    if update.get("status"):
                        tool["status"] = update["status"]
                    if update.get("title"):
                        tool["title"] = update["title"]
                    if update.get("content"):
                        tool["content"] = update["content"]

            elif kind == "plan":
                if plan_item is None:
                    plan_item = {"role": "plan", "entries": update.get("entries", [])}
                    items.append(plan_item)
                else:
                    plan_item["entries"] = update.get("entries", [])

    return items
