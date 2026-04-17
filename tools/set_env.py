import subprocess
import sys
import shutil
import os
from typing import List, Optional


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
    


def setup_environment(packages: Optional[List[str]] = None, method: str = "conda", target_env: Optional[str] = None) -> str:
    """
    自动安装计算化学依赖（如 AmberTools）。

    Args:
        packages: 要安装的包列表，默认安装 AmberTools 核心包 "ambertools"
        method: 安装方法，可选 "conda"、"pip"（仅适用于 Python 包）
        target_env: conda 目标环境名（可选）。不传时优先当前激活环境。

    Returns:
        安装结果描述字符串。
    """
    if packages is None or not isinstance(packages, list) or not packages:
        return "未指定明确的 packages 列表，本次不执行安装。请先确认缺失依赖后，再显式传入 packages。"

    method = method.lower().strip()

    try:
        if method == "conda":
            # 确保 conda 可用
            conda_cmd = shutil.which("conda")
            if not conda_cmd:
                return "未找到 conda 命令。请先安装 Anaconda 或 Miniconda。"

            target_args = _resolve_conda_target_args(target_env=target_env)
            if not target_args:
                return "未检测到激活的 conda 环境。请先激活 AutoMD 环境（例如 conda activate AutoMD）后再运行。"

            # 安装指定包
            cmd = [conda_cmd, "install", "-c", "conda-forge", "-y"] + target_args + packages
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return f"成功安装到当前 conda 环境: {', '.join(packages)}。"
            else:
                err = result.stderr.strip() or result.stdout.strip()
                return f"conda 安装失败: {err}"

        elif method == "pip":
            # 对于 Python 包，可安装 openmm、rdkit、pdbfixer 等
            cmd = [sys.executable, "-m", "pip", "install"] + packages
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return f"成功安装 Python 包: {', '.join(packages)}。当前解释器: {sys.executable}"
            else:
                err = result.stderr.strip() or result.stdout.strip()
                return f"pip 安装失败: {err}"

        else:
            return f"不支持的安装方法: {method}。请使用 conda 或 pip。"

    except Exception as e:
        return f"环境配置失败: {str(e)}"


def remove_packages(packages: List[str], method: str = "conda", target_env: Optional[str] = None) -> str:
    """
    从当前 conda 环境或指定环境中移除包。

    Args:
        packages: 要移除的包名列表，如 ["ambertools", "openbabel"]
        method: 目前仅支持 "conda"（pip 移除可后续添加）
        target_env: conda 目标环境名（可选）。不传时优先当前激活环境。

    Returns:
        操作结果字符串。
    """
    if not packages:
        return "packages 列表不能为空。"

    method = method.lower().strip()
    if method != "conda":
        return f"目前仅支持 conda 移除，不支持的 method: {method}"

    conda_cmd = shutil.which("conda")
    if not conda_cmd:
        return "未找到 conda 命令。"

    # 复用之前的 _resolve_conda_target_args 函数
    from your_setup_module import _resolve_conda_target_args
    target_args = _resolve_conda_target_args(target_env)
    if not target_args:
        return "未检测到激活的 conda 环境。请先激活环境（如 conda activate AutoMD）后再操作。"

    cmd = [conda_cmd, "remove", "-y"] + target_args + packages
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return f"成功移除包: {', '.join(packages)}。"
        else:
            err = result.stderr.strip() or result.stdout.strip()
            return f"移除失败: {err}"
    except subprocess.TimeoutExpired:
        return "移除操作超时（超过300秒）。"
    except Exception as e:
        return f"移除过程中发生异常: {str(e)}"