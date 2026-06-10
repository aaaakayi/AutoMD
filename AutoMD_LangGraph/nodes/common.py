from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# When nodes are executed directly as scripts, ensure the package and project
# roots are on sys.path so sibling imports resolve correctly.
nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek
from langgraph.types import Command, interrupt

from State import AutoMDState
from tools.shared import PROJECT_ROOT, ToolResult

load_dotenv()
api_key = os.getenv("LLM_API_KEY")
model_name = os.getenv("LLM_MODEL_ID", "deepseek-chat")
base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
_llm = ChatDeepSeek(
    model=model_name,
    temperature=0.1,
    max_tokens=4096,
    api_key=api_key,
    base_url=base_url,
    model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
)

def work_root(state: dict | None = None) -> Path:
    run_id = (state or {}).get("run_id") or "default"
    return PROJECT_ROOT / "output" / run_id


# 🆕 检测 MD 结果是否已存在 (用于 manual 集群模式的 Phase 3 续接)
def _detect_existing_md_result(state: dict) -> bool:
    """检查 MD 产物是否在文件系统上 (绕过 mtime 之类的不一致).

    检查顺序:
      1. state.md_trajectory 显式路径
      2. 默认路径 PROJECT_ROOT/output/{run_id}/md/{project_id}/prod.nc
    """
    md_traj = state.get("md_trajectory") or ""
    if md_traj and os.path.exists(md_traj):
        return True

    run_id = state.get("run_id") or "default"
    project_id = state.get("project_id") or "default"
    default_nc = PROJECT_ROOT / "output" / run_id / "md" / project_id / "prod.nc"
    if default_nc.exists():
        return True

    # 兜底: 任何 .nc 轨迹文件存在也算
    md_dir = PROJECT_ROOT / "output" / run_id / "md" / project_id
    if md_dir.exists() and any(md_dir.glob("*.nc")):
        return True

    return False


# ============================================================================
# Utilities
# ============================================================================

def get_llm() -> ChatDeepSeek:
    return _llm


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, ToolResult):
        return value.format_for_agent()
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            if item is not None:
                parts.append(f"{key}: {item}")
        return "\n".join(parts)
    return str(value)


def tool_ok(value: Any) -> bool:
    return isinstance(value, ToolResult) and value.ok or bool(value)


def tool_data(value: Any) -> Any:
    if isinstance(value, ToolResult):
        return value.data
    return value


def extract_pdb_id(text_value: str) -> Optional[str]:
    normalized = text_value.strip()
    explicit = re.search(r"(?i)(?:pdb\s*id|pdbid|pdb)\s*(?:[:=：为]\s*)?([A-Za-z0-9]{4})", normalized)
    if explicit:
        return explicit.group(1).upper()
    token = re.search(r"(?<![A-Za-z0-9])([A-Za-z0-9]{4})(?![A-Za-z0-9])", normalized)
    return token.group(1).upper() if token else None


