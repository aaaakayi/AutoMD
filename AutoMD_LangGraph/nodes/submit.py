"""
Submit node: package files, generate job script via LLM, submit to HPC cluster,
monitor job, and download results.

Cluster config is read exclusively from environment variables (.env).
Style aligned with protein.py / ligand.py / docking.py:
  - Tool/SSH failures → handle_tool_error
  - Skip paths → goto="plan_route"
  - LLM revision loop for user feedback ("no" at interrupt points)
  - Success → goto="trajectory_analysis" with md_* state fields

🆕 CLUSTER_MODE 控制两种提交流程:
  - "auto" (默认): 老行为, 全自动 ssh/scp/squeue/scp 完整跑
  - "manual": 只打包 input + md_run.py, 中断, 用户自己 scp 上集群跑,
              跑完 scp 回本机再触发 run_workflow (检测到 md_already_done
              自动跳到 trajectory_analysis)
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import time
from pathlib import Path

nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.types import Command, interrupt
from langgraph.graph import END
from pydantic import BaseModel, Field

from State import AutoMDState
from tools.system_tools import run_shell_command, write_text_file
from tools.shared import failed

from .common import work_root, ensure_dir, handle_tool_error

import nodes.print_utils as pu

# ---------------------------------------------------------------------------
# Robust import of _llm
# ---------------------------------------------------------------------------
try:
    from .common import _llm
except Exception:
    try:
        from nodes.common import _llm
    except Exception:
        import importlib.util
        nd = os.path.dirname(__file__)
        proot = os.path.abspath(os.path.join(nd, ".."))
        if proot not in sys.path:
            sys.path.insert(0, proot)
        spec = importlib.util.spec_from_file_location(
            "nodes.common", os.path.join(nd, "common.py"),
        )
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)
        _llm = common._llm

# ---------------------------------------------------------------------------
# Cluster config — from .env only
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()

CLUSTER_HOST = _env("CLUSTER_HOST")
CLUSTER_USER = _env("CLUSTER_USER")
CLUSTER_REMOTE_DIR = _env("CLUSTER_REMOTE_DIR", "/tmp/automd")
CLUSTER_SCHEDULER = _env("CLUSTER_SCHEDULER", "slurm")
CLUSTER_PARTITION = _env("CLUSTER_PARTITION", "gpu")
CLUSTER_GPUS = _env("CLUSTER_GPUS", "1")
CLUSTER_CPUS = _env("CLUSTER_CPUS", "8")
CLUSTER_WALLTIME = _env("CLUSTER_WALLTIME", "48:00:00")
CLUSTER_CONDA_ENV = _env("CLUSTER_CONDA_ENV", "automd")
CLUSTER_MODULES = _env("CLUSTER_MODULES", "cuda/11.8")
CLUSTER_RUN_SCRIPT = _env("CLUSTER_RUN_SCRIPT", "")
# 🆕 提交流程模式: "auto" (默认, ssh+scp+squeue 全套) | "manual" (只打包, 用户手动)
CLUSTER_MODE = _env("CLUSTER_MODE", "auto")

DEFAULT_POLL_INTERVAL = 1200
MAX_POLL_MULTIPLIER = 2.0
MAX_REVISION_ROUNDS = 3


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------

def _directive_prefix() -> str:
    return {"slurm": "#SBATCH", "pbs": "#PBS", "lsf": "#BSUB", "sge": "#$"}.get(
        CLUSTER_SCHEDULER, "#SBATCH",
    )


def _scheduler_directives() -> str:
    s = CLUSTER_SCHEDULER
    common = f"#SBATCH --job-name=automd\n#SBATCH --partition={CLUSTER_PARTITION}\n#SBATCH --gres=gpu:{CLUSTER_GPUS}\n#SBATCH --cpus-per-task={CLUSTER_CPUS}\n#SBATCH --time={CLUSTER_WALLTIME}\n#SBATCH --output=job_%j.out\n#SBATCH --error=job_%j.err"
    if s == "slurm":
        return textwrap.dedent(common)
    if s == "pbs":
        return textwrap.dedent(f"""\
        #PBS -N automd
        #PBS -q {CLUSTER_PARTITION}
        #PBS -l nodes=1:ppn={CLUSTER_CPUS}:gpus={CLUSTER_GPUS}
        #PBS -l walltime={CLUSTER_WALLTIME}
        #PBS -o job.out
        #PBS -e job.err""")
    if s == "lsf":
        return textwrap.dedent(f"""\
        #BSUB -J automd
        #BSUB -q {CLUSTER_PARTITION}
        #BSUB -n {CLUSTER_CPUS}
        #BSUB -gpu "num={CLUSTER_GPUS}"
        #BSUB -W {CLUSTER_WALLTIME}
        #BSUB -o job.%J.out
        #BSUB -e job.%J.err""")
    if s == "sge":
        return textwrap.dedent(f"""\
        #$ -N automd
        #$ -q {CLUSTER_PARTITION}
        #$ -pe smp {CLUSTER_CPUS}
        #$ -l h_rt={CLUSTER_WALLTIME}
        #$ -l gpu={CLUSTER_GPUS}
        #$ -o job.$JOB_ID.out
        #$ -e job.$JOB_ID.err""")
    return common


def _submit_command(remote_dir: str, script_name: str) -> str:
    return {
        "slurm": f"cd {remote_dir} && sbatch {script_name}",
        "pbs": f"cd {remote_dir} && qsub {script_name}",
        "lsf": f"cd {remote_dir} && bsub < {script_name}",
        "sge": f"cd {remote_dir} && qsub {script_name}",
    }.get(CLUSTER_SCHEDULER, f"cd {remote_dir} && sbatch {script_name}")


def _script_name() -> str:
    return {"pbs": "job.pbs", "lsf": "job.lsf", "sge": "job.sge"}.get(
        CLUSTER_SCHEDULER, "job.sh",
    )


# ---------------------------------------------------------------------------
# SubmitPlan — LLM structured output
# ---------------------------------------------------------------------------

class SubmitPlan(BaseModel):
    thought: str = Field(description="推理过程")
    job_script: str = Field(description=f"完整的 {CLUSTER_SCHEDULER} 提交脚本")
    files_to_upload: list[str] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=60)


submit_llm = _llm.with_structured_output(SubmitPlan, method="function_calling")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_prompt(raw_task: str, files: list[str]) -> str:
    file_list = "\n".join(f"  - {f}" for f in files) if files else "  (none)"
    return textwrap.dedent(f"""
## 任务需求
{raw_task[:500]}

## 输入文件
{file_list}

## 集群配置
- 调度器: {CLUSTER_SCHEDULER}
- 分区/队列: {CLUSTER_PARTITION}
- GPU: {CLUSTER_GPUS}  CPU: {CLUSTER_CPUS}
- 墙钟: {CLUSTER_WALLTIME}
- 远程目录: {CLUSTER_REMOTE_DIR}
- conda: {CLUSTER_CONDA_ENV}
- 模块: {CLUSTER_MODULES}

## 要求
生成 {CLUSTER_SCHEDULER} 作业脚本 ({_directive_prefix()} 指令):
1. 设置调度器指令 (分区、GPU、CPU、墙钟)
2. 加载模块 ({CLUSTER_MODULES}) + 激活 conda ({CLUSTER_CONDA_ENV})
3. 运行 MD 模拟，输出到 md_output/
4. files_to_upload 列出要上传的文件路径
""").strip()


def _build_revision_prompt(raw_task: str, user_feedback: str, previous_script: str) -> str:
    return textwrap.dedent(f"""
## 原始任务
{raw_task[:500]}

## 用户反馈 (需修改之处)
{user_feedback[:1000]}

## 上一版脚本
```
{previous_script[:3000]}
```

## 要求
根据用户反馈修改作业脚本。保留集群配置不变，只改用户提到的部分。
输出完整修订后脚本和 files_to_upload。
""").strip()


# ---------------------------------------------------------------------------
# Fallback plan (no LLM)
# ---------------------------------------------------------------------------

def _fallback_plan(files: list[str], job_name: str) -> SubmitPlan:
    script = textwrap.dedent(f"""\
    #!/bin/bash
    {_scheduler_directives()}

    module load {CLUSTER_MODULES}
    source activate {CLUSTER_CONDA_ENV}

    cd $SLURM_SUBMIT_DIR 2>/dev/null || cd $PBS_O_WORKDIR 2>/dev/null || cd $(dirname $0)
    mkdir -p md_output

    python -c "
    print('No run_script specified. Input files:')
    {chr(10).join(f'print("  {Path(f).name}")' for f in files)}
    "
    """)
    return SubmitPlan(
        thought="LLM unavailable; fallback template",
        job_script=script,
        files_to_upload=files,
        poll_interval_seconds=60,
    )


# ---------------------------------------------------------------------------
# Remote ops (ssh / scp via run_shell_command)
# ---------------------------------------------------------------------------

def _ssh(cmd: str, timeout: int = 120) -> tuple[bool, str]:
    full = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {CLUSTER_USER}@{CLUSTER_HOST} '{cmd}'"
    r = run_shell_command(full, timeout_seconds=timeout)
    if not r.ok:
        return False, "\n".join(r.errors) if r.errors else "ssh error"
    d = r.data if isinstance(r.data, dict) else {}
    return d.get("exit_code", -1) == 0, d.get("output", str(r.data))


def _scp_upload(local_paths: list[str], remote_dir: str) -> tuple[bool, str]:
    for p in local_paths:
        if not os.path.exists(p):
            return False, f"文件不存在: {p}"
    paths = " ".join(f'"{p}"' for p in local_paths)
    full = f"scp -o StrictHostKeyChecking=no -o ConnectTimeout=10 {paths} {CLUSTER_USER}@{CLUSTER_HOST}:{remote_dir}/"
    r = run_shell_command(full, timeout_seconds=600)
    if not r.ok:
        return False, "\n".join(r.errors) if r.errors else "scp error"
    d = r.data if isinstance(r.data, dict) else {}
    return d.get("exit_code", -1) == 0, d.get("output", str(r.data))


def _scp_download(remote_dir: str, local_dir: str, glob: str = "*") -> tuple[bool, str]:
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    full = f"scp -o StrictHostKeyChecking=no -o ConnectTimeout=10 -r {CLUSTER_USER}@{CLUSTER_HOST}:{remote_dir}/{glob} \"{local_dir}/\""
    r = run_shell_command(full, timeout_seconds=600)
    if not r.ok:
        return False, "\n".join(r.errors) if r.errors else "scp error"
    d = r.data if isinstance(r.data, dict) else {}
    return d.get("exit_code", -1) == 0, d.get("output", str(r.data))


def _safe_cleanup(remote: str) -> None:
    if remote.startswith(CLUSTER_REMOTE_DIR) and remote != CLUSTER_REMOTE_DIR:
        _ssh(f"rm -rf {remote}")


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

def _parse_job_id(output: str) -> str | None:
    m = re.search({
        "slurm": r"Submitted batch job (\d+)",
        "pbs": r"(\d+\.[\w-]+)",
        "lsf": r"Job <(\d+)>",
        "sge": r"Your job (\d+)",
    }.get(CLUSTER_SCHEDULER, r"Submitted batch job (\d+)"), output)
    return m.group(1) if m else None


def _job_status(job_id: str) -> str:
    """COMPLETED / RUNNING / PENDING / FAILED / UNKNOWN."""
    s = CLUSTER_SCHEDULER
    if s == "slurm":
        ok, out = _ssh(f"squeue -j {job_id} -h -o '%T' 2>/dev/null")
        if ok and out.strip():
            st = out.strip().upper()
            if "RUNNING" in st or "RUN" in st: return "RUNNING"
            if "PENDING" in st or "PEND" in st: return "PENDING"
            if "COMPLETED" in st: return "COMPLETED"
            if any(x in st for x in ("FAILED", "ERROR", "TIMEOUT", "CANCELLED")): return "FAILED"
        ok2, out2 = _ssh(f"sacct -j {job_id} --format State -n -P 2>/dev/null | head -1")
        if ok2 and out2.strip():
            s2 = out2.strip().upper()
            if s2 == "COMPLETED": return "COMPLETED"
            if s2 in ("FAILED", "TIMEOUT", "CANCELLED", "OUT_OF_MEMORY", "NODE_FAIL"): return "FAILED"
        return "UNKNOWN"
    if s == "lsf":
        ok, out = _ssh(f"bjobs -noheader -o stat {job_id} 2>/dev/null")
        if ok and out.strip():
            st = out.strip().upper()
            if st in ("RUN",): return "RUNNING"
            if st in ("PEND",): return "PENDING"
            if st in ("DONE",): return "COMPLETED"
            if st in ("EXIT",): return "FAILED"
        return "UNKNOWN"
    if s == "pbs":
        ok, out = _ssh(f"qstat -f {job_id} 2>/dev/null | awk -F = '/job_state/ {{gsub(/ /,\"\",$2); print $2; exit}}'")
        if ok and out.strip():
            sc = out.strip().upper()
            if sc in ("R", "E"): return "RUNNING"
            if sc in ("Q", "H", "W"): return "PENDING"
            if sc == "C": return "COMPLETED"
        ok2, out2 = _ssh(f"qacct -j {job_id} 2>/dev/null | awk -F = '/exit_status/ {{gsub(/ /,\"\",$2); print $2; exit}}'")
        if ok2 and out2.strip():
            return "COMPLETED" if out2.strip() == "0" else "FAILED"
        return "UNKNOWN"
    if s == "sge":
        ok, out = _ssh(f"qstat -j {job_id} 2>/dev/null | head -20")
        if ok and "error" not in out.lower():
            if "running" in out.lower(): return "RUNNING"
            if "pending" in out.lower() or "qw" in out.lower(): return "PENDING"
        ok2, out2 = _ssh(f"qacct -j {job_id} 2>/dev/null | awk -F : '/exit_status/ {{gsub(/ /,\"\",$2); print $2; exit}}'")
        if ok2 and out2.strip():
            return "COMPLETED" if out2.strip() == "0" else "FAILED"
        return "UNKNOWN"
    return "UNKNOWN"


def _walltime_to_seconds(wt: str) -> int:
    parts = wt.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) if len(parts) == 3 else 3600


# ---------------------------------------------------------------------------
# Confirmation + LLM revision loop
# ---------------------------------------------------------------------------

def _confirm_or_revise(
    state: AutoMDState,
    plan: SubmitPlan,
    files: list[str],
    *,
    files_uploaded: bool,
) -> SubmitPlan | Command:
    """Show plan, ask yes/no. 'no' → LLM revision → loop. 'skip' → plan_route."""

    raw_task = state.get("raw_task", "")

    for _round in range(MAX_REVISION_ROUNDS):
        if files_uploaded:
            choice = interrupt(
                "脚本已上传。输入 'yes' 提交运行，或描述需修改的内容。"
                "输入 'skip' 改为本地运行 MD。"
            )
        else:
            file_names = "\n".join(f"  - {Path(f).name}" for f in files)
            choice = interrupt(
                f"即将上传以下文件到集群:\n{file_names}\n"
                f"远程目录: {CLUSTER_REMOTE_DIR}/{state.get('project_id', 'job')}\n"
                f"输入 'yes' 确认上传，或描述需修改的内容。输入 'skip' 改为本地运行 MD。"
            )

        choice_stripped = choice.strip().lower()
        if choice_stripped in ("yes", "y"):
            return plan

        if choice_stripped == "skip":
            return Command(
                update={
                    "submit_to_cluster": False,
                    "md_is_success": False,
                    "md_summary": "用户取消集群提交，改本地运行",
                },
                goto="plan_route",
            )

        # User feedback → LLM revises
        feedback = choice.strip()
        pu.debug(f"用户反馈: {feedback}")
        pu.debug("LLM 修订脚本中...")

        new_task = f"{raw_task}\n\n用户额外要求: {feedback}"
        try:
            new_plan = submit_llm.invoke(
                _build_revision_prompt(raw_task, feedback, plan.job_script),
            )
        except Exception as exc:
            pu.debug(f"LLM 修订失败: {exc}")
            continue

        plan = new_plan
        pu.debug("修订后的作业脚本:\n" + plan.job_script)

    # Max rounds exhausted
    return Command(
        update={
            "submit_to_cluster": False,
            "md_is_success": False,
            "md_summary": "已达最大修订轮次，改本地运行",
        },
        goto="plan_route",
    )


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def _collect_files(state: AutoMDState) -> list[str]:
    files: list[str] = []
    explicit = state.get("submit_input_files") or []
    if explicit:
        files = [f for f in explicit if os.path.exists(f)]
    else:
        md_prmtop = state.get("md_prmtop") or ""
        md_inpcrd = state.get("md_inpcrd") or ""
        if md_prmtop and os.path.exists(md_prmtop) and md_inpcrd and os.path.exists(md_inpcrd):
            files = [md_prmtop, md_inpcrd]
        else:
            for k in ("protein_prmtop", "protein_inpcrd", "ligand_prmtop", "ligand_inpcrd"):
                v = state.get(k, "")
                if v and os.path.exists(v):
                    files.append(v)
    if CLUSTER_RUN_SCRIPT and os.path.exists(CLUSTER_RUN_SCRIPT):
        files.append(CLUSTER_RUN_SCRIPT)
    return files


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------


