from __future__ import annotations

import re

_LEAKED_PLACEHOLDER_RE = re.compile(r"\[shell命令\]|\[shell exit=\d+\]|\[已读取手册 [^\]]+\]")
_DSML_TAG_RE = re.compile(r"</?｜｜DSML｜｜[^>]*>|</?(?:｜|\|)*DSML(?:｜|\|)*[^>]*>")


def sanitize_terminal_text(text: str) -> str:
    """Normalize terminal input to avoid invalid UTF-8/surrogate errors.

    Some terminals can leave control bytes or lone surrogate code points when
    users edit CJK text. This function removes those artifacts before the text
    is sent to the API client.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    out = []
    for ch in text:
        code = ord(ch)

        # Apply basic backspace semantics for stray terminal artifacts.
        if ch in ("\b", "\x7f"):
            if out:
                out.pop()
            continue

        # Drop lone surrogate code points that break UTF-8 serialization.
        if 0xD800 <= code <= 0xDFFF:
            continue

        # Keep printable chars and common whitespace, discard other controls.
        if code < 32 and ch not in ("\n", "\r", "\t"):
            continue

        out.append(ch)

    cleaned = "".join(out)
    # Final defensive normalization.
    return cleaned.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def sanitize_summary_output(text: str) -> str:
    """Remove leaked placeholder tokens from summary LLM output."""
    if not text:
        return ""
    return _LEAKED_PLACEHOLDER_RE.sub("", text).strip()


def strip_dsml_text(text: str) -> str:
    """Remove leaked DSML protocol fragments from model-visible text.

    Keep the inner content and only strip the protocol markers so the user
    still sees a readable assistant message.
    """
    if not text:
        return ""
    cleaned = _DSML_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned
