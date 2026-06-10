"""
FallbackAgent v3: 双 LLM 架构。

diagnosis_llm (bind_tools) → 读文件, 输出技术报告
action_llm  (structured_output) → 技术报告+规范 → FallbackAction
四阶段: diagnose → decide → confirm → execute

保留了: interrupt 展示脚本+风险, expected_outputs 验证,
失败完整反馈, 3次耗尽后 interrupt(retry/skip/escalate)
"""

from __future__ import annotations
from typing import Literal

import json
import os
import sys
import textwrap
from pathlib import Path

nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.types import Command, interrupt
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from State import AutoMDState
from tools.system_tools import run_shell_command, read_text_file

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
        spec = importlib.util.spec_from_file_location("nodes.common", os.path.join(nd, "common.py"))
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)
        _llm = common._llm

# LLM prompts loaded from prompt/fallback.md (project root).
# project_root is already on sys.path (added above).
from prompt import load as _load_prompt

import nodes.print_utils as pu

_MAX_TOOL_ROUNDS = 10 # 诊断阶段最多调用工具的轮数，超过则强制 summarizer 收尾，避免上下文爆炸
_MAX_ATTEMPTS = 5 # 最大尝试次数，超过则认为问题无法自动修复，需人工介入

_NEXT_NODE_MAP: dict[str, str] = {
    "protein_fetch": "protein_clean", "protein_clean": "tleap_prep",
    "tleap_prep": "protein_receptor_prep", "protein_receptor_prep": "protein_qa",
    "protein_qa": "merge_inputs",
    "ligand_resolve": "ligand_to_3d", "ligand_to_3d": "ligand_antechamber",
    "ligand_antechamber": "ligand_parmchk", "ligand_parmchk": "ligand_tleap",
    "ligand_tleap": "ligand_pdbqt", "ligand_pdbqt": "ligand_qa",
    "ligand_qa": "merge_inputs",
    "merge_inputs": "pocket_detection",
    "visual_docking": "docking_setup",
    "pocket_detection": "docking_setup",
    "docking_setup": "docking_run", "docking_run": "docking_evaluation",
    "docking_evaluation": "complex_prep", "complex_prep": "md_preflight",
    "md_preflight": "md_run", "md_run": "trajectory_analysis",
    "trajectory_analysis": "md_plot", "md_plot": "report",
    "submit_to_cluster": "trajectory_analysis",
}


def _next_node_after(node_name: str, state: "AutoMDState | None" = None) -> str:
    """Return the typical next node after `node_name` for LLM hinting.

    For nodes with conditional routing, the answer depends on state:
      - merge_inputs: visual_docking (docking_mode='visual_box') else pocket_detection
      - md_preflight: submit_to_cluster (if submit_to_cluster flag) else md_run
    Multi-path routers like protein_qa / ligand_qa are documented in the LLM
    prompt instead of being hard-coded here.
    """
    if state is not None:
        if node_name == "merge_inputs" and state.get("docking_mode") == "visual_box":
            return "visual_docking"
        if node_name == "md_preflight" and state.get("submit_to_cluster"):
            return "submit_to_cluster"
    return _NEXT_NODE_MAP.get(node_name, "plan_route")


@tool
def read_text_file_tool(path: str, max_chars: int = 15000) -> str:
    """Read a file within the project. path: absolute or relative path."""
    r = read_text_file(path, max_chars=max_chars)
    if hasattr(r, "format_for_agent"):
        return r.format_for_agent()
    return str(r)


# Phase 1: explore files → natural language technical report
diagnosis_llm = _llm.bind_tools([read_text_file_tool])


class FallbackAction(BaseModel):
    """结构化决策输出。"""
    thought: str = Field(description="诊断推理过程")
    action: Literal["run_script", "reroute", "escalate"] = Field(description="行动类型")
    script: str = Field(default="", description="bash 脚本")
    script_description: str = Field(default="", description="脚本功能说明")
    state_updates: dict = Field(default_factory=dict, description="要写入 state 的字段")
    expected_outputs: list[str] = Field(default_factory=list, description="脚本即刻生成的文件（含启动标记文件）")
    final_outputs: list[str] = Field(default_factory=list, description="TMUX 后台任务完成后的最终产出文件。仅 tmux 模式下填写")
    next_node: str = Field(default="", description="成功后跳转的目标节点")
    user_summary: str = Field(default="", description="用户可见的总结")


# Phase 2: structured decision from technical report
action_llm = _llm.with_structured_output(FallbackAction, method="function_calling")

