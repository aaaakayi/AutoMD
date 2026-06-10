from __future__ import annotations
from typing import Literal
import shutil
import subprocess

import os
import sys

# Ensure package/project roots are on sys.path when running this node directly
nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.types import Command, interrupt

from State import AutoMDState
from langgraph.graph import END, START, StateGraph

import nodes.print_utils as pu


 

from pydantic import BaseModel, Field

# robust import of _llm so the module can be executed as a script
try:
    from .common import _llm
except Exception:
    try:
        from nodes.common import _llm
    except Exception:
        import importlib.util, os, sys

        # Ensure package and project roots are in sys.path so sibling imports resolve
        nodes_dir = os.path.dirname(__file__)
        package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
        project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
        if package_root not in sys.path:
            sys.path.insert(0, package_root)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        spec = importlib.util.spec_from_file_location(
            "nodes.common", os.path.join(nodes_dir, "common.py")
        )
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)  # type: ignore
        _llm = common._llm

class EnvSetupResult(BaseModel):
    """环境修复 LLM 的结构化输出。

    设计目标：
    - LLM 只负责判断“该装什么、用什么方式装、需要哪些验证步骤”
    - 代码层根据这些字段拼接成最终 shell 指令
    - 避免 LLM 直接输出一整段不可控脚本
    """

    shell: Literal["bash", "powershell"] = Field(
        default="bash",
        description="最终脚本类型。WSL/Linux 用 bash，Windows 用 powershell。",
    )
    manager: Literal["conda", "mamba", "pip", "uv", "apt", "shell"] = Field(
        default="conda",
        description="主要的环境/包管理方式。",
    )
    conda_channels: list[str] = Field(
        default_factory=list,
        description="conda/mamba 需要的 channel 列表。",
    )
    conda_packages: list[str] = Field(
        default_factory=list,
        description="通过 conda/mamba 安装的包。",
    )
    pip_packages: list[str] = Field(
        default_factory=list,
        description="通过 pip 安装的包。",
    )
    apt_packages: list[str] = Field(
        default_factory=list,
        description="通过 apt 安装的系统包。",
    )
    shell_commands: list[str] = Field(
        default_factory=list,
        description="必须原样执行的 shell 命令片段，按顺序拼接。",
    )
    # verify_commands removed: verification is handled by running the script and
    # inspecting subprocess output.
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="需要导出的环境变量。",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="给执行器或人看的备注，不直接作为 shell 执行。",
    )
    expected_outcome: str = Field(
        default="依赖已安装并可用",
        description="这次环境修复的最终预期结果。",
    )


def build_shell_script(result: EnvSetupResult) -> str:
    """把 EnvSetupResult 拼装成最终 shell 指令。"""

    lines: list[str] = []

    if result.shell == "bash":
        lines.append("#!/usr/bin/env bash")
        lines.append("set -euo pipefail")
    else:
        lines.append("$ErrorActionPreference = 'Stop'")

    lines.append("# install into the fixed current environment")

    for key, value in result.env_vars.items():
        if result.shell == "bash":
            lines.append(f'export {key}="{value}"')
        else:
            lines.append(f'$env:{key} = "{value}"')

    if result.manager in {"conda", "mamba"} and result.conda_packages:
        # LLM 经常写 mamba,但用户机器未必装了 mamba 二进制。
        # conda 26+ 自带 libmamba solver,行为一致,统一用 conda。
        # 显式 -n AutoMD,保证装到正确的 env (避免依赖外层 conda run 注入)。
        install_args: list[str] = ["conda", "install", "-y", "-n", TARGET_CONDA_ENV]
        for channel in result.conda_channels:
            install_args.extend(["-c", channel])
        install_args.extend(result.conda_packages)
        lines.append(" ".join(install_args))

    if result.manager == "apt" and result.apt_packages:
        lines.append("sudo apt-get update")
        lines.append("sudo apt-get install -y " + " ".join(result.apt_packages))

    if result.pip_packages:
        lines.append("python -m pip install -U pip")
        lines.append("python -m pip install " + " ".join(result.pip_packages))

    lines.extend(result.shell_commands)

    lines.append(f'echo "{result.expected_outcome}"')
    return "\n".join(lines)


