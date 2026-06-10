"""Unified terminal output formatting for AutoMD.

Usage: import nodes.print_utils as pu; then use pu.step(...), pu.ok(...), etc.
Call pu.set_log_file(f) once in node.py to enable pu.debug() to log to file.
"""

from __future__ import annotations

import shutil
import textwrap
import unicodedata
from typing import Callable, TextIO

_log_file: TextIO | None = None
_web_mode: bool = False
_web_sink: Callable[[str], None] | None = None


def set_log_file(f: TextIO) -> None:
    global _log_file
    _log_file = f


def set_web_mode(on: bool) -> None:
    global _web_mode
    _web_mode = on


def set_web_sink(sink: Callable[[str], None] | None) -> None:
    """Optional sink for pushing every emitted line to a web transport."""
    global _web_sink
    _web_sink = sink


def _emit(text: str) -> None:
    """Write to terminal (unless web mode) AND log file."""
    if not _web_mode:
        print(text)
    if _web_sink:
        _web_sink(text)
    if _log_file:
        _log_file.write(text + "\n")
        _log_file.flush()


def debug(msg: str) -> None:
    """Write to log file only — never appears on terminal."""
    if _log_file:
        _log_file.write(f"  [debug] {msg}\n")
        _log_file.flush()


# ═══════════════════════════════════════════════════════════════════
#  Terminal width helpers — CJK-aware display-width calculation
# ═══════════════════════════════════════════════════════════════════

def _display_width(text: str) -> int:
    """Return the display width of *text* in terminal columns.

    CJK characters (East Asian Width ``W`` or ``F``) occupy 2 columns;
    everything else occupies 1.
    """
    w = 0
    for ch in text:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
    return w


def _term_width() -> int:
    """Return the current terminal width in columns.

    Uses ``shutil.get_terminal_size()`` which reads ``sys.__stdout__``
    directly, so it works correctly even when ``sys.stdout`` is replaced
    by the ``_Tee`` wrapper in ``node.py``.
    """
    try:
        return shutil.get_terminal_size().columns
    except (ValueError, OSError):
        return 80


def _ljust_display(text: str, width: int) -> str:
    """Left-justify *text* so its display width equals *width*."""
    dw = _display_width(text)
    if dw >= width:
        return text
    return text + " " * (width - dw)


def _center_display(text: str, width: int) -> str:
    """Center *text* so its display width equals *width*."""
    dw = _display_width(text)
    if dw >= width:
        return text
    left = (width - dw) // 2
    right = width - dw - left
    return " " * left + text + " " * right


# ── Node-level flow steps ──────────────────────────────────────────

def step(label: str, detail: str = "") -> None:
    """Main flow step. *label* is a short Chinese name, *detail* a one-line result."""
    if detail:
        _emit(f"▶ {_ljust_display(label, 16)} {detail}")
    else:
        _emit(f"▶ {label}")


def ok(label: str, detail: str = "") -> None:
    """Sub-step success."""
    if detail:
        _emit(f"  ✅ {label}: {detail}")
    else:
        _emit(f"  ✅ {label}")


def warn(msg: str) -> None:
    _emit(f"  ⚠️  {msg}")


def fail(msg: str) -> None:
    _emit(f"  ❌ {msg}")


def info(msg: str) -> None:
    _emit(f"  ℹ️  {msg}")


# ── Section separators ─────────────────────────────────────────────

def section(title: str) -> None:
    """Print a separator line with *title* centered."""
    w = min(_term_width() - 4, 60)
    dw = _display_width(title)
    left = (w - dw) // 2
    right = w - dw - left
    _emit(f"\n{'─' * left} {title} {'─' * right}")


# ── User interaction ───────────────────────────────────────────────

def prompt_box(title: str, body: str) -> None:
    """Display a clean box for user interrupt prompts.

    The box adapts to terminal width (max 80) and correctly accounts
    for CJK character widths.
    """
    term_w = _term_width()
    content_w = min(term_w - 4, 80)  # content area (inside borders)

    # Top border: ┌─ title ──...──┐
    top_prefix = f"┌─ {title} "
    dashes = "─" * max(0, content_w - _display_width(title) - 1)
    _emit(f"\n{top_prefix}{dashes}┐")

    for raw_line in body.strip().split("\n"):
        _emit(f"│ {_ljust_display(raw_line, content_w)} │")

    # Bottom border: └──...──┘
    _emit(f"└{'─' * (content_w + 2)}┘")


# ── Final report ───────────────────────────────────────────────────

_STATE_GROUPS: list[tuple[str, list[str]]] = [
    ("蛋白", ["protein_pdb_id", "protein_raw_pdb", "protein_clean_pdb",
              "protein_filtered_pdb", "protein_receptor_pdbqt",
              "protein_prmtop", "protein_inpcrd"]),
    ("配体", ["ligand_smiles", "ligand_input_file", "ligand_mol2",
              "ligand_frcmod", "ligand_prmtop", "ligand_inpcrd", "ligand_pdbqt"]),
    ("对接", ["docked_ligand_pdb", "docked_ligand_pdbqt", "docking_result"]),
    ("MD", ["md_duration_ns", "md_trajectory", "md_result"]),
    ("分析", ["analysis_result"]),
]


def _shorten_path(value: str) -> str:
    """Show only the last 3 path segments for readability."""
    if "/" not in value:
        return value
    parts = value.split("/")
    if len(parts) <= 4:
        return value
    return ".../" + "/".join(parts[-3:])


def report(state: dict) -> None:
    """Print a grouped final-report from the full state dict.

    The report box adapts to terminal width (max 80) and correctly
    accounts for CJK character widths in headers and values.
    """
    term_w = _term_width()
    W = min(term_w - 4, 80)

    _emit(f"\n╔{'═' * W}╗")
    _emit(f"║{_center_display('执 行 完 成', W)}║")

    task = state.get("raw_task", "")
    route = state.get("route", "")
    duration_ns = state.get("md_duration_ns", "")
    if task:
        short_task = textwrap.shorten(str(task), 50)
        extras = []
        if route:
            extras.append(f"路由={route}")
        if duration_ns:
            extras.append(f"MD={duration_ns}ns")
        header = f"任务: {short_task}"
        if extras:
            header += f"  ({', '.join(extras)})"
        _emit(f"╠{'═' * W}╣")
        _emit(f"║  {_ljust_display(header, W - 2)}║")

    for group_name, keys in _STATE_GROUPS:
        items = [(k, state.get(k)) for k in keys if state.get(k)]
        if not items:
            continue
        _emit(f"╠{'═' * W}╣")
        _emit(f"║  {group_name}{' ' * (W - 2 - _display_width(group_name))}║")
        for k, v in items:
            val = _shorten_path(str(v)) if v else ""
            line = f"    {k}: {val}"
            if _display_width(line) > W:
                trimmed = ""
                tw = 0
                for ch in line:
                    cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
                    if tw + cw > W - 3:
                        trimmed += "..."
                        break
                    trimmed += ch
                    tw += cw
                line = trimmed
            _emit(f"║ {_ljust_display(line, W)}║")

    _emit(f"╚{'═' * W}╝")