DIAGNOSIS_SYSTEM = _load_prompt("fallback", "DIAGNOSIS_SYSTEM")

ACTION_DECIDE_PROMPT = _load_prompt("fallback", "ACTION_DECIDE_PROMPT")


def _parse_tmux_session(script: str) -> str | None:
    """从脚本中提取 tmux 会话名（拒绝变量名，剥离引号）。"""
    import re
    m = re.search(r'tmux\s+new-session\s+.*?-s\s+(\S+)', script)
    if not m:
        return None
    name = m.group(1).strip("'\"")
    if "$" in name:
        return None
    return name


def _build_diagnosis_prompt(state: AutoMDState) -> str:
    """Build the diagnosis LLM user message by formatting the
    ACTION_CURRENT_FAILURE template from prompt/fallback.md with state context.
    """
    last_failed_node = state.get("last_failed_node") or state.get("calling_node", "unknown")
    last_error_analysis = state.get("last_error_analysis", "")
    fallback_count = state.get("fallback_count", 0)
    history = state.get("_fallback_history") or []
    _original_error = state.get("_original_error", "")
    project_id = state.get("project_id", "default")

    template = _load_prompt("fallback", "ACTION_CURRENT_FAILURE")
    base = template.format(
        last_failed_node=last_failed_node,
        _next_node_after=_next_node_after(last_failed_node, state),
        fallback_count=fallback_count + 1,
        _MAX_ATTEMPTS=_MAX_ATTEMPTS,
        last_error_analysis=last_error_analysis or "(无)",
        _original_error=_original_error or "(无)",
        raw_task=state.get("raw_task", ""),
        ligand_smiles=state.get("ligand_smiles", ""),
        protein_pdb_id=state.get("protein_pdb_id", ""),
        project_id=project_id,
        protein_filtered_pdb=state.get("protein_filtered_pdb", ""),
        protein_prmtop=state.get("protein_prmtop", ""),
        ligand_input_file=state.get("ligand_input_file", ""),
        ligand_mol2=state.get("ligand_mol2", ""),
        ligand_frcmod=state.get("ligand_frcmod", ""),
        ligand_prmtop=state.get("ligand_prmtop", ""),
        docked_ligand_pdb=state.get("docked_ligand_pdb", ""),
        md_prmtop=state.get("md_prmtop", ""),
        md_inpcrd=state.get("md_inpcrd", ""),
        complex_is_success=state.get("complex_is_success", False),
    )

    if not history:
        return base + textwrap.dedent("""
        请调用 read_text_file_tool 检查相关文件（特别是失败节点的日志文件），然后输出技术诊断报告。
        ## 输出格式要求
        请严格按照系统提示中的格式输出技术诊断报告，确保包含以下要素：
        **错误日志摘要**、**文件状态**、**根本原因**、**修复可行性**、**修复策略**（Level 1/2/3）、**修复建议**。
        """)

    history_block = ["## 之前的修复尝试 (请基于这些信息选择不同策略)\n"]
    for h in history:
        history_block.append(f"### 尝试 {h.get('attempt','?')}")
        history_block.append(f"- 技术诊断: {h.get('tech_report','')}")
        history_block.append(f"- 执行脚本: {h.get('script_description','')}")
        history_block.append(f"- 脚本内容:\n```bash\n{h.get('script','')}\n```")
        history_block.append(f"- 失败输出:\n```\n{h.get('failure_output','')}\n```")
        history_block.append(f"- 缺失文件: {h.get('missing_files',[])}")
        history_block.append("")
    history_block.append(textwrap.dedent("""
    → 如果需要，你可以调用 read_text_file_tool 读取关键文件（尤其是失败节点的日志文件，如 topology/tleap.log、leap.log、run.log），因为这些信息可能在历史输出中不完整。
    请基于读取到的具体错误内容输出诊断报告。
    如果上次是调参 (Level 1)，请尝试修复中间文件 (Level 2) 或替代工具 (Level 3)。

    ## 输出格式要求
    请严格按照系统提示中的格式输出技术诊断报告，确保包含以下要素：
    **错误日志摘要**（复制关键错误行）、**文件状态**、**根本原因**、**修复可行性**、**修复策略**（Level 1/2/3）、**修复建议**。
    """).strip())
    return base + "\n\n" + "\n".join(history_block)