def build_env_setup_prompt(package_needs: list[str]) -> str:
    """给环境修复 LLM 的提示词模板。"""

    packages = ", ".join(package_needs)
    return f"""
你是一个环境配置专家。
你的任务不是直接输出完整 shell 脚本，而是输出一个 EnvSetupResult 的结构化计划。
当前环境是固定的，脚本必须直接安装到当前激活的 LangGraph/conda 环境中。
当前环境是固定的，脚本会由外层直接在 LangGraph conda 环境中执行。

你需要分析以下缺失依赖：
{packages}

请严格填写这些字段：
- shell: bash 或 powershell
- manager: conda / mamba / pip / uv / apt / shell
- conda_channels: 需要的 channel
- conda_packages: 需要通过 conda/mamba 安装的包
- pip_packages: 需要通过 pip 安装的包
- apt_packages: 需要通过 apt 安装的系统包
- shell_commands: 额外的 shell 片段
- env_vars: 需要导出的环境变量
- notes: 备注
- expected_outcome: 预期结果

约束：
- 优先给出最小修复集，不要泛化成一大堆不必要的包。
- 如果某个依赖更适合 conda，不要放到 pip_packages。
- 不要输出 conda create、conda activate 之类的命令。
- 不要输出解释性自然语言，只输出符合 EnvSetupResult 的结构化内容。
""".strip()

env_setup_llm = _llm.with_structured_output(EnvSetupResult,method="function_calling")

TARGET_CONDA_ENV = "AutoMD"

# 已知包 → conda 安装映射。命中时 set_env 跳过 LLM,直接拼 conda install。
# (channel, conda_pkg_name) — 大多数时候 conda_name == key。
# 注意 "vina": 实际靠 bioconda 的 autodock-vina 包提供 /bin/vina 二进制(1.79MB C++ CLI)。
# 若映射到 conda-forge 的 "vina"(vina-1.2.7),在 autodock-vina 已装的环境里,conda libmamba
# solver 会误判 "vina" spec 已通过 env bin 里同名二进制满足,returncode=0 但啥也没装,
# → docking_run 仍找不到 vina → 死循环。所以 vina 必须映射到 autodock-vina。
KNOWN_PACKAGE_INSTALL: dict[str, tuple[str, str]] = {
    "vina":        ("bioconda",   "autodock-vina"),
    "obabel":      ("conda-forge", "openbabel"),
    "openbabel":   ("conda-forge", "openbabel"),
    "rdkit":       ("conda-forge", "rdkit"),
    "tleap":       ("conda-forge", "ambertools"),
    "ambertools":  ("conda-forge", "ambertools"),
    "sander":      ("conda-forge", "ambertools"),
    "cpptraj":     ("conda-forge", "ambertools"),
    "antechamber": ("conda-forge", "ambertools"),
    "parmchk2":    ("conda-forge", "ambertools"),
    "pymol":       ("conda-forge", "pymol-open-source"),
    "mgltools":    ("bioconda",   "mgltools"),
}


