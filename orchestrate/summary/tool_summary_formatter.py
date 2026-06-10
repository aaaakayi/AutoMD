"""Tool result summary formatters for AutoMD summary_stream.

Provides per-tool human-readable, truncated summaries intended for
inclusion in the `summary_stream` transcript. Designed to be conservative
about length and robust to varying tool output formats.
"""
from __future__ import annotations

import re
from typing import Any, Dict


def _smart_truncate_text(text: str, max_chars: int = 2000, head_lines: int = 10, tail_lines: int = 10) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    if len(lines) <= head_lines + tail_lines + 2:
        return text[:max_chars]
    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    return "\n".join(head) + f"\n...（中间省略 {len(lines)-head_lines-tail_lines} 行）...\n" + "\n".join(tail)


def format_run_shell_command(args: Dict[str, Any], content: str) -> str:
    cmd = args.get("command") if isinstance(args, dict) else None
    cwd = args.get("cwd") if isinstance(args, dict) else None

    # Try to detect exit code and output block in content
    exit_code = None
    m = re.search(r"exit_code[:=]\s*(\d+)", content)
    if m:
        exit_code = m.group(1)
    m2 = re.search(r"\[shell exit=(\d+)\]", content)
    if m2:
        exit_code = m2.group(1)

    out = []
    out.append("助手执行shell命令:")
    if cmd:
        out.append(f"  命令: {cmd if len(cmd) <= 200 else cmd[:200] + '...'}")
    if cwd:
        out.append(f"  工作目录: {cwd}")
    if exit_code is not None:
        out.append(f"  结果: [exit {exit_code}]")
    else:
        out.append("  结果:")

    trimmed = _smart_truncate_text(content or "(无输出)")
    for line in trimmed.splitlines():
        out.append(f"    {line}")

    return "\n".join(out)


def format_pymol_execute(args: Dict[str, Any], content: str) -> str:
    cli_list = args.get("cli_list") or args.get("CLI_LIST") or []
    out = []
    out.append("助手执行PyMOL CLI命令:")
    if isinstance(cli_list, list) and cli_list:
        out.append("  命令列表（按顺序）:")
        for idx, c in enumerate(cli_list, start=1):
            cstr = c if isinstance(c, str) else str(c)
            out.append(f"    {idx}. {cstr if len(cstr) <= 300 else cstr[:300] + '...'}")
    else:
        out.append("  （无命令详情）")

    # Attempt to extract a short summary like "5 成功, 0 失败"
    m = re.search(r"命令执行[:：]\s*(\d+)\s*成功[,，]\s*(\d+)\s*失败", content)
    if m:
        out.append(f"  执行结果: {m.group(1)} 条成功, {m.group(2)} 条失败")
    else:
        # fallback: include content trimmed
        trimmed = _smart_truncate_text(content or "")
        if trimmed:
            out.append("  执行结果: \n" + "\n".join(f"    {l}" for l in trimmed.splitlines()))
    return "\n".join(out)


def format_list_outputs(args: Dict[str, Any], content: str) -> str:
    out = ["助手列出输出目录:"]
    content = (content or "").strip()
    if not content:
        out.append("  结果: 目录为空")
        return "\n".join(out)
    # If content is a single-line listing or multiple paths, show first 50 lines
    trimmed = _smart_truncate_text(content, max_chars=2000, head_lines=50, tail_lines=0)
    out.append("  结果:")
    out.extend(f"    {l}" for l in trimmed.splitlines())
    return "\n".join(out)


def format_default(args: Dict[str, Any], content: str) -> str:
    return _smart_truncate_text((content or "").strip(), max_chars=1000)


def format_tool_summary(tool_name: str, args: Dict[str, Any], content: str) -> str:
    try:
        if tool_name == "run_shell_command":
            return format_run_shell_command(args or {}, content or "")
        if tool_name == "pymol_execute":
            return format_pymol_execute(args or {}, content or "")
        if tool_name == "list_outputs":
            return format_list_outputs(args or {}, content or "")
    except Exception:
        # fall through
        pass
    return format_default(args or {}, content or "")
