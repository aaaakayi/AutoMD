import subprocess
import sys
import shutil
import os
from typing import List, Optional

from tools.shared import success, degraded, failed, ToolResult


def _resolve_conda_target_args(target_env: Optional[str] = None) -> List[str]:
    """解析 conda 安装目标，优先当前激活环境，避免误装到 base。"""
    if target_env:
        return ["-n", target_env]

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        return ["--prefix", conda_prefix]

    conda_env_name = os.environ.get("CONDA_DEFAULT_ENV")
    if conda_env_name:
        return ["-n", conda_env_name]

    return []


def setup_environment(
    packages: Optional[List[str]] = None,
    method: str = "conda",
    target_env: Optional[str] = None,
) -> ToolResult:
    """
    自动安装计算化学依赖（如 AmberTools）。

    Args:
        packages: 要安装的包列表，默认安装 AmberTools 核心包 "ambertools"
        method: 安装方法，可选 "conda"、"pip"（仅适用于 Python 包）
        target_env: conda 目标环境名（可选）。不传时优先当前激活环境。

    Returns:
        ToolResult，status="success"|"degraded"|"failed"
    """
    if packages is None or not isinstance(packages, list) or not packages:
        return failed(
            errors=["未指定明确的 packages 列表，本次不执行安装。请先确认缺失依赖后，再显式传入 packages。"],
        )

    method = method.lower().strip()

    # ── L0: conda ──────────────────────────────────────────────
    if method == "conda":
        conda_cmd = shutil.which("conda")
        if not conda_cmd:
            # 降级到 pip
            if shutil.which("pip") or True:
                return _install_via_pip(packages)
            return failed(errors=["未找到 conda 命令，也未能降级到 pip。请先安装 Anaconda 或 Miniconda。"])

        target_args = _resolve_conda_target_args(target_env=target_env)
        if not target_args:
            return failed(errors=["未检测到激活的 conda 环境。请先激活 AutoMD 环境（例如 conda activate AutoMD）后再运行。"])

        cmd = [conda_cmd, "install", "-c", "conda-forge", "-y"] + target_args + packages
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return failed(errors=["conda 安装超时（>600s）"])

        if result.returncode == 0:
            return success(data=f"成功安装到 conda 环境: {', '.join(packages)}。")

        # L0 conda 失败 → L1: pip
        err = result.stderr.strip() or result.stdout.strip()
        pip_result = _install_via_pip(packages)
        pip_result.degradation.insert(0, f"conda→pip (conda error: {err[:200]})")
        return pip_result

    # ── L0: pip ─────────────────────────────────────────────────
    elif method == "pip":
        return _install_via_pip(packages)

    else:
        return failed(errors=[f"不支持的安装方法: {method}。请使用 conda 或 pip。"])


def _install_via_pip(packages: List[str]) -> ToolResult:
    """L1 降级: 使用 pip 安装。"""
    cmd = [sys.executable, "-m", "pip", "install"] + packages
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return failed(errors=["pip 安装超时（>300s）"])
    except Exception as e:
        return failed(errors=[f"pip 安装异常: {str(e)}"])

    if result.returncode == 0:
        return degraded(
            data=f"降级使用 pip 安装: {', '.join(packages)}。当前解释器: {sys.executable}",
            degradation=["conda→pip"],
            warnings=["pip 安装可能缺少非 Python 依赖（如 ambertools 的二进制组件）"],
        )
    else:
        err = result.stderr.strip() or result.stdout.strip()
        return failed(
            errors=[f"pip 安装也失败: {err}"],
            degradation=["conda→pip"],
        )


def remove_packages(
    packages: List[str],
    method: str = "conda",
    target_env: Optional[str] = None,
) -> ToolResult:
    """
    从当前 conda 环境或指定环境中移除包。

    Args:
        packages: 要移除的包名列表，如 ["ambertools", "openbabel"]
        method: 目前仅支持 "conda"
        target_env: conda 目标环境名（可选）。不传时优先当前激活环境。

    Returns:
        ToolResult
    """
    if not packages:
        return failed(errors=["packages 列表不能为空。"])

    method = method.lower().strip()
    if method != "conda":
        return failed(errors=[f"目前仅支持 conda 移除，不支持的 method: {method}"])

    conda_cmd = shutil.which("conda")
    if not conda_cmd:
        return failed(errors=["未找到 conda 命令。"])

    target_args = _resolve_conda_target_args(target_env)
    if not target_args:
        return failed(errors=["未检测到激活的 conda 环境。请先激活环境（如 conda activate AutoMD）后再操作。"])

    cmd = [conda_cmd, "remove", "-y"] + target_args + packages
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return success(data=f"成功移除包: {', '.join(packages)}。")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            return failed(errors=[f"移除失败: {err}"])
    except subprocess.TimeoutExpired:
        return failed(errors=["移除操作超时（超过300秒）。"])
    except Exception as e:
        return failed(errors=[f"移除过程中发生异常: {str(e)}"])