def _strip_code_fences(text: str) -> str:
    """Remove markdown fences if the LLM still returns them."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("\n", 1)[0] if "\n" in cleaned else ""
    lines = [line for line in cleaned.splitlines() if line.strip() != "```"]
    if lines and lines[0].strip().lower() in {"bash", "sh", "powershell"}:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _find_conda_executable() -> str | None:
    """Locate the conda binary.

    Order:
    1. shutil.which("conda")  — works if conda is on PATH (typical after
       `conda activate AutoMD`).
    2. Common conda install roots — works if conda is installed but not on
       PATH (e.g. when running a venv python without `conda activate`).
    """
    found = shutil.which("conda")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/miniconda3/bin/conda"),
        os.path.expanduser("~/anaconda3/bin/conda"),
        os.path.expanduser("~/miniforge3/bin/conda"),
        os.path.expanduser("~/mambaforge/bin/conda"),
        "/opt/conda/bin/conda",
        "/usr/local/bin/conda",
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None


def _run_install_script(script: str) -> subprocess.CompletedProcess[str]:
    """Run the generated script inside the fixed conda environment."""

    cleaned_script = _strip_code_fences(script)
    conda_cmd = _find_conda_executable()
    if conda_cmd:
        command = [conda_cmd, "run", "-n", TARGET_CONDA_ENV, "bash", "-lc", cleaned_script]
    else:
        command = [
            "/bin/bash",
            "-lc",
            f'eval "$(conda shell.bash hook)" && conda activate {TARGET_CONDA_ENV} && {cleaned_script}',
        ]

    return subprocess.run(command, capture_output=True, text=True, timeout=3600)


def _print_progress(message: str) -> None:
    """Print a compact one-line progress update."""

    pu.debug(f"[set_env] {message}")

def set_env(state: AutoMDState) -> Command:
    """
    作为一个独立节点，负责根据 package_needs 生成并执行环境修复脚本
    并处理失败重试逻辑。成功后清空 package_needs 并跳回调用节点。
    """
    package_needs = state.get("package_needs") or []
    if not package_needs:
        raise ValueError("set_env 节点被调用，但 package_needs 为空。请检查前置节点是否正确设置了 package_needs。")

    # ── 熔断：同一组 package_needs 反复触发 set_env 时及时人工介入 ──
    # 避免 LLM 写出的安装脚本实际未生效时陷入 docking_run ↔ set_env 死循环。
    env_setup_attempts: dict = dict(state.get("env_setup_attempts") or {})
    attempt_key = ",".join(sorted(package_needs))
    prior_count = int(env_setup_attempts.get(attempt_key, 0))
    MAX_ENV_SETUP_ATTEMPTS = 2
    if prior_count >= MAX_ENV_SETUP_ATTEMPTS:
        _print_progress(
            f"环境修复熔断：{attempt_key} 已连续触发 {prior_count} 次 set_env，"
            f"超过上限 {MAX_ENV_SETUP_ATTEMPTS}，等待人工介入"
        )
        human_fix = interrupt(
            f"自动修复环境连续失败 {prior_count} 次（{package_needs}）。\n"
            f"可能原因：安装脚本没有真正把包装到 {TARGET_CONDA_ENV} env、"
            f"包名拼错、或者包在当前 channel 不存在。\n"
            f"请手动 `conda activate {TARGET_CONDA_ENV}` 后执行安装，"
            f"完成后输入 'continue' 重试，或输入 'skip' 跳过此步骤。"
        )
        if human_fix.strip().lower() == "continue":
            env_setup_attempts[attempt_key] = 0  # 人工修复成功，重置计数
            return Command(
                update={"package_needs": [], "env_setup_attempts": env_setup_attempts},
                goto=state.get("calling_node")
            )
        pu.warn("用户跳过环境修复，终止流程")
        return Command(
            update={
                "package_needs": [],
                "env_setup_attempts": env_setup_attempts,
                "env_setup_status": "failed",
                "error_message": f"环境修复熔断 ({attempt_key})，用户选择跳过。",
            },
            goto=END
        )

    # 硬编码快速路径:所有缺失包都在 KNOWN_PACKAGE_INSTALL 表里 → 直接拼
    # conda install,跳过 LLM(LLM 经常把包装到错的 env,自检永远过不了)。
    # 显式带 -n AutoMD,防止 conda run -n AutoMD 包装下的 bash 把 install 装到 base env
    # (实测发现某些状态下 bash 启动的 active prefix 不是 AutoMD,导致装包到 base)。
    if all(p in KNOWN_PACKAGE_INSTALL for p in package_needs):
        # 按 channel 分组,每个 channel 一次 install(混 channel 装会冲突)。
        # 同时按 (channel, conda_name) 去重 —— 避免 obabel+openbabel 装两遍。
        by_channel: dict[str, list[str]] = {}
        seen: set[tuple[str, str]] = set()
        for p in package_needs:
            ch, name = KNOWN_PACKAGE_INSTALL[p]
            if (ch, name) in seen:
                continue
            seen.add((ch, name))
            by_channel.setdefault(ch, []).append(name)
        lines = [
            f"conda install -y -n {TARGET_CONDA_ENV} -c {ch} {' '.join(pkgs)}"
            for ch, pkgs in by_channel.items()
        ]
        script = " && ".join(lines)
        _print_progress(f"硬编码命中 {package_needs} → 跳过 LLM,直接 {script}")
    else:
        result: EnvSetupResult = env_setup_llm.invoke(build_env_setup_prompt(package_needs))
        script = build_shell_script(result)

    _print_progress(f"开始修复环境{package_needs}的缺失（attempt {prior_count + 1}/{MAX_ENV_SETUP_ATTEMPTS}）")
    _print_progress("正在执行安装脚本")

    # Execute script in the fixed environment and capture output
    try:
        completed = _run_install_script(script)
        run_ok = completed.returncode == 0
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
    except Exception as exc:
        run_ok = False
        stdout = ""
        stderr = str(exc)

    # 把 install 脚本的关键输出写进日志，便于排查 LLM 写出的脚本是否真的装上了
    if stdout:
        snippet = stdout[-500:] if len(stdout) > 500 else stdout
        _print_progress(f"install stdout (last 500 chars): {snippet}")
    if stderr:
        snippet = stderr[-500:] if len(stderr) > 500 else stderr
        _print_progress(f"install stderr (last 500 chars): {snippet}")

    if run_ok:
        # 自检: 每个 package_needs 真的能在 AutoMD env 里 command -v 找到
        # 防止 conda libmamba solver 误判"已装" (returncode=0 但啥也没装) 的假成功。
        # 已知触发场景: env bin 里已存在同名二进制 (例如 autodock-vina 提供 /bin/vina),
        # 此时 `conda install -y -c conda-forge vina` 会静默跳过。
        conda_cmd = _find_conda_executable()
        still_missing: list[str] = []
        for pkg in package_needs:
            probe_cmd = [conda_cmd, "run", "-n", TARGET_CONDA_ENV, "bash", "-lc",
                         f"command -v {pkg} >/dev/null 2>&1 || test -x \"$CONDA_PREFIX/bin/{pkg}\""]
            try:
                r = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    still_missing.append(pkg)
            except Exception:
                still_missing.append(pkg)

        if still_missing:
            _print_progress(f"自检失败: conda returncode=0 但 {still_missing} 在 {TARGET_CONDA_ENV} env 里仍不可用 (可能是 conda solver 误判已装)")
            run_ok = False
            failure_msg = (
                f"脚本 returncode=0 但以下包在 {TARGET_CONDA_ENV} env 中仍不可用: {still_missing}。"
                f"可能 conda libmamba solver 误判 '已装' (典型: env bin 里已存在同名二进制时跳过 install)。"
            )
        else:
            _print_progress(f"环境修复完成 (自检通过: {package_needs} 可用)")
            # 注意: 不再 pop 清零 attempts —— 之前的 success 可能是假成功,
            # attempts 留着用于熔断计数。attempts 仅在人工 'continue' 时由失败路径清零。
            return Command(
                update={
                    "package_needs": [],
                    "env_setup_status": "success",
                    "env_setup_attempts": env_setup_attempts,
                },
                goto=state.get("calling_node")
            )

    failure_msg = failure_msg or f"脚本执行失败 (returncode != 0): {stderr or stdout or 'unknown error'}"
    env_setup_attempts[attempt_key] = prior_count + 1

    _print_progress(f"环境修复失败：{failure_msg[:200]}")
    human_fix = interrupt(
        f"自动修复环境失败。\n"
        f"缺失依赖：{package_needs}\n"
        f"最后一次错误：{failure_msg}\n"
        f"请手动 `conda activate {TARGET_CONDA_ENV}` 后执行安装，"
        f"完成后输入 'continue' 重试，或输入 'skip' 跳过此步骤。"
    )
    if human_fix.strip().lower() == "continue":
        env_setup_attempts[attempt_key] = 0  # 人工修复成功，重置计数
        return Command(
            update={
                "package_needs": [],
                "env_setup_attempts": env_setup_attempts,
            },
            goto=state.get("calling_node")
        )
    pu.warn("用户跳过环境修复，终止流程")
    return Command(
        update={
            "package_needs": [],
            "env_setup_attempts": env_setup_attempts,
            "env_setup_status": "failed",
            "error_message": f"环境修复失败，用户选择跳过。错误: {failure_msg}",
        },
        goto=END
    )

# 一个简单的验证节点
def protein_clean(state: AutoMDState):
    print("成功进入 protein_clean 节点，当前状态:")
    print(state.get("package_needs"),state.get("calling_node"))


if __name__ == "__main__":
    # 简单测试（不依赖包内 AutoMDState 类型，支持直接以脚本运行）
    graph = StateGraph(AutoMDState)
    graph.add_node("set_env", set_env)
    graph.add_node("protein_clean", protein_clean)


    graph.add_edge(START, "set_env")
    graph.add_edge("set_env", "protein_clean")
    graph.add_edge("protein_clean", END)

    app = graph.compile()

    test_state = {
        "package_needs": ["ambertools", "openbabel"],
        "calling_node": "protein_clean",
    }

    final_state = app.invoke(test_state)
    print("Graph run complete. Final state:")
    print(final_state)
