from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def message_role_content(message):
    if isinstance(message, HumanMessage):
        return {"role": "human", "content": message.content}
    if isinstance(message, AIMessage):
        item = {"role": "ai", "content": message.content}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            item["tool_calls"] = tool_calls
        return item
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": getattr(message, "tool_call_id", None),
        }
    return None


def log_llm_context(session_id: str, stage: str, messages, log_path: Path):
    if not env_flag("LOG_LLM_CONTEXT", False):
        return

    records = []
    for message in messages:
        item = message_role_content(message)
        if item is not None:
            records.append(item)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "stage": stage,
        "messages": records,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")