# 🆕 CLUSTER_MODE=manual: 打包 input + md_run.py, 中断, 用户手动桥接
def _build_python_md_runner(state: AutoMDState, files: list[str]) -> str:
    """用 LLM 生成独立 OpenMM MD 脚本 (集群上跑)."""
    prmtop = Path(files[0]).name if files else "complex.prmtop"
    inpcrd = Path(files[1]).name if len(files) > 1 else "complex.inpcrd"

    md_dur = state.get("md_duration_ns") or 10.0
    md_temp = state.get("md_temperature_k") or 300.0
    md_press = state.get("md_pressure_atm") or 1.0
    md_step = state.get("md_timestep_fs") or 2.0
    md_save = state.get("md_save_interval_ps") or 100.0
    md_nvt = state.get("md_nvt_equil_ps") or 100.0
    md_npt = state.get("md_npt_equil_ps") or 100.0
    md_ff = state.get("md_force_field") or "ff14SB"
    md_water = state.get("md_water_model") or "tip3p"
    md_ligand_ff = state.get("md_ligand_ff") or "gaff2"

    prompt = textwrap.dedent(f"""\
你是分子动力学脚本生成器。生成一个独立可运行的 Python 脚本, 用 OpenMM + AMBER 跑 MD。

## 输入文件 (跟脚本放同一目录)
- {prmtop}     # Amber 复合物拓扑
- {inpcrd}     # Amber 复合物初始坐标

## MD 配置
- 时长: {md_dur} ns (生产相 NPT)
- 温度: {md_temp} K
- 压强: {md_press} atm
- 时间步长: {md_step} fs
- NVT 平衡: {md_nvt} ps
- NPT 平衡: {md_npt} ps
- 轨迹保存间隔: {md_save} ps
- 力场: {md_ff} + {md_ligand_ff} (已含在 prmtop)

## 要求
- import: openmm, openmm.app as app, openmm.unit as unit
- 用 app.AmberPrmtopFile + app.AmberInpcrdFile 加载
- 系统: nonbondedMethod=PME, nonbondedCutoff=1.0 nm, constraints=HBonds
- 积分器: openmm.LangevinMiddleIntegrator ({md_temp} K, 1/ps, {md_step} fs)
- 平台: 先试 CUDA, fallback CPU
- 流程:
  1. 能量最小化 (maxIterations=1000)
  2. NVT 平衡 ({md_nvt} ps)
  3. 加 MonteCarloBarostat ({md_press} bar, {md_temp} K, 25 freq)
  4. NPT 模拟 ({md_dur} ns)
- 创建 md_output/ 目录
- DCDReporter → md_output/prod.dcd (间隔 {md_save} ps)
- StateDataReporter → stdout (每 10000 步, 报 step / T / E)
- 用 mdtraj 把 prod.dcd + prmtop 转成 md_output/prod.nc (mdtraj 格式)

## 输出
只输出 Python 代码, 不要 markdown 包装, 不要解释文字。
""")

    try:
        result = _llm.invoke(prompt)
        code = result.content if hasattr(result, "content") else str(result)
        # 去掉 markdown ```python``` 包装
        code = re.sub(r"^```python\s*\n", "", code, flags=re.MULTILINE)
        code = re.sub(r"^```\s*\n", "", code, flags=re.MULTILINE)
        code = re.sub(r"\n```\s*$", "", code, flags=re.MULTILINE)
        return code.strip() + "\n"
    except Exception as exc:
        pu.debug(f"LLM 生成 md_run.py 失败, 用 fallback: {exc!r}")
        return _fallback_md_runner(prmtop, inpcrd, md_dur, md_temp, md_step, md_nvt, md_save, md_press)


