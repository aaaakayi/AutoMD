"""
AutoMD 工具共享配置与统一返回类型。

提供:
- ToolResult: 统一工具返回类型，标注成功/降级/失败状态与降级路径
- 共享路径常量: PROJECT_ROOT, MGLTools 路径, conda 环境名
- 工厂函数: success(), degraded(), failed()
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# conda env that holds the heavy scientific tools (vina / obabel / tleap /
# antechamber / parmchk2 / acpype / pymol / cpptraj / ambertools ...).
# All subprocess calls into those tools MUST go through run_in_conda_env so the
# main process PATH does not matter.
DEFAULT_CONDA_ENV = "AutoMD"

# ============================================================================
# 共享路径常量
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_ROOT = PROJECT_ROOT / "temp"

MGLTOOLS_ROOT = PROJECT_ROOT / "dock_tools" / "mgltools" / "mgltools_x86_64Linux2_1.5.7"
MGLTOOLS_PCKGS_PATH = MGLTOOLS_ROOT / "MGLToolsPckgs"
CONDA_MGLTOOLS_ENV = "mgltools"

# ============================================================================
# MGLTools 脚本路径
# ============================================================================

def _mgltools_script(*parts: str) -> Path:
    return MGLTOOLS_PCKGS_PATH.joinpath(*parts)


PREPARE_RECEPTOR4_SCRIPT = _mgltools_script("AutoDockTools", "Utilities24", "prepare_receptor4.py")
PREPARE_LIGAND4_SCRIPT = _mgltools_script("AutoDockTools", "Utilities24", "prepare_ligand4.py")

# ============================================================================
# ToolResult
# ============================================================================


@dataclass
class ToolResult:
    """统一的工具返回类型。

    Attributes:
        status: "success" | "degraded" | "failed"
        data: 实际返回值（dict、str 等）
        degradation: 降级步骤列表，如 ["antechamber bcc→gas", "MGLTools→OpenBabel"]
        errors: 遇到的错误列表（即使已恢复也会记录）
        warnings: 非致命警告
    """

    status: str
    data: Any = None
    degradation: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    env_packages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("success", "degraded")

    def format_for_agent(self) -> str:
        """格式化为 Agent 可读的文本。"""
        lines = [f"[{self.status.upper()}]"]
        if self.degradation:
            lines.append(f"降级路径: {' → '.join(self.degradation)}")
        if self.warnings:
            lines.append(f"警告: {'; '.join(self.warnings)}")
        if self.env_packages:
            lines.append(f"环境依赖: {'; '.join(self.env_packages)}")
        if self.errors:
            lines.append(f"错误(已处理): {'; '.join(self.errors)}")
        if self.data is not None:
            if isinstance(self.data, dict):
                for k, v in self.data.items():
                    if v is not None:
                        lines.append(f"  {k}: {v}")
            else:
                lines.append(str(self.data))
        return "\n".join(lines)


def success(data: Any = None, warnings: list[str] | None = None, env_packages: list[str] | None = None) -> ToolResult:
    return ToolResult(status="success", data=data, warnings=warnings or [], env_packages=env_packages or [])


def degraded(
    data: Any = None,
    *,
    degradation: list[str] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    env_packages: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        status="degraded",
        data=data,
        degradation=degradation or [],
        errors=errors or [],
        warnings=warnings or [],
        env_packages=env_packages or [],
    )


def failed(
    data: Any = None,
    *,
    errors: list[str] | None = None,
    degradation: list[str] | None = None,
    warnings: list[str] | None = None,
    env_packages: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        status="failed",
        data=data,
        errors=errors or [],
        degradation=degradation or [],
        warnings=warnings or [],
        env_packages=env_packages or [],
    )


# ============================================================================
# Tool-layer debug logging bridge
# ============================================================================

_tool_debug_writer = None


def set_tool_debug_writer(fn) -> None:
    """Inject a debug-logging function from the node layer.

    Called once in ``node.py`` to connect ``pu.debug`` so that tool-internal
    debug messages appear in the run log file.
    """
    global _tool_debug_writer
    _tool_debug_writer = fn


def tool_debug(msg: str) -> None:
    """Log a tool-internal event (e.g. which fallback tier was used).

    Messages go to the run log file only — never to the terminal.
    Safe to call before ``set_tool_debug_writer`` (no-op).
    """
    if _tool_debug_writer:
        _tool_debug_writer(msg)


# ============================================================================
# Conda env subprocess runner
# ============================================================================
#
# Why this exists:
#   set_env installs packages into the LangGraph conda env.  But the main
#   process (where the LangGraph server runs) is often started from another
#   env (base / pymol_env / plain system Python).  A child subprocess inherits
#   the *parent's* PATH, so `shutil.which("vina")` in the parent will keep
#   returning None even after `mamba install -n LangGraph vina` succeeded,
#   and the graph loops forever between docking_run -> set_env -> docking_run.
#
# Fix: every external CLI (vina, obabel, tleap, antechamber, parmchk2, acpype,
# pymol, cpptraj, ...) is invoked through `conda run -n LangGraph ...`.  This
# always works as long as the package is installed in the target env, no
# matter which env the parent process lives in.
#
# `shutil.which` is still used as a *fast pre-check* so we can return a
# helpful `env_packages=[...]` error before spending time on the subprocess.
# After the pre-check, the actual invocation goes through conda run.

_CONDA_BIN = shutil.which("conda") or shutil.which("mamba")


def _conda_executable() -> str | None:
    """Find the conda (or mamba) executable on disk.

    Tries PATH first, then a list of common conda install roots so the
    helpers still work when the process is launched from a Python venv or
    directly with a conda-env python binary (no `conda activate`).
    """
    found = shutil.which("conda") or shutil.which("mamba")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/miniconda3/bin/conda"),
        os.path.expanduser("~/anaconda3/bin/conda"),
        os.path.expanduser("~/miniforge3/bin/conda"),
        os.path.expanduser("~/mambaforge/bin/conda"),
        os.path.expanduser("~/miniconda3/bin/mamba"),
        os.path.expanduser("~/miniforge3/bin/mamba"),
        os.path.expanduser("~/mambaforge/bin/mamba"),
        "/opt/conda/bin/conda",
        "/usr/local/bin/conda",
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None


def is_tool_available(tool: str, *, conda_env: str = DEFAULT_CONDA_ENV) -> bool:
    """Return True if *tool* can be found in current PATH or within *conda_env*."""
    if shutil.which(tool):
        return True

    conda = _conda_executable()
    if not conda:
        return False

    import shlex
    tool_q = shlex.quote(tool)

    try:
        r = subprocess.run(
            [conda, "run", "-n", conda_env, "bash", "-lc",
             f"command -v {tool_q} >/dev/null 2>&1 || test -x \"$CONDA_PREFIX/bin/{tool_q}\""],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def run_in_conda_env(
    cmd: Sequence[str],
    *,
    conda_env: str = DEFAULT_CONDA_ENV,
    timeout: int | None = None,
    cwd: str | None = None,
    env: dict | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` inside the named conda env via `conda run -n <env>`.

    Returns a CompletedProcess.  Raises FileNotFoundError if conda is not
    installed at all (caller should map that to env_packages=["conda"]).
    """
    conda = _conda_executable()
    if not conda:
        raise FileNotFoundError(
            "conda/mamba executable not found on PATH; cannot run tools "
            "inside a conda env"
        )
    full_cmd = [conda, "run", "-n", conda_env, *cmd]
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
        input=input_text,
    )
