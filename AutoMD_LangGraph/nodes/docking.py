from __future__ import annotations

import os
import sys

nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.types import Command, interrupt

from State import AutoMDState
from tools.dock import dock, get_docking_box_from_p2rank
from tools.postdock import analyze_interactions

from .common import work_root, ensure_dir, handle_tool_error, text, tool_data


def merge_inputs(state: AutoMDState) -> Command:
    protein_receptor = state.get("protein_receptor_pdbqt")
    ligand_pdbqt = state.get("ligand_pdbqt")

    if not protein_receptor or not ligand_pdbqt:
        user_choice = interrupt(
            "缺少蛋白或配体输入，无法进入对接。\n"
            "输入 'retry' 返回重试，或 'skip' 跳过对接路线。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="plan_route")
        return Command(
            update={
                "docking_is_success": False,
                "docking_summary": "用户跳过对接路线",
                "need_docking": False,
            },
            goto="plan_route",
        )

    docking_mode = state.get("docking_mode", "blind")
    next_node = "visual_docking" if docking_mode == "visual_box" else "pocket_detection"
    return Command(
        update={"docking_summary": f"已汇合输入: {protein_receptor} + {ligand_pdbqt}"},
        goto=next_node,
    )


def pocket_detection(state: AutoMDState) -> Command:
    protein_pdb = (
        state.get("protein_filtered_pdb")
        or state.get("protein_clean_pdb")
        or state.get("protein_raw_pdb")
    )
    if not protein_pdb:
        user_choice = interrupt("缺少蛋白结构，无法进行口袋识别。输入 'retry' 返回，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="pocket_detection")
        return Command(
            update={"need_docking": False, "docking_summary": "缺少蛋白结构"},
            goto="plan_route",
        )

    pocket_dir = ensure_dir(work_root(state) / "docking" / (state.get("project_id") or "default") / "pocket")
    r = get_docking_box_from_p2rank(
        protein_pdb=protein_pdb,
        output_dir=str(pocket_dir),
        use_getbox=True,
        extension=8.0,
        fallback_to_simple=True,
    )
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="pocket_detection",
            retry_goto="pocket_detection",
            skip_update={"need_docking": False, "docking_is_success": False},
        )

    data = tool_data(r)
    if not isinstance(data, dict):
        user_choice = interrupt(f"口袋检测返回异常数据: {text(r)}。输入 'retry' 重试，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="pocket_detection")
        return Command(
            update={"need_docking": False, "docking_summary": "口袋检测数据异常"},
            goto="plan_route",
        )

    return Command(
        update={"docking_box": data, "docking_summary": text(r)},
        goto="docking_setup",
    )


def docking_setup(state: AutoMDState) -> Command:
    if not state.get("protein_receptor_pdbqt") or not state.get("ligand_pdbqt"):
        user_choice = interrupt("对接输入不完整。输入 'retry' 返回，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="docking_setup")
        return Command(
            update={"need_docking": False, "docking_summary": "输入不完整"},
            goto="plan_route",
        )

    box = state.get("docking_box") or {}
    if not box:
        user_choice = interrupt("缺少对接盒信息。输入 'retry' 返回，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="docking_setup")
        return Command(
            update={"need_docking": False, "docking_summary": "缺少对接盒信息"},
            goto="plan_route",
        )

    return Command(update={"docking_summary": f"对接准备完成: {box}"}, goto="docking_run")


def docking_run(state: AutoMDState) -> Command:
    box = state.get("docking_box") or {}
    protein_file = state.get("protein_receptor_pdbqt") or ""
    ligand_file = state.get("ligand_pdbqt") or ""
    out_dir = ensure_dir(work_root(state) / "docking" / (state.get("project_id") or "default") / "vina")
    exhaustiveness = state.get("docking_exhaustiveness", 8)
    num_modes = state.get("docking_num_modes", 9)
    energy_range = state.get("docking_energy_range", 3.0)

    if not protein_file or not ligand_file:
        user_choice = interrupt("对接输入文件缺失。输入 'retry' 返回，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="docking_run")
        return Command(
            update={"need_docking": False, "docking_summary": "输入文件缺失"},
            goto="plan_route",
        )

    r = dock(
        protein_file=protein_file,
        ligand_file=ligand_file,
        output_dir=str(out_dir),
        center_x=box.get("center_x"),
        center_y=box.get("center_y"),
        center_z=box.get("center_z"),
        size_x=box.get("size_x", 15.0),
        size_y=box.get("size_y", 15.0),
        size_z=box.get("size_z", 15.0),
        use_getbox=False,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        energy_range=energy_range,
    )
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="docking_run",
            retry_goto="docking_run",
            skip_update={"need_docking": False, "docking_is_success": False},
        )

    summary = r.data.get("summary", "") if isinstance(r.data, dict) else ""
    docked_pdbqt = r.data.get("output_pdbqt", "") if isinstance(r.data, dict) else ""
    docked_pdb = r.data.get("output_pdb", "") if isinstance(r.data, dict) else ""
    return Command(
        update={
            "docking_result": summary or text(r),
            "docked_ligand_pdbqt": docked_pdbqt,
            "docked_ligand_pdb": docked_pdb or "",
            "docking_is_success": True,
            "docking_summary": text(r),
        },
        goto="docking_evaluation",
    )


def docking_evaluation(state: AutoMDState) -> Command:
    docking_result = state.get("docking_result")
    if not docking_result:
        user_choice = interrupt("对接失败或未生成结果。输入 'retry' 返回，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="docking_run")
        return Command(
            update={"need_docking": False, "docking_summary": "未生成结果"},
            goto="plan_route",
        )

    protein_pdb = (
        state.get("protein_filtered_pdb")
        or state.get("protein_clean_pdb")
        or state.get("protein_raw_pdb")
    )
    ligand_file = (
        state.get("docked_ligand_pdb")
        or state.get("docked_ligand_pdbqt")
        or state.get("ligand_mol2")
        or state.get("ligand_input_file")
        or state.get("ligand_pdbqt")
    )
    eval_dir = ensure_dir(work_root(state) / "docking" / (state.get("project_id") or "default") / "evaluation")

    r = analyze_interactions(
        protein_pdb=protein_pdb or "",
        ligand_sdf=ligand_file or "",
        output_dir=str(eval_dir),
        use_plip=True,
    )

    data = tool_data(r)
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="docking_evaluation",
            retry_goto="docking_run",
            skip_update={"need_docking": False, "docking_is_success": False},
        )

    interactions = data if isinstance(data, dict) else {"summary": text(r)}

    if state.get("need_analysis") and not state.get("need_md"):
        return Command(
            update={
                "docking_interactions": interactions,
                "analysis_result": text(r),
                "analysis_is_success": True,
                "analysis_summary": text(r),
                "docking_summary": text(r),
            },
            goto="report",
        )

    if not state.get("need_md"):
        return Command(
            update={"docking_interactions": interactions, "docking_summary": text(r)},
            goto="report",
        )

    return Command(
        update={"docking_interactions": interactions, "docking_summary": text(r)},
        goto="complex_prep",
    )