def _build_decide_prompt(state: AutoMDState, tech_report: str) -> str:
    """Build the action LLM user message by formatting the
    ACTION_FAILED_NODE template from prompt/fallback.md with state context.
    """
    last_failed_node = state.get("last_failed_node") or state.get("calling_node", "unknown")
    project_id = state.get("project_id", "default")
    template = _load_prompt("fallback", "ACTION_FAILED_NODE")
    return template.format(
        last_failed_node=last_failed_node,
        _next_node_after=_next_node_after(last_failed_node, state),
        tech_report=tech_report,
        protein_pdb_id=state.get("protein_pdb_id", ""),
        protein_filtered_pdb=state.get("protein_filtered_pdb", ""),
        protein_prmtop=state.get("protein_prmtop", ""),
        ligand_smiles=state.get("ligand_smiles", ""),
        ligand_mol2=state.get("ligand_mol2", ""),
        ligand_frcmod=state.get("ligand_frcmod", ""),
        ligand_prmtop=state.get("ligand_prmtop", ""),
        ligand_input_file=state.get("ligand_input_file", ""),
        docked_ligand_pdb=state.get("docked_ligand_pdb", ""),
        md_prmtop=state.get("md_prmtop", ""),
        md_inpcrd=state.get("md_inpcrd", ""),
        complex_is_success=state.get("complex_is_success", False),
        project_id=project_id,
    )


def _assess_risk(script: str) -> str:
    s = script.lower()
    risks: list[str] = []
    if "rm -rf" in s or "rm -r" in s: risks.append("删除文件/目录")
    if "sudo" in s: risks.append("提权操作")
    if "conda install" in s or "pip install" in s: risks.append("安装软件包")
    if "chmod" in s or "chown" in s: risks.append("修改文件权限")
    return "中 — " + ", ".join(risks) if risks else "低 — 标准文件生成操作"


def _execute_and_verify(script: str, description: str, expected_outputs: list[str]) -> tuple:
    if not script.strip():
        return False, "script 为空", []
    pu.debug(f"执行: {description or script[:120]}")
    r = run_shell_command(script.strip(), timeout_seconds=600)
    if not r.ok:
        output = "\n".join(r.errors) if r.errors else str(r.data or "")
        return False, output, []
    data = r.data if isinstance(r.data, dict) else {}
    exit_code = data.get("exit_code", -1)
    output = data.get("output", str(r.data))
    if exit_code != 0:
        return False, output, []
    _SIZE_RULES = [
        (lambda f: f.endswith(".prmtop"), 50),
        (lambda f: f.endswith(".inpcrd"), 10),
    ]

    missing: list[str] = []
    for f in expected_outputs:
        if not f or not os.path.exists(f):
            missing.append(f)
            continue
        size = os.path.getsize(f)
        if size == 0 and f.endswith(".frcmod"):
            continue  # 空 frcmod 合法, GAFF 有内置参数
        if size == 0 and (f.endswith(".mol2") or f.endswith(".pdb") or f.endswith(".pdbqt")):
            missing.append(f"{f} (0B)")
            continue
        for check, min_sz in _SIZE_RULES:
            if check(f) and size < min_sz:
                missing.append(f"{f} ({size}B < {min_sz}B)")
                break
    if missing:
        return False, f"验证失败: {missing}", missing
    return True, output, []


def _serialize_action(action: FallbackAction) -> str:
    return json.dumps({
        "thought": action.thought, "action": action.action,
        "script": action.script, "script_description": action.script_description,
        "state_updates": action.state_updates, "expected_outputs": action.expected_outputs,
        "final_outputs": action.final_outputs,
        "next_node": action.next_node, "user_summary": action.user_summary,
    })


def _deserialize_action(raw: str) -> FallbackAction | None:
    try:
        return FallbackAction(**json.loads(raw))
    except (json.JSONDecodeError, KeyError):
        return None


