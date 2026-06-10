from __future__ import annotations

from orchestrate.text.sanitizers import strip_dsml_text


class DSMLStreamFilter:
    """Chunk-safe DSML filter for streaming token outputs.

    Keeps a short tail buffer so DSML tags split across chunks are removed
    before content is emitted to frontend.
    """

    def __init__(self, hold_chars: int = 256):
        self.hold_chars = max(32, hold_chars)
        self._tail = ""

    def feed(self, chunk: str) -> str:
        data = self._tail + (chunk or "")
        if len(data) <= self.hold_chars:
            self._tail = data
            return ""
        emit_src = data[:-self.hold_chars]
        self._tail = data[-self.hold_chars:]
        return strip_dsml_text(emit_src)

    def flush(self) -> str:
        out = strip_dsml_text(self._tail)
        self._tail = ""
        return out
