"""Read and edit ``~/.grok/config.toml`` for the settings UI.

Reading uses stdlib ``tomllib``. Writing is line-based and only touches the
specific ``key = value`` line (or appends it to its section), preserving all
comments and unrelated content.

Only a whitelist of harmless keys is editable from the web UI.
"""
from __future__ import annotations

import tomllib
from typing import Any

from . import config

# (section, key) -> {"type": ..., "label": ..., "options": [...]}
EDITABLE: dict[tuple[str, str], dict[str, Any]] = {
    ("ui", "permission_mode"): {
        "type": "choice",
        "options": ["always-approve", "default", "accept-edits", "dont-ask", "plan"],
        "label": "權限模式",
        "hint": "always-approve = 不跳權限詢問；default = 工具需要批准（會出現在網頁/TUI）",
    },
    ("ui", "compact_mode"): {"type": "bool", "label": "TUI 精簡模式", "hint": "終端機介面的密度"},
    ("ui", "yolo"): {"type": "bool", "label": "YOLO 模式", "hint": "跳過所有確認（危險）"},
    ("cli", "use_leader"): {
        "type": "bool",
        "label": "Leader 共享模式",
        "hint": "TUI 與網頁共享同一個活 session（關閉會失去同步；改動後需重開 TUI）",
    },
    ("cli", "auto_update"): {"type": "bool", "label": "自動更新", "hint": "grok CLI 自動更新"},
    ("ui", "max_thoughts_width"): {"type": "int", "label": "思考區寬度", "hint": "TUI 思考文字最大寬度"},
    ("ui", "fork_secondary_model"): {"type": "str", "label": "Fork 次要模型", "hint": "fork session 使用的模型"},
}


def read() -> dict[str, Any]:
    path = config.GROK_HOME / "config.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        data = {}

    entries = []
    for (section, key), meta in EDITABLE.items():
        entries.append(
            {
                "section": section,
                "key": key,
                "value": (data.get(section) or {}).get(key),
                **meta,
            }
        )
    return {"path": str(path), "entries": entries}


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write(section: str, key: str, value: Any) -> None:
    if (section, key) not in EDITABLE:
        raise ValueError(f"not editable: [{section}] {key}")
    meta = EDITABLE[(section, key)]
    if meta["type"] == "bool":
        value = value in (True, "true", "on", 1, "1")
    elif meta["type"] == "int":
        value = int(value)
    elif meta["type"] == "choice" and value not in meta["options"]:
        raise ValueError(f"invalid choice: {value}")

    path = config.GROK_HOME / "config.toml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=False)

    out: list[str] = []
    in_section = False
    replaced = False
    section_end = -1  # index in `out` right after the last line of the target section
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_section and not replaced:
                section_end = len(out)
            in_section = stripped == f"[{section}]"
        elif in_section and not replaced and stripped.split("=")[0].strip() == key:
            out.append(f"{key} = {_format_value(value)}")
            replaced = True
            continue
        out.append(line)
    if not replaced:
        if section_end >= 0:
            # insert before the next section header, skipping trailing blanks
            while section_end > 0 and out[section_end - 1].strip() == "":
                section_end -= 1
            out.insert(section_end, f"{key} = {_format_value(value)}")
        elif in_section:
            out.append(f"{key} = {_format_value(value)}")
        else:
            out.extend(["", f"[{section}]", f"{key} = {_format_value(value)}"])
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