def _fallback_md_runner(prmtop, inpcrd, md_dur, md_temp, md_step, md_nvt, md_save, md_press) -> str:
    """LLM 失败时的占位脚本 (用户手动实现 MD 流程)."""
    return textwrap.dedent(f"""\
#!/usr/bin/env python3
\"\"\"AutoMD MD runner (fallback - LLM 不可用, 请手动实现).

输入: {prmtop}, {inpcrd}
输出: md_output/prod.dcd + md_output/prod.nc
配置: 时长 {md_dur} ns, 温度 {md_temp} K, 步长 {md_step} fs, NVT 平衡 {md_nvt} ps
\"\"\"
import os
os.makedirs("md_output", exist_ok=True)
raise NotImplementedError(
    "LLM 不可用, 请手动编写 OpenMM MD 脚本 (参考 prompt 中的规格), "
    "或重试让 LLM 生成。"
)
""")


def _build_manual_readme(state: AutoMDState, files: list[str], job_name: str) -> str:
    """给用户的"集群手动提交"使用说明."""
    raw_task = state.get("raw_task", "")[:200]
    md_dur = state.get("md_duration_ns") or 10.0
    md_temp = state.get("md_temperature_k") or 300.0
    md_ff = state.get("md_force_field") or "ff14SB"
    md_ligand_ff = state.get("md_ligand_ff") or "gaff2"
    md_water = state.get("md_water_model") or "tip3p"
    run_id = state.get("run_id", "")
    file_list = "\n".join(f"  - {Path(f).name}" for f in files)
    local_md_dir = f"D:\\\\AutoMD\\\\AutoMD_LangGraph\\\\output\\\\{run_id}\\\\md\\\\{job_name}\\\\"

    return textwrap.dedent(f"""\
AutoMD 集群手动提交包
=====================

任务: {raw_task}

## 输入文件
{file_list}
  - md_run.py       # OpenMM MD 脚本 (LLM 生成或 fallback)
  - README.txt      # 本文件

## MD 配置 (供参考)
- 时长: {md_dur} ns
- 温度: {md_temp} K
- 蛋白力场: {md_ff}
- 配体力场: {md_ligand_ff}
- 水模型: {md_water}

## 步骤

### 1. 上传整个目录到集群
```bash
scp -r {job_name}/ user@cluster:/tmp/automd/{job_name}/
```

### 2. SSH 登录, 跑 MD
```bash
ssh user@cluster
cd /tmp/automd/{job_name}
python md_run.py
```
（如果集群有 SLURM, 把 md_run.py 包到 sbatch 脚本里再提交也行）

### 3. 跑完, 把 md_output/ 拉回本机
```bash
scp -r user@cluster:/tmp/automd/{job_name}/md_output/ \\
    "{local_md_dir}"
```
（路径要根据本机实际项目根调整）

### 4. 回到 AutoMD 前端, 重新触发 run_workflow
在前端对话框说 "继续分析 1HX0 + 布洛芬" 之类的 (带上原始任务), workflow
会自动检测 md_output/ 存在, 跳过 MD 直接跑 trajectory_analysis。
如果前端 LLM 没自动识别, 也可以直接说 "MD 已跑完, 只做分析"。

## 故障排除
- md_run.py 跑失败 → 看 stderr, 常见是 CUDA 不可用 (自动 fallback CPU)
- 轨迹文件没生成 → 检查 md_output/ 目录权限
- Phase 3 没自动跳分析 → 确认本机 output/{{run_id}}/md/{job_name}/prod.nc 存在
""")