def extract_smiles(text_value: str) -> Optional[str]:
    match = re.search(
        r"SMILES\s*(?:[:=：为]\s*)?([A-Za-z0-9@+\-\[\]\(\)=#$\\/%.:]+)",
        text_value,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None


def extract_file_path(text_value: str) -> Optional[str]:
    match = re.search(r"([\w./\\-]+\.(?:sdf|mol2|pdb|pdbqt|mol))", text_value, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_numeric_hints(text_value: str) -> dict:
    """Regex-based fallback extraction of numeric parameters from raw task text.

    Returns a dict with keys matching Vavles field names. Only populated when
    the regex finds an explicit numeric value in the text — empty dict otherwise.
    """
    hints: dict = {}

    m = re.search(r"(\d+(?:\.\d+)?)\s*ns", text_value, flags=re.IGNORECASE)
    if m:
        hints["md_duration_ns"] = float(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*K\b", text_value)
    if m:
        hints["md_temperature_k"] = float(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*atm\b", text_value, flags=re.IGNORECASE)
    if m:
        hints["md_pressure_atm"] = float(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*fs\b", text_value, flags=re.IGNORECASE)
    if m:
        hints["md_timestep_fs"] = float(m.group(1))

    m = re.search(r"(?:exhaustiveness|精度)\s*(?:[:=：为]\s*)?(\d+)", text_value, flags=re.IGNORECASE)
    if m:
        hints["docking_exhaustiveness"] = int(m.group(1))

    return hints


# ============================================================================
# Task valves
# ============================================================================

class Vavles(BaseModel):
    """LLM 从用户任务中提取所有可配置参数。未指定则使用默认值。"""
    # ---- 核心标识符 ----
    protein_pdb_id: str = Field(default="", description="蛋白质PDB ID，4位字母数字。从任务描述或用户修改意见中提取，未识别则留空")
    ligand_smiles: str = Field(default="", description="配体SMILES字符串。从任务描述或用户修改意见中提取，未识别则留空")
    ligand_input_file: str = Field(default="", description="配体输入文件路径(sdf/mol2/pdb等)。从任务描述或用户修改意见中提取，未识别则留空")

    # ---- 流程开关 ----
    need_protein: bool = Field(description="是否需要蛋白处理")
    need_ligand: bool = Field(description="是否需要配体处理")
    need_docking: bool = Field(description="是否需要对接")
    need_md: bool = Field(description="是否需要MD模拟")
    need_analysis: bool = Field(description="是否需要分析")

    # ---- MD 模拟参数 ----
    md_duration_ns: float = Field(default=1.0, description="MD模拟时长(ns)，从任务中提取数字，未指定默认1.0")
    md_temperature_k: float = Field(default=300.0, description="温度(K)，如300K、310K。未指定默认300.0")
    md_ensemble: str = Field(default="npt", description="系综: npt/nvt/nve。识别NPT/NVT/NVE关键词，未指定默认npt")
    md_solvent: str = Field(default="explicit", description="溶剂: explicit/tip3p/opc/implicit。识别显式溶剂/TIP3P等关键词")
    md_pressure_atm: float = Field(default=1.0, description="压强(atm)，默认1.0")
    md_timestep_fs: float = Field(default=2.0, description="积分步长(fs)，如2fs、1fs，默认2.0")
    md_nvt_equil_ps: float = Field(default=100.0, description="NVT平衡时长(ps)，默认100")
    md_npt_equil_ps: float = Field(default=100.0, description="NPT平衡时长(ps)，默认100")
    md_save_interval_ps: float = Field(default=100.0, description="轨迹帧保存间隔(ps)，默认100")
    md_force_field: str = Field(default="ff14SB", description="蛋白力场: ff14SB/ff19SB/ff03等，默认ff14SB")
    md_water_model: str = Field(default="tip3p", description="水模型: tip3p/opc/tip4pew/tip4p/spce，默认tip3p")
    md_ligand_ff: str = Field(default="gaff2", description="配体力场: gaff/gaff2，默认gaff2")

    # ---- 对接参数 ----
    docking_mode: str = Field(default="blind", description="对接模式: blind(P2Rank盲对接)/visual_box(PyMOL可视对接)。识别可视对接/PyMOL/GUI手动等关键词")
    docking_exhaustiveness: int = Field(default=8, description="Vina对接精度，识别高精度/exhaustiveness=16等，默认8")
    docking_num_modes: int = Field(default=9, description="对接模式数，默认9")
    docking_energy_range: float = Field(default=3.0, description="对接能量范围(kcal/mol)，默认3.0")

    # ---- 分析与降级 ----
    analysis_mmpbsa: bool = Field(default=False, description="是否尝试MMPBSA计算结合自由能。识别MMPBSA/结合自由能关键词")
    degradation_instructions: str = Field(
        default="",
        description="用户指定的降级/容错策略。将'若X失败则Y'、'超时则Z'等描述整理为 '- 场景: 操作' 格式",
    )
    # ---- 集群手动模式的"续接" ----
    md_already_done: bool = Field(
        default=False,
        description="MD 结果已存在, 跳过 MD 跑 trajectory_analysis 即可。"
                    "识别'继续分析'/'MD 已跑过'/'只做分析'/'resume after MD'等关键词。"
                    "配合 CLUSTER_MODE=manual, 用户集群跑完回到本机后, 重新触发 run_workflow 时设 true",
    )


Vavles_llm = _llm.with_structured_output(Vavles,method="function_calling")


def _fallback_task_valves(text_value: str) -> Vavles:
    normalized = text_value.lower()
    has_protein = any(k in normalized for k in ("蛋白", "protein", "受体", "receptor"))
    has_ligand = any(k in normalized for k in ("配体", "ligand", "小分子", "smiles", "sdf", "mol2"))
    has_docking = any(k in normalized for k in ("对接", "docking", "dock"))
    has_md = not any(m in normalized for m in ("不要进行md", "不进行md", "无需md", "不做md", "skip md", "no md", "without md"))
    has_analysis = any(k in normalized for k in ("分析", "analysis", "总结", "report", "评估"))
    return Vavles(
        protein_pdb_id="",
        ligand_smiles="",
        ligand_input_file="",
        need_protein=has_protein or has_docking or has_md,
        need_ligand=has_ligand or has_docking or has_md,
        need_docking=has_docking or (has_protein and has_ligand),
        need_md=has_md,
        need_analysis=has_analysis,
        md_duration_ns=1.0,
        md_temperature_k=300.0,
        md_ensemble="npt",
        md_solvent="explicit",
        md_pressure_atm=1.0,
        md_timestep_fs=2.0,
        md_nvt_equil_ps=100.0,
        md_npt_equil_ps=100.0,
        md_save_interval_ps=100.0,
        md_force_field="ff14SB",
        md_water_model="tip3p",
        md_ligand_ff="gaff2",
        docking_mode="blind",
        docking_exhaustiveness=8,
        docking_num_modes=9,
        docking_energy_range=3.0,
        analysis_mmpbsa=False,
        degradation_instructions="",
    )


def analyze_task_intent(text_value: str) -> Vavles:
    prompt = f"""
你是分子动力学任务意图分析器。
请基于用户需求输出一个结构化结果，包含以下字段：

## 核心标识符
- protein_pdb_id: 蛋白质PDB ID。识别 "PDB ID为XXXX"/"蛋白质PDB改成XXXX"/"PDB: XXXX" 等模式，提取4位字母数字码，转为大写。未识别则留空 ""。
- ligand_smiles: 配体SMILES字符串。识别 "SMILES为XXXX"/"SMILES: XXXX" 等模式，提取完整的SMILES字符串。未识别则留空 ""。
- ligand_input_file: 配体输入文件路径。识别 ".sdf"/".mol2"/".pdb"/".mol" 结尾的文件路径。未识别则留空 ""。

## 流程开关
- need_protein: 是否需要蛋白处理。涉及蛋白准备、受体清洗、PDB 下载则为 true。
- need_ligand: 是否需要配体处理。涉及配体、SMILES、SDF、mol2、PDBQT 则为 true。
- need_docking: 是否需要对接。涉及对接、dock、vina、binding mode 则为 true。
- need_md: 是否需要 MD 模拟。涉及 MD、动力学、轨迹、模拟则为 true；用户明确说不要 MD 则为 false。
- need_analysis: 是否需要分析。涉及分析、总结、评估、报告则为 true。

## MD 模拟参数（未指定则用默认值）
- md_duration_ns: MD 模拟时长(ns)。如 "5 ns"→5.0, "10 ns"→10.0, "50 ns"→50.0。未指定默认 1.0。
- md_temperature_k: 温度(K)。识别 "300 K"、"310K"、"室温"；未指定默认 300.0。
- md_ensemble: 系综。"NPT"/"NVT"/"NVE" → 小写 npt/nvt/nve；未指定默认 "npt"。"常压"="npt"。
- md_solvent: 溶剂。"显式溶剂"/"TIP3P"/"OPC"/"TIP4P" → explicit/tip3p/opc；"隐式溶剂"/"GB"/"广义玻恩" → implicit；未指定默认 "explicit"。
- md_pressure_atm: 压强(atm)。识别 "1 atm"、"1 bar"；未指定默认 1.0。
- md_timestep_fs: 步长(fs)。识别 "2 fs"、"1 fs"、"4 fs"；未指定默认 2.0。
- md_nvt_equil_ps: NVT 平衡时长(ps)。除非用户明确指定，否则默认 100.0。
- md_npt_equil_ps: NPT 平衡时长(ps)。除非用户明确指定，否则默认 100.0。
- md_save_interval_ps: 轨迹保存间隔(ps)。默认 100.0。
- md_force_field: 蛋白力场。"ff19SB"/"ff14SB"/"ff03" 按原文提取；未指定默认 "ff14SB"。
- md_water_model: 水模型。"OPC"/"TIP4P"/"TIP4P-Ew"/"TIP3P"/"SPC/E" → opc/tip4pew/tip4p/tip3p/spce；未指定默认 "tip3p"。
- md_ligand_ff: 配体力场。"GAFF2"/"gaff2" → gaff2；"GAFF"/"gaff" → gaff；未指定默认 "gaff2"。

## 对接参数（未指定则用默认值）
- docking_mode: 对接模式。"可视对接"/"PyMOL"/"GUI"/"手动对接" → visual_box；未指定默认 "blind" (P2Rank盲对接)。
- docking_exhaustiveness: Vina 精度。"高精度"/"exhaustiveness=16"/"32" → 对应数字；"快速"/"低精度" → 4；未指定默认 8。
- docking_num_modes: 对接模式数。默认 9。
- docking_energy_range: 能量范围(kcal/mol)。默认 3.0。

## 分析开关
- analysis_mmpbsa: 是否尝试 MMPBSA。提到 "MMPBSA"/"结合自由能"/"结合能" → true；未指定默认 false。

## 降级策略
- degradation_instructions: 将用户描述的容错/降级策略整理为文字。识别 "若...则..."、"允许降级"、"若失败"、"超时则" 等模式。每条一行 "- 场景: 操作"。无降级描述则留空 ""。

## 重要
- 以上所有参数如果任务中未明确指定，必须使用上述默认值，不要自行推测。
- 如果当前上下文中包含用户的修改意见（以"用户对上次参数提取的修改意见"开头），则修改意见的优先级高于原始任务描述。

用户需求：{text_value}
"""
    try:
        result = Vavles_llm.invoke(prompt)
        if isinstance(result, Vavles):
            return result
        if isinstance(result, dict):
            return Vavles(**result)
        if hasattr(result, "model_dump"):
            return Vavles(**result.model_dump())
        if hasattr(result, "dict"):
            return Vavles(**result.dict())
    except Exception:
        pass
    return _fallback_task_valves(text_value)


# ============================================================================
# Error analysis (LLM-structured) — replaces is_env_missing + parse_missing_deps
# ============================================================================

class ErrorAnalysis(BaseModel):
    """LLM analyzes tool failure cause and suggests remediation."""
    category: Literal["env_missing", "recoverable", "fatal"]
    env_packages: list[str] = Field(default_factory=list)
    user_message: str = ""
    can_retry: bool = True
    skip_impact: str = ""
    needs_fallback: bool = False


error_analyzer = _llm.with_structured_output(ErrorAnalysis, method="function_calling")


def classify_tool_error(
    calling_node: str, error_text: str, state: AutoMDState,
) -> ErrorAnalysis:
    raw_task = state.get("raw_task", "")
    protein_summary = state.get("protein_summary", "")
    ligand_summary = state.get("ligand_summary", "")
    docking_summary = state.get("docking_summary", "")
    md_summary = state.get("md_summary", "")

    prompt = f"""
你在分子动力学自动化流程的 {calling_node} 节点中处理工具执行失败的情况。

## 任务全貌
- 用户原始需求: {raw_task}
- 当前路由: {state.get('route', '')}
- 蛋白 PDB: {state.get('protein_pdb_id', '')} | 已完成: {state.get('protein_is_success', False)}
- 配体 SMILES: {state.get('ligand_smiles', '')} | 已完成: {state.get('ligand_is_success', False)}
- 对接已完成: {state.get('docking_is_success', False)}
- MD 需要: {state.get('need_md', False)} | MD 已完成: {state.get('md_is_success', False)}
- 需要分析: {state.get('need_analysis', False)}

## 已完成步骤摘要
- 蛋白: {protein_summary}
- 配体: {ligand_summary}
- 对接: {docking_summary}
- MD: {md_summary}

## 当前失败
{calling_node} 节点报错:
{error_text}

## 分析要求
请结合任务全貌判断此失败的实际严重程度：
- env_missing: 缺失外部依赖（conda/pip 包），给出具体包名
- recoverable: 非环境问题但可重试，或者**此步骤的输出对最终报告不是必需的**（例如极小的配体分子无法产生有意义的相互作用分析，此失败可安全跳过）
- fatal: 核心步骤失败且无法绕过（如蛋白下载失败、对接输入文件缺失）

额外判断 needs_fallback:
- 如果失败可通过写脚本自动修复（如 sed 替换残基名、切换电荷方法、换镜像下载），设 needs_fallback=true
- 如果失败只需简单 retry 或安全 skip（如小配体分析失败），设 needs_fallback=false
- 如果失败无法自动修复且 skip 会导致路线断裂，设 needs_fallback=true

输出字段：
- env_packages: category=env_missing 时填写 conda/pip 包名列表
- user_message: 给用户看的友好消息。如果是可安全跳过的非关键步骤，明确说明原因并建议 skip；如果是可重试的，说明 "输入 'retry' 重试，或 'skip' 跳过"
- can_retry: 是否允许用户重试当前步骤
- skip_impact: 跳过此步骤对最终结果的影响简述（如 "无影响，配体过小无法产生有意义的相互作用数据"）
""".strip()
    import nodes.print_utils as pu
    pu.debug(f"LLM 正在分析 {calling_node} 节点的错误...")
    result = error_analyzer.invoke(prompt)
    pu.debug(f"LLM 分析完成: category={result.category}, needs_fallback={result.needs_fallback}")
    return result


def handle_tool_error(
    *,
    result: ToolResult,
    calling_node: str,
    state: AutoMDState,
    retry_goto: str,
    skip_goto: str = "plan_route",
    skip_update: dict | None = None,
) -> Command:
    """Unified tool failure handler.

    Fast path: if result.env_packages is non-empty, route to set_env immediately.
    Slow path: classify via LLM (ErrorAnalysis with full state context), then either set_env or interrupt.
    """
    # Fast path: tool layer already declared missing packages
    if result.env_packages:
        return Command(
            update={
                "package_needs": result.env_packages,
                "calling_node": calling_node,
            },
            goto="set_env",
        )

    # LLM path: structured error classification with full task context
    analysis = classify_tool_error(
        calling_node=calling_node,
        error_text="\n".join(result.errors),
        state=state,
    )

    if analysis.category == "env_missing" and analysis.env_packages:
        return Command(
            update={
                "package_needs": analysis.env_packages,
                "calling_node": calling_node,
            },
            goto="set_env",
        )

    # Deep issue: route to fallback_agent for autonomous diagnosis and repair
    if analysis.needs_fallback:
        return Command(
            update={
                "last_error": "\n".join(result.errors),
                "last_failed_node": calling_node,
                "last_error_analysis": analysis.user_message,
            },
            goto="fallback_agent",
        )

    # Recoverable/fatal without fallback: interrupt with LLM-generated message
    user_choice = interrupt(analysis.user_message)
    if analysis.can_retry and user_choice.strip().lower() == "retry":
        return Command(goto=retry_goto)
    return Command(update=skip_update or {}, goto=skip_goto)


# ============================================================================
# Routing
# ============================================================================

def _format_extraction_summary(update: dict) -> str:
    """Build a human-readable summary of all extracted parameters for user review."""

    def _yn(b: bool) -> str:
        return "✓" if b else "✗"

    def _v(key: str, default: str = "—") -> str:
        val = update.get(key)
        if val is None or val == "":
            return default
        return str(val)

    lines = []
    lines.append("── 任务参数提取 ──\n")

    # Protein / Ligand identifiers
    pdb = _v("protein_pdb_id", "(未识别)")
    smi = _v("ligand_smiles")
    inp = _v("ligand_input_file")
    lines.append(f"  蛋白质 PDB: {pdb}")
    if smi != "—":
        lines.append(f"  配体 SMILES: {smi}")
    if inp != "—":
        lines.append(f"  配体文件: {inp}")

    # Flow valves
    flow = (
        f"蛋白 {_yn(update.get('need_protein'))}  |  "
        f"配体 {_yn(update.get('need_ligand'))}  |  "
        f"对接 {_yn(update.get('need_docking'))}  |  "
        f"MD {_yn(update.get('need_md'))}  |  "
        f"分析 {_yn(update.get('need_analysis'))}"
    )
    lines.append(f"\n  流程: {flow}")

    # MD parameters (only LIVE — each shown value drives actual MD/docking behavior)
    lines.append("\n  MD 参数:")
    lines.append(
        f"    温度: {_v('md_temperature_k')} K    "
        f"时长: {_v('md_duration_ns')} ns"
    )
    lines.append(
        f"    压强: {_v('md_pressure_atm')} atm    "
        f"步长: {_v('md_timestep_fs')} fs\n"
        f"    蛋白力场: {_v('md_force_field')}    "
        f"水模型: {_v('md_water_model')}    "
        f"配体力场: {_v('md_ligand_ff')}"
    )
    equil = (
        f"NVT {_v('md_nvt_equil_ps')} ps / "
        f"NPT {_v('md_npt_equil_ps')} ps"
    )
    lines.append(f"    平衡: {equil}    保存间隔: {_v('md_save_interval_ps')} ps")

    # Docking parameters
    lines.append("\n  对接参数:")
    dm = _v("docking_mode")
    dm_label = {"blind": "P2Rank 盲对接", "visual_box": "PyMOL 可视对接"}.get(dm, dm)
    lines.append(f"    模式: {dm_label}")
    lines.append(
        f"    精度: {_v('docking_exhaustiveness')}    "
        f"模式数: {_v('docking_num_modes')}    "
        f"能量范围: {_v('docking_energy_range')}"
    )

    # Prompt
    lines.append("\n  输入 'yes' 或直接回车确认继续, 或输入修改意见重新提取")

    return "\n".join(lines)


def normalize_task(state: AutoMDState) -> Command:
    raw_task = (state.get("raw_task") or "").strip()
    normalized = re.sub(r"\s+", " ", raw_task)

    # Check for user feedback from a previous extraction round
    feedback = (state.get("_extraction_feedback") or "无").strip()

    # Build LLM context
    if feedback != "无":
        # Re-extraction: only show current state + modification request.
        # raw_task is intentionally excluded — it would interfere by re-introducing
        # original values that the user has already modified.
        current_lines = []
        for key, label in [
            ("protein_pdb_id", "PDB"), ("ligand_smiles", "SMILES"),
            ("ligand_input_file", "输入文件"),
            ("md_duration_ns", "MD时长(ns)"), ("md_temperature_k", "温度(K)"),
            ("md_pressure_atm", "压强(atm)"), ("md_timestep_fs", "步长(fs)"),
            ("md_nvt_equil_ps", "NVT平衡(ps)"), ("md_npt_equil_ps", "NPT平衡(ps)"),
            ("md_save_interval_ps", "保存间隔(ps)"), ("md_force_field", "蛋白力场"),
            ("md_water_model", "水模型"), ("md_ligand_ff", "配体力场"),
            ("docking_mode", "对接模式"), ("docking_exhaustiveness", "对接精度"), ("docking_num_modes", "对接模式数"),
            ("docking_energy_range", "对接能量范围"),
            ("need_protein", "蛋白"), ("need_ligand", "配体"),
            ("need_docking", "对接"), ("need_md", "MD"), ("need_analysis", "分析"),
        ]:
            v = state.get(key)
            if v is not None and v != "":
                current_lines.append(f"  {label}: {v}")

        context = (
            f"当前参数规划:\n"
            + "\n".join(current_lines) + "\n\n"
            f"修改意见:\n{feedback}\n\n"
            f"请根据修改意见调整对应参数。只修改明确提到的参数，其他参数保持当前值不变。"
        )
    else:
        context = normalized

    # 模板化输出的valves
    valves = analyze_task_intent(context)

    # Debug: log LLM raw extraction for troubleshooting
    import nodes.print_utils as pu
    pu.debug(f"LLM valves: pdb={valves.protein_pdb_id!r} smi={valves.ligand_smiles[:30]!r} "
             f"dur={valves.md_duration_ns}ns T={valves.md_temperature_k}K "
             f"dock_exh={valves.docking_exhaustiveness} num_modes={valves.docking_num_modes}")

    # Regex hints: only from feedback when re-extracting; raw_task only for first pass.
    if feedback != "无":
        hints = _extract_numeric_hints(feedback)
    else:
        hints = _extract_numeric_hints(raw_task)

    # Merge: LLM non-default first, then regex hint, then keep current state.
    def _or_current(llm_val, default_val, state_key, hint_key=None):
        if llm_val != default_val:
            return llm_val
        if hint_key and hint_key in hints:
            return hints[hint_key]
        return state.get(state_key, default_val)

    md_duration_ns = _or_current(valves.md_duration_ns, 1.0, "md_duration_ns", "md_duration_ns")
    md_temperature_k = _or_current(valves.md_temperature_k, 300.0, "md_temperature_k", "md_temperature_k")
    md_pressure_atm = _or_current(valves.md_pressure_atm, 1.0, "md_pressure_atm", "md_pressure_atm")
    md_timestep_fs = _or_current(valves.md_timestep_fs, 2.0, "md_timestep_fs", "md_timestep_fs")
    docking_exhaustiveness = _or_current(valves.docking_exhaustiveness, 8, "docking_exhaustiveness", "docking_exhaustiveness")

    # Non-numeric params: keep current state if LLM returned default
    md_nvt_equil_ps = valves.md_nvt_equil_ps if valves.md_nvt_equil_ps != 100.0 else state.get("md_nvt_equil_ps", 100.0)
    md_npt_equil_ps = valves.md_npt_equil_ps if valves.md_npt_equil_ps != 100.0 else state.get("md_npt_equil_ps", 100.0)
    md_save_interval_ps = valves.md_save_interval_ps if valves.md_save_interval_ps != 100.0 else state.get("md_save_interval_ps", 100.0)
    md_force_field = valves.md_force_field if valves.md_force_field != "ff14SB" else state.get("md_force_field", "ff14SB")
    md_water_model = valves.md_water_model if valves.md_water_model != "tip3p" else state.get("md_water_model", "tip3p")
    md_ligand_ff = valves.md_ligand_ff if valves.md_ligand_ff != "gaff2" else state.get("md_ligand_ff", "gaff2")
    docking_mode = valves.docking_mode if valves.docking_mode != "blind" else state.get("docking_mode", "blind")
    docking_num_modes = valves.docking_num_modes if valves.docking_num_modes != 9 else state.get("docking_num_modes", 9)
    docking_energy_range = valves.docking_energy_range if valves.docking_energy_range != 3.0 else state.get("docking_energy_range", 3.0)

    # Booleans without defaults: trust LLM (they are required fields)
    need_protein = valves.need_protein
    need_ligand = valves.need_ligand
    need_docking = valves.need_docking
    need_md = valves.need_md
    need_analysis = valves.need_analysis

    if hints:
        pu.debug(f"Regex override hints applied: {hints}")

    # Identifiers: LLM first, then regex from feedback, then keep current state (NOT raw_task!)
    _pdb_from_fb = extract_pdb_id(feedback) if feedback != "无" else None
    _smi_from_fb = extract_smiles(feedback) if feedback != "无" else None
    _file_from_fb = extract_file_path(feedback) if feedback != "无" else None
    protein_pdb_id = valves.protein_pdb_id or _pdb_from_fb or state.get("protein_pdb_id") or extract_pdb_id(raw_task) or ""
    ligand_smiles = valves.ligand_smiles or _smi_from_fb or state.get("ligand_smiles") or extract_smiles(raw_task) or ""
    ligand_input_file = valves.ligand_input_file or _file_from_fb or state.get("ligand_input_file") or extract_file_path(raw_task) or ""

    pu.debug(f"Merged: pdb={protein_pdb_id!r} dur={md_duration_ns}ns T={md_temperature_k}K")

    project_id = protein_pdb_id or state.get("run_id") or "default"

    update = {
        "raw_task": raw_task,
        "normalized_task": normalized,
        "protein_pdb_id": protein_pdb_id,
        "ligand_smiles": ligand_smiles,
        "project_id": project_id,
        "ligand_input_file": ligand_input_file,
        # Flow valves
        "need_protein": need_protein,
        "need_ligand": need_ligand,
        "need_docking": need_docking,
        "need_md": need_md,
        "need_analysis": need_analysis,
        # MD parameters
        "md_duration_ns": md_duration_ns,
        "md_temperature_k": md_temperature_k,
        "md_pressure_atm": md_pressure_atm,
        "md_timestep_fs": md_timestep_fs,
        "md_nvt_equil_ps": md_nvt_equil_ps,
        "md_npt_equil_ps": md_npt_equil_ps,
        "md_save_interval_ps": md_save_interval_ps,
        "md_force_field": md_force_field,
        "md_water_model": md_water_model,
        "md_ligand_ff": md_ligand_ff,
        # Docking parameters
        "docking_mode": docking_mode,
        "docking_exhaustiveness": docking_exhaustiveness,
        "docking_num_modes": docking_num_modes,
        "docking_energy_range": docking_energy_range,
    }

    # Show extraction results and ask user to confirm
    user_input = interrupt(_format_extraction_summary(update))

    if user_input.strip().lower() in ("yes", "skip"):
        update["_extraction_feedback"] = ""
        return Command(update=update, goto="plan_route")

    # User provided feedback — store it and re-run extraction
    update["_extraction_feedback"] = user_input
    return Command(update=update, goto="normalize_task")


def plan_route(state: AutoMDState) -> Command:
    # Compute what still needs work (valve flag AND not yet done)
    need_protein = bool(state.get("need_protein")) and not state.get("protein_is_success")
    need_ligand = bool(state.get("need_ligand")) and not state.get("ligand_is_success")
    need_docking = bool(state.get("need_docking")) and not state.get("docking_is_success")
    need_md = bool(state.get("need_md")) and not state.get("md_is_success")
    need_analysis = bool(state.get("need_analysis")) and not state.get("analysis_is_success")

    # 🆕 Manual 集群模式 Phase 3 续接: md_already_done valve 或文件系统检测
    # 优先看 valve (用户显式说"继续分析"会触发), 兜底看文件
    if need_md and (state.get("md_already_done") or state.get("_resume_after_md")
                    or _detect_existing_md_result(state)):
        reason = []
        if state.get("md_already_done"):
            reason.append("valve md_already_done=True")
        if state.get("_resume_after_md"):
            reason.append("_resume_after_md 标记")
        if _detect_existing_md_result(state):
            reason.append("检测到现有 MD 产物文件")
        return Command(
            update={
                "route": "analysis-resume",
                "route_reason": f"手动集群模式续接: {'; '.join(reason)}",
                "md_is_success": True,  # 标记 MD 已完成 (虽然实际是用户跑的)
            },
            goto="trajectory_analysis",
        )

    # Shortcut: only MD remains
    if need_md and not (need_protein or need_ligand or need_docking):
        return Command(
            update={"route": "md", "route_reason": "仅需 MD，直达 md_preflight"},
            goto="md_preflight",
        )

    # Shortcut: only analysis remains
    if need_analysis and not (need_protein or need_ligand or need_docking or need_md):
        return Command(
            update={"route": "analysis", "route_reason": "valves: analysis-only"},
            goto="report",
        )

    # Determine route name and pick the earliest incomplete step
    if need_docking or need_md:
        route = "both"
        if need_protein:
            next_node = "protein_fetch"
        elif need_ligand:
            next_node = "ligand_resolve"
        else:
            next_node = "merge_inputs"
    elif need_protein and need_ligand:
        route = "both"
        next_node = "protein_fetch"
    elif need_protein:
        route = "protein"
        next_node = "protein_fetch"
    elif need_ligand:
        route = "ligand"
        next_node = "ligand_resolve"
    else:
        return Command(
            update={
                "route": "done",
                "route_reason": "所有步骤已完成或已跳过",
            },
            goto="report",
        )

    return Command(
        update={
            "route": route,
            "route_reason": f"valves: protein={need_protein}, ligand={need_ligand}, docking={need_docking}, md={need_md}, analysis={need_analysis}",
        },
        goto=next_node,
    )


# ============================================================================
# Report
# ============================================================================

def final_report_from_state(state: AutoMDState) -> str:
    lines = ["《执行报告》"]
    lines.append(f"任务: {state.get('raw_task', '')}")
    if state.get("route"):
        lines.append(f"路由: {state.get('route')}")
    # 🆕 修: graph.py 节点填的是 *_summary 字段, 优先用 *_summary (节点摘要)
    for k, label in [
        ("protein_summary", "蛋白"),
        ("ligand_summary", "配体"),
        ("docking_summary", "对接"),
        ("md_summary", "MD"),
        ("analysis_summary", "分析"),
    ]:
        v = state.get(k)
        if v:
            lines.append(f"{label}: {v}")
    # *_result 才是文件路径, 列在下面作产物路径
    for k, label in [
        ("protein_result", "蛋白产物路径"),
        ("ligand_result", "配体产物路径"),
        ("docking_result", "对接产物路径"),
        ("md_result", "MD产物路径"),
        ("analysis_result", "分析产物路径"),
    ]:
        v = state.get(k)
        if v:
            lines.append(f"{label}: {v}")
    return "\n".join(lines)
