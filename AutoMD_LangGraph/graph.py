from __future__ import annotations

import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# Ensure package root is on sys.path when running this module directly
this_dir = os.path.dirname(__file__)
package_root = os.path.abspath(this_dir)
if package_root not in sys.path:
    sys.path.insert(0, package_root)

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv
load_dotenv()

from State import AutoMDState
from nodes.set_env import set_env
from nodes.fallback_agent import fallback_agent
from nodes.submit import submit_to_cluster
from nodes import (
    complex_prep,
    docking_evaluation,
    docking_run,
    docking_setup,
    ligand_resolve,
    ligand_to_3d,
    ligand_antechamber,
    ligand_parmchk,
    ligand_tleap,
    ligand_pdbqt,
    ligand_qa,
    md_plot,
    md_preflight,
    md_run,
    merge_inputs,
    normalize_task,
    plan_route,
    pocket_detection,
    protein_clean,
    protein_fetch,
    protein_qa,
    protein_receptor_prep,
    tleap_prep,
    report,
    trajectory_analysis,
    visual_docking,
)

import nodes.print_utils as pu

NODE_LABELS = {
    "normalize_task": "任务分析",
    "plan_route": "流程规划",
    "protein_fetch": "蛋白下载",
    "protein_clean": "蛋白清洗",
    "tleap_prep": "拓扑生成",
    "protein_receptor_prep": "受体准备",
    "protein_qa": "蛋白质检",
    "ligand_resolve": "配体解析",
    "ligand_to_3d": "配体3D化",
    "ligand_antechamber": "电荷计算",
    "ligand_parmchk": "参数检查",
    "ligand_tleap": "配体拓扑",
    "ligand_pdbqt": "配体PDBQT",
    "ligand_qa": "配体质检",
    "merge_inputs": "输入汇合",
    "visual_docking": "可视对接",
    "pocket_detection": "口袋检测",
    "docking_setup": "对接准备",
    "docking_run": "对接运行",
    "docking_evaluation": "对接评估",
    "complex_prep": "复合物准备",
    "md_preflight": "MD预检",
    "md_run": "MD模拟",
    "trajectory_analysis": "轨迹分析",
    "md_plot": "图表生成",
    "set_env": "环境配置",
    "fallback_agent": "自动修复",
    "submit_to_cluster": "集群提交",
    "report": "报告生成",
}


def build_graph(checkpointer=None):
    graph = StateGraph(AutoMDState)

    graph.add_node("normalize_task", normalize_task)
    graph.add_node("plan_route", plan_route)
    graph.add_node("protein_fetch", protein_fetch)
    graph.add_node("protein_clean", protein_clean)
    graph.add_node("tleap_prep", tleap_prep)
    graph.add_node("protein_receptor_prep", protein_receptor_prep)
    graph.add_node("protein_qa", protein_qa)
    graph.add_node("ligand_resolve", ligand_resolve)
    graph.add_node("ligand_to_3d", ligand_to_3d)
    graph.add_node("ligand_antechamber", ligand_antechamber)
    graph.add_node("ligand_parmchk", ligand_parmchk)
    graph.add_node("ligand_tleap", ligand_tleap)
    graph.add_node("ligand_pdbqt", ligand_pdbqt)
    graph.add_node("ligand_qa", ligand_qa)
    # ligand nodes added above
    graph.add_node("merge_inputs", merge_inputs)
    graph.add_node("visual_docking", visual_docking)
    graph.add_node("pocket_detection", pocket_detection)
    graph.add_node("docking_setup", docking_setup)
    graph.add_node("docking_run", docking_run)
    graph.add_node("docking_evaluation", docking_evaluation)
    graph.add_node("complex_prep", complex_prep)
    graph.add_node("md_preflight", md_preflight)
    graph.add_node("md_run", md_run)
    graph.add_node("trajectory_analysis", trajectory_analysis)
    graph.add_node("md_plot", md_plot)
    graph.add_node("set_env", set_env)
    graph.add_node("fallback_agent", fallback_agent)
    graph.add_node("submit_to_cluster", submit_to_cluster)
    graph.add_node("report", report)

    graph.add_edge(START, "normalize_task")
    graph.add_edge("report", END)

    return graph.compile(checkpointer=checkpointer)


