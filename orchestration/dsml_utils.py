"""Centralized DSML detection, extraction, and sanitization utilities.

DSML (DeepSeek Markup Language) is a pseudo-protocol that DeepSeek models
sometimes output instead of standard OpenAI-compatible tool_calls. The tags
use U+FF5C (FULLWIDTH VERTICAL LINE) as a delimiter before "DSML".
"""

import json
import re
from typing import Any, Dict, List, Tuple

# ── Regex patterns ──────────────────────────────────────────────────────────
# U+FF5C = FULLWIDTH VERTICAL LINE (the character used in DSML markup)
_FF = "｜"
_FF_SEQ = rf"{_FF}+"

# Matches <DSML|function_calls>...</DSML|function_calls> or <DSML|tool_calls>...</DSML|tool_calls>
_DSML_BLOCK_RE = re.compile(
    rf"<{_FF_SEQ}DSML{_FF_SEQ}(?:function_calls|tool_calls)\s*>[\s\S]*?</{_FF_SEQ}DSML{_FF_SEQ}(?:function_calls|tool_calls)\s*>",
)

# Matches <DSML|invoke name="TOOL_NAME"> body </DSML|invoke>
_DSML_INVOKE_RE = re.compile(
    rf"<{_FF_SEQ}DSML{_FF_SEQ}invoke\s+name=\"(?P<name>[^\"]+)\"\s*>\s*(?P<body>.*?)\s*</{_FF_SEQ}DSML{_FF_SEQ}invoke\s*>",
    re.DOTALL,
)

# Matches <DSML|parameter name="KEY">VALUE</DSML|parameter>
_DSML_PARAM_RE = re.compile(
    rf"<{_FF_SEQ}DSML{_FF_SEQ}parameter\s+name=\"(?P<key>[^\"]+)\"[^>]*>(?P<value>.*?)</{_FF_SEQ}DSML{_FF_SEQ}parameter\s*>",
    re.DOTALL,
)

# Matches any multi-line DSML tag (for cleanup of orphaned tags)
_DSML_TAG_RE = re.compile(rf"<{_FF_SEQ}DSML{_FF_SEQ}[^\n>]*>[\s\S]*?</{_FF_SEQ}DSML{_FF_SEQ}[^\n>]*>")

# Matches stray single-line DSML tags
_DSML_SINGLE_LINE_RE = re.compile(rf"^\s*<{_FF_SEQ}DSML{_FF_SEQ}[^\n]*$", re.MULTILINE)

# Fast detection sentinel — "DSML" alone is enough since the project domain
# (molecular dynamics) never uses this string naturally.
_DSML_SENTINEL = f"{_FF}DSML{_FF}"


def has_dsml(text: str) -> bool:
    """Return True if text contains DSML pseudo-protocol markup."""
    if not isinstance(text, str) or not text:
        return False
    return _DSML_SENTINEL in text


def extract_dsml_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Parse DSML markup and return list of (tool_name, args_dict) tuples.

    Values remain as raw strings — callers should coerce types before use.
    """
    calls: List[Tuple[str, Dict[str, Any]]] = []
    if not has_dsml(text):
        return calls

    for match in _DSML_INVOKE_RE.finditer(text):
        tool_name = (match.group("name") or "").strip()
        body = match.group("body") or ""
        args: Dict[str, Any] = {}
        for p in _DSML_PARAM_RE.finditer(body):
            key = (p.group("key") or "").strip()
            value = (p.group("value") or "").strip()
            args[key] = value
        if tool_name:
            calls.append((tool_name, args))

    return calls


def strip_dsml(text: str) -> str:
    """Remove all DSML markup, replacing blocks with a brief notice."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""

    cleaned = _DSML_BLOCK_RE.sub("[DSML 工具调用已过滤]", text)
    cleaned = _DSML_TAG_RE.sub("", cleaned)
    cleaned = _DSML_SINGLE_LINE_RE.sub("", cleaned)
    # Collapse multiple blank lines from removed blocks
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def coerce_value(value: str) -> Any:
    """Coerce a string value to bool/int/float if possible, otherwise keep as str."""
    v = value.strip()
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def parse_dsml_to_function_calls(text: str) -> List[Dict[str, Any]]:
    """Parse DSML markup into FunctionCall-compatible dicts.

    Each returned dict has keys: id, name, arguments (JSON string).
    These can be used to construct autogen_core.models.FunctionCall objects.
    """
    raw_calls = extract_dsml_calls(text)
    result: List[Dict[str, Any]] = []
    for idx, (tool_name, args) in enumerate(raw_calls):
        coerced = {k: coerce_value(v) for k, v in args.items()}
        result.append(
            {
                "id": f"dsml_{idx}",
                "name": tool_name,
                "arguments": json.dumps(coerced, ensure_ascii=False),
            }
        )
    return result
