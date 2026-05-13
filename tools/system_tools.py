import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from tools.shared import success, failed, ToolResult


_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DANGEROUS_COMMAND_PATTERNS = (
    r"\brm\s+-rf\b",
    r"\bdel\s+/s\b",
    r"\bdel\s+/q\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
)


def _is_dangerous_command(command: str) -> bool:
    normalized = (command or "").lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _DANGEROUS_COMMAND_PATTERNS)


def _resolve_path(path: str, *, allow_outside_project: bool) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (_PROJECT_ROOT / p).resolve()
    else:
        p = p.resolve()

    if not allow_outside_project:
        try:
            p.relative_to(_PROJECT_ROOT)
        except ValueError as e:
            raise ValueError(f"不允许访问项目目录外的路径: {p}") from e
    return p


def run_shell_command(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 20000,
    env: Optional[dict] = None,
) -> ToolResult:
    """
    执行任意 Shell 命令并返回输出（stdout+stderr）、退出码与工作目录信息。

    说明：
    - 在 Windows 下使用 PowerShell/CMD 的语法；在 Linux/WSL 下使用 /bin/sh 语法。
    - 本函数是“任意命令执行”，请只在受信任环境使用。
    """
    if not command or not command.strip():
        return failed(errors=["command 不能为空。"])

    if _is_dangerous_command(command):
        return failed(errors=[f"命令被安全策略拒绝：检测到潜在危险操作 - {command}"])

    resolved_cwd = None
    if cwd:
        resolved_cwd = str(_resolve_path(cwd, allow_outside_project=False))
    else:
        resolved_cwd = str(_PROJECT_ROOT)

    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=resolved_cwd,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        out = out[-max_output_chars:] if max_output_chars > 0 else out
        return failed(
            errors=[f"命令执行超时（>{timeout_seconds}s）"],
            data={"command": command, "partial_output": out},
        )
    except Exception as e:
        return failed(errors=[f"命令执行失败：{type(e).__name__}: {e}"])

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout + (("\n" if stdout and stderr else "") + stderr if stderr else "")
    if max_output_chars > 0 and len(combined) > max_output_chars:
        combined = combined[-max_output_chars:]

    return success(
        data={
            "exit_code": completed.returncode,
            "cwd": resolved_cwd or os.getcwd(),
            "output": combined,
        }
    )


def read_text_file(
    path: str,
    max_chars: int = 20000,
    allow_outside_project: bool = False,
    encoding: str = "utf-8",
) -> ToolResult:
    """读取文件内容并返回（可截断）。"""
    try:
        p = _resolve_path(path, allow_outside_project=allow_outside_project)
        if not p.exists():
            return failed(errors=[f"文件不存在: {p}"])
        if p.is_dir():
            return failed(errors=[f"目标是目录而不是文件: {p}"])
        content = p.read_text(encoding=encoding, errors="replace")
        if max_chars > 0 and len(content) > max_chars:
            content = content[-max_chars:]
        return f"文件内容（{p}）：\n{content}"
    except Exception as e:
        return failed(errors=[f"读取文件失败: {type(e).__name__}: {e}"])


def write_text_file(
    path: str,
    content: str,
    mode: str = "w",
    allow_outside_project: bool = False,
    encoding: str = "utf-8",
) -> ToolResult:
    """
    写入内容到文件。

    Args:
        path: 目标路径（相对路径默认相对项目根目录）
        content: 要写入的文本
        mode: 'w' 覆盖写入，'a' 追加写入
    """
    if mode not in {"w", "a"}:
        return failed(errors=["mode 只能是 'w' 或 'a'。"])
    try:
        p = _resolve_path(path, allow_outside_project=allow_outside_project)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open(mode, encoding=encoding, errors="replace", newline="\n") as f:
            f.write(content or "")
        return success(data={"path": str(p), "mode": mode, "chars": len(content or '')})
    except Exception as e:
        return failed(errors=[f"写入文件失败: {type(e).__name__}: {e}"])


def read_error_report(
    *,
    log_path: Optional[str] = None,
    raw_error_text: Optional[str] = None,
    max_chars: int = 20000,
    allow_outside_project: bool = False,
) -> ToolResult:
    """
    读取“相关报错”。

    用法二选一：
    - 提供 log_path：读取日志文件/终端输出文件
    - 提供 raw_error_text：直接传入报错文本（用于把报错作为上下文返回给 agent）
    """
    if (log_path is None) == (raw_error_text is None):
        return failed(errors=["请二选一提供 log_path 或 raw_error_text。"])

    if log_path is not None:
        return read_text_file(
            log_path,
            max_chars=max_chars,
            allow_outside_project=allow_outside_project,
        )

    text = raw_error_text or ""
    if max_chars > 0 and len(text) > max_chars:
        text = text[-max_chars:]
    return success(data={"error_text": text})

