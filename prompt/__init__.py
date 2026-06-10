"""AutoMD LLM prompt loader.

Loads prompt sections from .md files in this directory. Sections are delimited
by HTML comments:

    <!-- SECTION_NAME -->
    section text, can contain literal { and } safely
    (no str.format() is applied at load time)

Sections may contain {variable} placeholders for the caller to substitute via
str.format() or f-string after loading.

Usage:
    from prompt import load
    system = load("chat", "TOOL_LLM_SYSTEM")              # raw
    user = load("chat", "SUMMARY_REQUIREMENTS").format(   # with vars
        session_id="abc123",
    )
"""
import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent

_SECTION_RE = re.compile(r"<!--\s*([A-Z][A-Z0-9_]*)\s*-->")
_META_COMMENT_RE = re.compile(r"<!--[^>]*-->")


@lru_cache(maxsize=None)
def _load_sections(module: str) -> dict[str, str]:
    """Parse all sections from prompt/{module}.md into a dict.

    Section markers are HTML comments matching <!-- SECTION_NAME -->. Lines
    containing meta comments (any <!-- ... --> line that is NOT a section
    marker) are stripped from section body. This lets prompt authors add
    notes like <!-- 变量: {sid} --> right under a section header.
    """
    path = PROMPT_DIR / f"{module}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt module not found: {path}. "
            f"Available: {sorted(p.stem for p in PROMPT_DIR.glob('*.md'))}"
        )
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip("\n")
            current_name = m.group(1)
            current_lines = []
            continue
        if current_name is None:
            continue  # skip preamble before any section
        # Strip any <!-- ... --> meta comment line from body
        if _META_COMMENT_RE.fullmatch(line.strip()):
            continue
        current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip("\n")
    return sections


def load(module: str, section: str) -> str:
    """Load prompt section text. No variable substitution is performed.

    Caller can do `.format(**kwargs)` or f-string if the section has
    {variable} placeholders.
    """
    sections = _load_sections(module)
    if section not in sections:
        available = sorted(sections.keys())
        raise KeyError(
            f"Section {section!r} not found in prompt/{module}.md. "
            f"Available: {available}"
        )
    return sections[section]


def list_sections(module: str) -> list[str]:
    """Return all section names defined in prompt/{module}.md."""
    return sorted(_load_sections(module).keys())


def list_modules() -> list[str]:
    """Return all .md prompt modules in this directory."""
    return sorted(p.stem for p in PROMPT_DIR.glob("*.md"))


def clear_cache() -> None:
    """Clear the section cache. Useful after editing .md files in dev."""
    _load_sections.cache_clear()


__all__ = ["load", "list_sections", "list_modules", "clear_cache", "PROMPT_DIR"]