def _submit_manual(state: AutoMDState) -> Command:
    """CLUSTER_MODE=manual: 打包 input, 中断, 用户手动 scp 跑."""
    files = _collect_files(state)
    if not files:
        user_choice = interrupt(
            "无可打包文件, 输入 'retry' 重新收集, 或 'skip' 改本地跑。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="submit_to_cluster")
        return Command(
            update={
                "submit_to_cluster": False,
                "md_is_success": False,
                "md_summary": "无文件可打包, 改本地跑",
            },
            goto="plan_route",
        )

    pu.debug(f"收集到 {len(files)} 个文件: {[Path(f).name for f in files]}")

    # 生成 md_run.py
    md_run_code = _build_python_md_runner(state, files)

    # 输出目录
    job_name = re.sub(r"[^a-zA-Z0-9_-]", "_", state.get("project_id") or "job")
    out_dir = ensure_dir(work_root(state) / "cluster_input" / job_name)

    # 写 README
    readme = _build_manual_readme(state, files, job_name)
    (out_dir / "README.txt").write_text(readme, encoding="utf-8")

    # 写 md_run.py
    (out_dir / "md_run.py").write_text(md_run_code, encoding="utf-8")

    # 复制 input 文件
    for f in files:
        target = out_dir / Path(f).name
        if not target.exists():
            try:
                shutil.copy2(f, target)
            except Exception as e:
                pu.warn(f"复制 {f} 失败: {e}")

    # 打 tarball
    tarball_base = str(out_dir.parent / job_name)
    tarball = tarball_base + ".tar.gz"
    if os.path.exists(tarball):
        os.remove(tarball)
    shutil.make_archive(tarball_base, "gztar",
                        root_dir=str(out_dir.parent), base_dir=job_name)
    tarball_path = Path(tarball_base + ".tar.gz").resolve()

    pu.ok(f"已打包到 {tarball_path}")

    # 中断 workflow (goto END), 留用户手动跑
    return Command(
        update={
            "submit_to_cluster": False,    # 已处理
            "md_is_success": False,         # 还没跑
            "md_summary": (
                f"已生成手动提交包: {tarball_path}。"
                f"请 scp 到集群跑完后, 回到前端说'继续分析'即可自动接续。"
            ),
            "submit_input_files": files,
            "submit_result": str(tarball_path),
            "_resume_after_md": True,        # Phase 3 检测标记
        },
        goto=END,
    )


# 根据state和.env配置，决定是否提交到集群，并执行提交流程
def submit_to_cluster(state: AutoMDState) -> Command:
    """Submit MD job to HPC cluster.

    When submit_to_cluster=True in state, replaces md_run.
    CLUSTER_MODE=manual: 打包 input + 中断, 用户手动 scp 跑 (Phase 3 续接)
    CLUSTER_MODE=auto (默认): 全自动 ssh/scp/squeue/下载
    Failures → handle_tool_error. Skips → plan_route.
    """

    # 🆕 Manual 模式: 走 _submit_manual, 完全跳过 SSH/SCP/squeue
    if CLUSTER_MODE == "manual":
        return _submit_manual(state)

    if not CLUSTER_HOST:
        user_choice = interrupt(
            "集群未配置 (CLUSTER_HOST 为空)。输入 'retry' 重试，或 'skip' 改为本地运行 MD。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="submit_to_cluster")
        return Command(
            update={
                "submit_to_cluster": False,
                "md_is_success": False,
                "md_summary": "集群未配置，改本地运行",
            },
            goto="plan_route",
        )

    files = _collect_files(state)
    if not files:
        user_choice = interrupt(
            "无可上传文件。输入 'retry' 重新收集，或 'skip' 改为本地运行 MD。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="submit_to_cluster")
        return Command(
            update={
                "submit_to_cluster": False,
                "md_is_success": False,
                "md_summary": "无文件可提交，改本地运行",
            },
            goto="plan_route",
        )

    pu.debug(f"文件: {[Path(f).name for f in files]}, 调度器: {CLUSTER_SCHEDULER}")

    # LLM: generate initial plan
    pu.debug("LLM 生成作业脚本中...")
    raw_task = state.get("raw_task", "")
    try:
        plan: SubmitPlan = submit_llm.invoke(_build_prompt(raw_task, files))
    except Exception as exc:
        pu.debug(f"LLM 失败，使用 fallback: {exc}")
        plan = _fallback_plan(files, state.get("project_id") or "job")

    script_lines = plan.job_script.strip().split("\n")
    pu.info(f"作业脚本 ({len(script_lines)} 行)")
    for line in script_lines[:25]:
        pu.debug(line)
    if len(script_lines) > 25:
        pu.info(f"... (共 {len(script_lines)} 行，完整内容见日志文件)")

    # --- Confirmation #1: upload? ---
    result = _confirm_or_revise(state, plan, plan.files_to_upload or files, files_uploaded=False)
    if isinstance(result, Command):
        return result
    plan = result

    job_name = re.sub(r"[^a-zA-Z0-9_-]", "_", state.get("project_id") or "job")
    remote = f"{CLUSTER_REMOTE_DIR}/{job_name}"

    # mkdir
    ok, msg = _ssh(f"mkdir -p {remote}")
    if not ok:
        return handle_tool_error(
            result=failed(errors=[f"ssh mkdir 失败: {msg}"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )

    # Write script + upload
    sname = _script_name()
    submit_dir = ensure_dir(work_root(state) / "submit" / job_name)
    local_script = submit_dir / f"submit_{job_name}{Path(sname).suffix}"
    write_text_file(str(local_script), plan.job_script)

    uploads = list(dict.fromkeys([str(local_script)] + (plan.files_to_upload or [])))
    pu.debug(f"上传 {len(uploads)} 个文件...")
    ok, msg = _scp_upload(uploads, remote)
    if not ok:
        return handle_tool_error(
            result=failed(errors=[f"scp 失败: {msg}"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )

    # --- Confirmation #2: execute? ---
    result = _confirm_or_revise(state, plan, files, files_uploaded=True)
    if isinstance(result, Command):
        _safe_cleanup(remote)
        return result

    # Submit
    cmd = _submit_command(remote, sname)
    pu.debug(f"{cmd}")
    ok, out = _ssh(cmd)
    if not ok:
        return handle_tool_error(
            result=failed(errors=[f"提交失败: {out}"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )

    job_id = _parse_job_id(out)
    if not job_id:
        return handle_tool_error(
            result=failed(errors=[f"无法解析 Job ID: {out}"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )
    pu.ok(f"集群作业已提交, Job ID: {job_id}")

    # Poll
    max_wait = int(_walltime_to_seconds(CLUSTER_WALLTIME) * MAX_POLL_MULTIPLIER)
    elapsed, interval = 0, plan.poll_interval_seconds or DEFAULT_POLL_INTERVAL
    last_state = ""

    while elapsed < max_wait:
        st = _job_status(job_id)
        if st != last_state:
            pu.info(f"{job_id}: {st} ({elapsed // 60}m)")
            last_state = st
        if st == "COMPLETED":
            break
        if st in ("FAILED",):
            return handle_tool_error(
                result=failed(errors=[f"Job {job_id} FAILED"]),
                calling_node="submit_to_cluster", state=state,
                retry_goto="submit_to_cluster",
                skip_update={"submit_to_cluster": False, "md_is_success": False},
            )
        time.sleep(interval)
        elapsed += interval
    else:
        return handle_tool_error(
            result=failed(errors=[f"Job {job_id} 超时 ({max_wait // 3600}h)"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )

    pu.ok(f"集群作业完成: {job_id}")

    # Download results — try md_output/ first, then entire remote dir
    local_out = str(Path(files[0]).parent.parent / "cluster_results" / job_id)
    ok, output = _scp_download(f"{remote}/md_output", local_out)
    if not ok:
        pu.warn(f"md_output/ 下载失败，尝试完整下载: {output}")
        ok, output = _scp_download(remote, local_out, glob="*")

    if not ok:
        _safe_cleanup(remote)
        return handle_tool_error(
            result=failed(errors=[f"下载结果失败: {output}"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )

    # Verify at least one file was downloaded
    downloaded = list(Path(local_out).rglob("*"))
    downloaded_files = [f for f in downloaded if f.is_file()]
    if not downloaded_files:
        _safe_cleanup(remote)
        return handle_tool_error(
            result=failed(errors=[f"下载完成但本地无文件: {local_out}"]),
            calling_node="submit_to_cluster", state=state,
            retry_goto="submit_to_cluster",
            skip_update={"submit_to_cluster": False, "md_is_success": False},
        )

    pu.ok(f"结果下载: {local_out} ({len(downloaded_files)} 个文件)")

    # Safe exit: cleanup remote
    _safe_cleanup(remote)

    return Command(
        update={
            "submit_job_id": job_id,
            "md_result": local_out,
            "md_trajectory": local_out,
            "md_is_success": True,
            "md_summary": f"集群 {CLUSTER_SCHEDULER} 作业 {job_id} 完成: {local_out}",
        },
        goto="trajectory_analysis",
    )