def run_automd(raw_task: str, thread_id: str = "main"):
    """Generator: yields structured dicts for each workflow event.

    Yield types: section, step, interrupt, report.
    For interrupts, caller must send() user input back via generator:
        gen.send(user_input) after receiving {"type":"interrupt",...}

    Log file is written alongside via print_utils.
    """
    # ── Log file ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(thread_id)) or "main"
    log_dir = Path(__file__).resolve().parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{safe}_{ts}.log"
    log_file = open(str(log_path), "w", encoding="utf-8", buffering=1)
    pu.set_log_file(log_file)
    from tools.shared import set_tool_debug_writer
    set_tool_debug_writer(pu.debug)
    pu.set_web_mode(True)
    pu.debug(f"日志文件: {log_path}")

    config = {"configurable": {"thread_id": thread_id}}
    checkpointer = MemorySaver()
    app = build_graph(checkpointer=checkpointer)
    pu.debug("Graph compiled successfully")

    state: dict = {"raw_task": raw_task, "run_id": thread_id}
    state["submit_to_cluster"] = os.getenv("USE_CLUSTER", "false").lower() == "true"

    def _log_yield(msg: dict) -> None:
        t = msg.get("type", "")
        if t == "section": pu.debug(f"section: {msg.get('text','')}")
        elif t == "step": pu.debug(f"▶ {msg.get('label','')}  {msg.get('detail','')}")
        elif t == "interrupt": pu.debug(f"[interrupt] {textwrap.shorten(str(msg.get('text','')),300)}")
        elif t == "report": pu.debug(f"[报告] {len(msg.get('state',{}))} 字段")

    def _emit_step(node_name, up):
        label = NODE_LABELS.get(node_name, node_name)
        detail = ""
        for k in ("protein_summary","ligand_summary","docking_summary","md_summary",
                   "fallback_result","route","analysis_summary"):
            if up.get(k): detail = str(up[k]); break
        return label, detail

    # Phase 1
    m = {"type": "section", "text": "**开始执行**"}; yield m; _log_yield(m)
    for event in app.stream(state, config, stream_mode="updates"):
        for node_name, up in event.items():
            if node_name == "__interrupt__" or not isinstance(up, dict): continue
            state.update(up)
            label, detail = _emit_step(node_name, up)
            m = {"type": "step", "label": label, "detail": detail}; yield m; _log_yield(m)

    # Phase 2: interrupts
    while True:
        snapshot = app.get_state(config)
        ivals = list(snapshot.interrupts) if snapshot and snapshot.interrupts else []
        if not ivals: break
        for ir in ivals:
            m = {"type": "interrupt", "text": str(ir.value)}
            user_input = yield m
            _log_yield(m)
            if not user_input:
                user_input = "skip"
            pu.debug(f"[run_automd] interrupt reply received: raw={user_input!r} strip={str(user_input).strip()!r}")
            for event in app.stream(Command(resume=user_input), config, stream_mode="updates"):
                for node_name, up in event.items():
                    if node_name == "__interrupt__" or not isinstance(up, dict): continue
                    state.update(up)
                    label, detail = _emit_step(node_name, up)
                    m = {"type": "step", "label": label, "detail": detail}; yield m; _log_yield(m)
            # Sync local state from checkpoint (fixes fallback_agent count drift)
            cur = app.get_state(config)
            if cur and cur.values: state.update(cur.values)

    # Report
    final = app.get_state(config)
    state = final.values if final else state
    m = {"type": "report", "state": state}; yield m; _log_yield(m)
    pu.set_log_file(None)
    log_file.close()
    return state

def test_garph(raw_task: str):
    """Run the LangGraph workflow directly for local validation.

    This helper avoids the web/UI layer and only exercises the graph
    compilation + streaming path. If the graph emits interrupts, they are
    auto-resolved with ``skip`` so the workflow can finish end-to-end.
    """
    app = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test"}}
    state: dict = {
        "raw_task": raw_task,
        "run_id": "test",
        "submit_to_cluster": os.getenv("USE_CLUSTER", "false").lower() == "true",
    }

    print("**开始 LangGraph 测试**")
    print(f"- raw_task: {textwrap.shorten(raw_task, 120)}")

    def _emit_update(updates: dict) -> None:
        for node_name, up in updates.items():
            if node_name == "__interrupt__" or not isinstance(up, dict):
                continue
            state.update(up)
            label = NODE_LABELS.get(node_name, node_name)
            detail = ""
            for key in (
                "protein_summary",
                "ligand_summary",
                "docking_summary",
                "md_summary",
                "fallback_result",
                "route",
                "analysis_summary",
            ):
                if up.get(key):
                    detail = textwrap.shorten(str(up[key]), 120)
                    break
            if detail:
                print(f"- {label}: {detail}")
            else:
                print(f"- {label}")

    for event in app.stream(state, config, stream_mode="updates"):
        _emit_update(event)

    while True:
        snapshot = app.get_state(config)
        interrupts = list(snapshot.interrupts) if snapshot and snapshot.interrupts else []
        if not interrupts:
            break
        for interrupt in interrupts:
            print(f"[interrupt] {interrupt.value}")
        for event in app.stream(Command(resume="skip"), config, stream_mode="updates"):
            _emit_update(event)

    final = app.get_state(config)
    final_state = final.values if final else state
    print("**LangGraph 测试完成**")
    return final_state

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "请对蛋白质 PDB ID 3PTB 和配体 SMILES CCO 进行对接，使用 ff19SB 力场，不要 MD"
    )
    tid = os.getenv("AUTOMD_SESSION", "main")

    gen = run_automd(task, thread_id=tid)
    send_back = None
    try:
        while True:
            try:
                pu.debug(f"[cli send probe] about to send: {send_back!r}")
                msg = gen.send(send_back)
            except StopIteration:
                break

            if msg is None:
                send_back = None
                continue

            kind = msg.get("type")
            if kind == "section":
                print(msg.get("text", ""))
            elif kind == "step":
                detail = msg.get("detail", "")
                if detail:
                    print(f"- {msg.get('label', '')}: {detail}")
                else:
                    print(f"- {msg.get('label', '')}")
            elif kind == "interrupt":
                print(f"\n[需要输入] {msg.get('text', '')}")
                raw_input = input("> ")
                stripped_input = raw_input.strip()
                pu.debug(
                    f"[cli input probe] raw={raw_input!r} len={len(raw_input)} strip={stripped_input!r} len={len(stripped_input)}"
                )
                send_back = stripped_input or "skip"
            elif kind == "report":
                pu.report(msg.get("state", {}) or {})
                send_back = None
            else:
                send_back = None
    except KeyboardInterrupt:
        print("\n已中断。")