def fallback_agent(state: AutoMDState) -> Command:
    """兜底 Agent v3: 双 LLM 四阶段架构."""

    last_failed_node = state.get("last_failed_node") or state.get("calling_node", "unknown")
    fallback_count = state.get("fallback_count", 0)
    phase = state.get("_fallback_phase", "diagnose")

    # 首次触发保存原始错误，retry 时不变
    history = state.get("_fallback_history") or []
    _original_error = state.get("_original_error", "")
    if not history and not _original_error:
        _original_error = state.get("last_error", "")

    # ---- Phase: DIAGNOSE + DECIDE ----
    if phase == "diagnose":
        pu.debug(f"[{last_failed_node}] 第{fallback_count+1}次诊断")
        pu.step("自动修复", f"第{fallback_count+1}次尝试")

        # Step 1: diagnosis_llm explores files, outputs technical report
        diagnosis_msgs: list = [
            SystemMessage(content=DIAGNOSIS_SYSTEM),
            HumanMessage(content=_build_diagnosis_prompt(state)),
        ]

        tech_report = ""
        try:
            for _ in range(_MAX_TOOL_ROUNDS):
                r = diagnosis_llm.invoke(diagnosis_msgs)
                diagnosis_msgs.append(r)
                if r.tool_calls:
                    for tc in r.tool_calls:
                        args = tc.get("args", {})
                        try:
                            content = read_text_file_tool.invoke(args)
                        except Exception as exc:
                            content = f"读取失败: {exc}"
                        diagnosis_msgs.append(ToolMessage(content=str(content), tool_call_id=tc["id"]))
                else:
                    tech_report = r.content or str(r)
                    break
            # 诊断结束但未产生技术报告,也就是前面一直都是tool_calls
            # 强行使用上下文最后一轮的内容作为技术报告
            # 仅当前面没拿到技术报告时，才启用无工具 summarizer
            if not tech_report.strip():
                diagnosis_summarizer_llm = _llm  # 不绑定工具
                summarize_msgs = diagnosis_msgs + [
                    HumanMessage(content="你现在不能调用任何工具。请基于以上对话和工具结果，直接输出“技术诊断报告”。")
                ]
                r = diagnosis_summarizer_llm.invoke(summarize_msgs)
                tech_report = (r.content or "").strip()

            if not tech_report:
                tech_report = "诊断未产生技术报告。请基于原始错误做最佳尝试。"


            
        except Exception as exc:
            return Command(update={"fallback_result": f"诊断 LLM 失败: {exc}", "fallback_count": fallback_count + 1}, goto="report")

        pu.debug(f"技术报告: {tech_report[:1000]}...")

        # Step 2: action_llm makes structured decision
        try:
            action: FallbackAction = action_llm.invoke(ACTION_DECIDE_PROMPT + "\n\n" + _build_decide_prompt(state, tech_report))
        except Exception as exc:
            pu.debug(f"决策 LLM 失败: {exc}")
            pu.debug("正在进行重试...")
            if fallback_count + 1 >= _MAX_ATTEMPTS:
                return Command(update={"fallback_result": f"决策 LLM 失败且已达最大尝试次数: {exc}", "fallback_count": fallback_count + 1, "analysis_result": "[需人工介入]", "analysis_is_success": False}, goto="report")
            else:
                return Command(update={"fallback_result": f"决策 LLM 失败: {exc}", "fallback_count": fallback_count + 1}, goto="fallback_agent")

        pu.debug(f"action={action.action}, next_node={action.next_node or '(default)'}")

        if action.action == "reroute":
            next_node = action.next_node or _next_node_after(last_failed_node, state)
            return Command(update={**(action.state_updates or {}), "fallback_count": 0, "_fallback_phase": "diagnose", "_fallback_history": [], "_original_error": "", "_last_tech_report": "", "fallback_result": action.thought}, goto=next_node if next_node != last_failed_node else "plan_route")

        if action.action == "escalate":
            return Command(update={"fallback_count": fallback_count + 1, "_fallback_history": [], "_original_error": "", "_last_tech_report": "", "fallback_result": action.user_summary, "analysis_result": f"[需人工介入] {action.user_summary}", "analysis_is_success": False}, goto="report")

        # run_script → enter confirm phase
        return Command(update={"_fallback_phase": "confirm", "_fallback_action_raw": _serialize_action(action), "_last_tech_report": tech_report, "_original_error": _original_error, "fallback_count": fallback_count}, goto="fallback_agent")

    # ---- Phase: CONFIRM + EXECUTE ----
    if phase == "confirm":
        action = _deserialize_action(state.get("_fallback_action_raw", ""))
        if action is None:
            return Command(update={"_fallback_phase": "diagnose"}, goto="fallback_agent")

        pu.debug(f"修复脚本 ({action.script_description}):\n{action.script}")
        user_input = interrupt(
            f"[FallbackAgent 修复方案] (第 {fallback_count+1}/{_MAX_ATTEMPTS} 次)\n\n"
            f"目标: 完全替代 {last_failed_node} 的产出物\n"
            f"操作: {action.script_description}\n\n"
            f"预期生成文件:\n  " + "\n  ".join(action.expected_outputs or ["(无)"]) + "\n\n"
            f"后台最终文件 (TMUX 完成后):\n  " + "\n  ".join(action.final_outputs or ["(无)"]) + "\n\n"
            f"风险评估: {_assess_risk(action.script)}\n\n"
            f"脚本内容:\n```bash\n{action.script}\n```\n\n"
            f"输入 'yes' 执行此脚本, 'no' 拒绝（将重新诊断）"
        )

        codepoints = " ".join(f"U+{ord(ch):04X}" for ch in str(user_input))
        pu.debug(f"[fallback_agent] confirm reply raw={user_input!r} strip={str(user_input).strip()!r} lower={str(user_input).strip().lower()!r} len={len(str(user_input))} codepoints={codepoints}")

        if user_input.strip().lower() != "yes":
            history.append({
                "attempt": fallback_count + 1,
                "tech_report": state.get("_last_tech_report", ""),
                "script_description": "用户拒绝: " + action.script_description,
                "script": "(未执行)",
                "failure_output": "用户拒绝执行",
                "missing_files": [],
            })
            return Command(update={"_fallback_phase": "diagnose", "_fallback_history": history, "last_error": "用户拒绝了上次修复方案。请用完全不同的策略重新设计。", "fallback_count": fallback_count, "_original_error": _original_error}, goto="fallback_agent")

        ok, output, missing = _execute_and_verify(action.script, action.script_description, action.expected_outputs)

        if ok:
            # 检测是否需要 tmux 后台等待
            tmux_session = _parse_tmux_session(action.script)
            if tmux_session and action.final_outputs:
                pu.debug(f"tmux 检测: 会话={tmux_session}, 等待最终文件")
                return Command(update={
                    "_fallback_phase": "waiting",
                    "_tmux_session": tmux_session,
                    "_waiting_next_node": action.next_node or _next_node_after(last_failed_node, state),
                    "_waiting_final_outputs": action.final_outputs,
                    "_waiting_state_updates": action.state_updates or {},
                    "fallback_count": fallback_count,
                }, goto="fallback_agent")

            next_node = action.next_node or _next_node_after(last_failed_node, state)
            if next_node == last_failed_node:
                next_node = _next_node_after(last_failed_node, state)
            pu.debug(f"成功 → 跳 {next_node}")
            return Command(update={**(action.state_updates or {}), "fallback_count": 0, "_fallback_phase": "diagnose", "_fallback_action_raw": "", "_fallback_history": [], "_original_error": "", "_last_tech_report": "", "fallback_result": f"修复成功 → {next_node}: {action.script_description}"}, goto=next_node)

        new_count = fallback_count + 1
        history.append({
            "attempt": new_count,
            "tech_report": state.get("_last_tech_report", ""),
            "script_description": action.script_description,
            "script": action.script,
            "failure_output": output,
            "missing_files": [str(m) for m in missing],
        })

        if new_count >= _MAX_ATTEMPTS:
            attempts_text = "\n".join(
                f"  [{h.get('attempt','?')}] {h.get('script_description','')} → {h.get('failure_output','')[:1000]}"
                for h in history
            )
            user_choice = interrupt(
                f"[FallbackAgent] 已达最大尝试次数 ({_MAX_ATTEMPTS})\n\n"
                f"已尝试方案:\n{attempts_text}\n\n最后错误: {output}\n\n"
                f"输入 'retry' 重新尝试, 'skip' 跳过 {last_failed_node}, 'escalate' 终止并输出报告"
            )
            choice = user_choice.strip().lower()
            if "skip" in choice:
                return Command(update={"fallback_count": 0, "_fallback_phase": "diagnose", "_fallback_action_raw": "", "_fallback_history": [], "_original_error": "", "_last_tech_report": "", "fallback_result": f"用户跳过 {last_failed_node}"}, goto="plan_route")
            if "retry" in choice:
                return Command(update={"fallback_count": 0, "_fallback_phase": "diagnose", "_fallback_action_raw": ""}, goto="fallback_agent")
            return Command(update={"fallback_count": new_count, "fallback_result": "用户 escalates", "analysis_result": "[需人工介入]", "analysis_is_success": False}, goto="report")

        pu.debug(f"失败 ({new_count}/{_MAX_ATTEMPTS}) → 重新诊断")
        return Command(update={"_fallback_phase": "diagnose", "_fallback_action_raw": "", "fallback_count": new_count, "_fallback_history": history, "last_error": output, "last_failed_node": last_failed_node, "_original_error": _original_error}, goto="fallback_agent")

    # ---- Phase: WAITING (tmux 后台任务) ----
    if phase == "waiting":
        import subprocess as _sp

        session = state.get("_tmux_session", "")
        final_outputs = state.get("_waiting_final_outputs") or []
        next_node = state.get("_waiting_next_node", "trajectory_analysis")
        state_updates = state.get("_waiting_state_updates") or {}

        r = _sp.run(["tmux", "has-session", "-t", session], capture_output=True)

        # 主检查失败 → 二次容错: 可能会话名含变量未展开, 查 list-sessions
        if r.returncode != 0:
            ls = _sp.run(["tmux", "list-sessions"], capture_output=True, text=True)
            if ls.returncode == 0 and ls.stdout.strip():
                lines = ls.stdout.strip().split("\n")
                names = [l.split(":")[0].strip() for l in lines if ":" in l]
                if names:
                    pu.debug(f"has-session 失败, list-sessions 找到: {names}")
                    # 用第一个匹配项或提示用户选择
                    for name in names:
                        if session.strip("'\"") in name or name in session.strip("'\""):
                            session = name
                            pu.debug(f"匹配到运行中的会话: {session}")
                            r.returncode = 0  # 视为仍在运行

        if r.returncode == 0:
            user_input = interrupt(
                f"tmux 会话 '{session}' 仍在运行中。\n"
                f"请等待，回复 'check' 可直接检查状态。\n"
                f"或回复 'abort' 放弃等待。"
            )
            if user_input.strip().lower() == "abort":
                return Command(update={
                    "_fallback_phase": "diagnose",
                    "_tmux_session": "", "_waiting_next_node": "",
                    "_waiting_final_outputs": [], "_waiting_state_updates": {},
                    "fallback_result": "用户中止等待",
                }, goto="plan_route")
            return Command(update={"_tmux_session": session}, goto="fallback_agent")

        # 会话已结束 → 验证最终文件
        missing = [f for f in final_outputs
                   if not os.path.exists(f) or os.path.getsize(f) == 0]
        if missing:
            history.append({
                "attempt": fallback_count + 1,
                "tech_report": state.get("_last_tech_report", ""),
                "script_description": f"TMUX 会话 {session} 已结束但文件缺失",
                "script": "(tmux background)",
                "failure_output": f"缺失文件: {missing}",
                "missing_files": [str(m) for m in missing],
            })
            pu.debug(f"tmux 结束但文件缺失: {missing}")
            return Command(update={
                "_fallback_phase": "diagnose",
                "_tmux_session": "", "_waiting_next_node": "",
                "_waiting_final_outputs": [], "_waiting_state_updates": {},
                "fallback_count": fallback_count + 1,
                "_fallback_history": history,
                "last_error": f"TMUX 会话结束但文件缺失: {missing}",
                "_original_error": _original_error,
            }, goto="fallback_agent")

        # 成功
        pu.debug(f"tmux 完成 → 跳 {next_node}")
        return Command(update={
            **state_updates,
            "fallback_count": 0,
            "_fallback_phase": "diagnose",
            "_fallback_action_raw": "",
            "_fallback_history": [], "_original_error": "", "_last_tech_report": "",
            "_tmux_session": "", "_waiting_next_node": "",
            "_waiting_final_outputs": [], "_waiting_state_updates": {},
            "fallback_result": f"修复成功 → {next_node}",
        }, goto=next_node)

    return Command(goto="report")


if __name__ == "__main__":
    from langgraph.graph import END, START, StateGraph
    graph = StateGraph(AutoMDState)
    graph.add_node("fallback_agent", fallback_agent)
    graph.add_edge(START, "fallback_agent")
    graph.add_edge("fallback_agent", END)
    app = graph.compile()
    test_state = {
        "raw_task": "对接 PDB 1A2B 与 SMILES CCO 的配体", "route": "both",
        "calling_node": "docking_evaluation", "protein_pdb_id": "1A2B",
        "protein_is_success": True, "ligand_smiles": "CCO", "ligand_is_success": True,
        "docking_is_success": True, "need_protein": True, "need_ligand": True,
        "need_docking": True, "need_md": False, "need_analysis": True, "fallback_count": 0,
    }
    print("=== FallbackAgent v3 独立测试 ===\n")
    result = app.invoke(test_state)
    print(f"\n--- fallback_result ---\n{result.get('fallback_result', '(empty)')[:500]}")
