from __future__ import annotations

import os
import sys
from pathlib import Path

nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = os.path.abspath(os.path.join(nodes_dir, "..", ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.types import Command, interrupt

from State import AutoMDState
from tools.protein import (
    fetch_pdb,
    filter_standard_protein_residues,
    run_tleap_with_recovery,
    run_pdb4amber,
    run_prepare_receptor4_py,
)

from .common import (
    work_root,
    ensure_dir,
    handle_tool_error,
)


def protein_fetch(state: AutoMDState) -> Command:
    pdb_id = state.get("protein_pdb_id")
    if not pdb_id:
        user_input = interrupt("原始任务中未成功捕获到PDB id，请手动输入蛋白质 PDB ID：")
        return Command(
            update={"protein_pdb_id": user_input},
            goto="protein_fetch",
        )

    out_dir = ensure_dir(work_root(state) / "protein" / pdb_id)
    r = fetch_pdb(pdb_id, output_dir=str(out_dir / "fetch"))
    if not r.ok:
        return handle_tool_error(
            result=r,
            calling_node="protein_fetch",
            state=state,
            retry_goto="protein_fetch",
            skip_update={"need_protein": False, "protein_is_success": False},
        )

    raw_pdb = r.data
    return Command(
        update={
            "protein_raw_pdb": raw_pdb,
            "protein_result": raw_pdb,
            "protein_is_success": True,
            "protein_summary": f"已下载 PDB: {raw_pdb}",
            "calling_node": "protein_fetch",
        },
        goto="protein_clean",
    )


def protein_clean(state: AutoMDState) -> Command:
    pdb_id = state.get("protein_pdb_id", "protein")
    raw_pdb = state.get("protein_raw_pdb") or state.get("protein_result")
    if not raw_pdb:
        user_choice = interrupt(
            "蛋白清洗失败：未找到原始 PDB 文件。\n"
            f"当前 PDB ID: {state.get('protein_pdb_id', '未知')}\n"
            "请选择: 输入 'retry' 重新下载，或输入 'skip' 跳过蛋白准备"
        )
        if user_choice.strip().lower() == "retry":
            return Command(
                update={"protein_result": "", "protein_summary": "用户要求重新下载 PDB"},
                goto="protein_fetch",
            )
        return Command(
            update={
                "protein_is_success": False,
                "protein_summary": "用户跳过蛋白准备",
                "need_protein": False,
            },
            goto="plan_route",
        )

    prep_dir = ensure_dir(work_root(state) / "protein" / pdb_id / "clean")
    clean_pdb = str(prep_dir / f"{pdb_id}_clean.pdb")
    protein_only_pdb = str(prep_dir / f"{pdb_id}_protein_only.pdb")

    r = run_pdb4amber(raw_pdb, clean_pdb, keep_hetatm=False)
    if not r.ok:
        return handle_tool_error(
            result=r,
            calling_node="protein_clean",
            state=state,
            retry_goto="protein_clean",
            skip_update={"need_protein": False, "protein_is_success": False},
        )

    r = filter_standard_protein_residues(clean_pdb, protein_only_pdb)
    if not r.ok:
        return handle_tool_error(
            result=r,
            calling_node="protein_clean",
            state=state,
            retry_goto="protein_clean",
            skip_update={"need_protein": False, "protein_is_success": False},
        )

    return Command(
        update={
            "protein_clean_pdb": clean_pdb,
            "protein_filtered_pdb": protein_only_pdb,
            "protein_result": protein_only_pdb,
            "protein_is_success": True,
            "protein_summary": f"已完成蛋白清洗与过滤: {protein_only_pdb}",
            "calling_node": "protein_clean",
        },
        goto="tleap_prep",
    )


def tleap_prep(state: AutoMDState) -> Command:
    protein_only_pdb = state.get("protein_filtered_pdb") or state.get("protein_result")
    pdb_id = state.get("protein_pdb_id") or "protein"
    if not protein_only_pdb:
        user_choice = interrupt(
            f"蛋白质受体准备失败：未在{protein_only_pdb}下找到纯蛋白 PDB 文件。\n"
            f"请选择: 输入 'retry' 重试，或 'skip' 跳过蛋白受体准备步骤"
        )
        if user_choice.strip().lower() == "retry":
            return Command(
                update={"protein_summary": "用户要求重试 tleap 准备"},
                goto="tleap_prep",
            )
        return Command(
            update={
                "protein_is_success": False,
                "protein_summary": "用户跳过 tleap 准备，蛋白路线终止",
                "need_protein": False,
            },
            goto="plan_route",
        )

    md_dir = ensure_dir(work_root(state) / "protein" / pdb_id / "topology")
    force_field = state.get("md_force_field", "ff14SB")
    water_model = state.get("md_water_model", "tip3p")
    r = run_tleap_with_recovery(
        clean_pdb=protein_only_pdb,
        output_dir=md_dir,
        box_padding=10.0,
        neutralize=True,
        force_field=force_field,
        water_model=water_model,
    )
    if not r.ok:
        return handle_tool_error(
            result=r,
            calling_node="tleap_prep",
            state=state,
            retry_goto="tleap_prep",
            skip_update={"need_protein": False, "protein_is_success": False},
        )

    return Command(
        update={
            "protein_prmtop": str(r.data["prmtop"]),
            "protein_inpcrd": str(r.data["inpcrd"]),
            "protein_summary": f"tleap 已生成蛋白 MD 输入: {r.data['prmtop']}, {r.data['inpcrd']}",
        },
        goto="protein_receptor_prep",
    )


def protein_receptor_prep(state: AutoMDState) -> Command:
    protein_only_pdb = state.get("protein_filtered_pdb") or state.get("protein_result")
    if not protein_only_pdb:
        user_choice = interrupt(
            f"蛋白质PDBQT准备失败：未在{protein_only_pdb}下找到纯蛋白 PDB 文件。\n"
            f"请选择: 输入 'retry' 重试，或 'skip' 跳过蛋白PDBQT准备步骤"
        )
        if user_choice.strip().lower() == "retry":
            return Command(
                update={"protein_summary": "用户要求重试 protein_receptor_prep 准备"},
                goto="protein_receptor_prep",
            )
        return Command(
            update={
                "protein_is_success": False,
                "protein_summary": "用户跳过蛋白PDBQT准备，蛋白路线终止",
                "need_protein": False,
            },
            goto="plan_route",
        )

    out_dir = ensure_dir(work_root(state) / "protein" / (state.get("protein_pdb_id") or "protein") / "receptor")
    receptor_pdbqt = str(out_dir / f"{Path(protein_only_pdb).stem}.pdbqt")
    r = run_prepare_receptor4_py(input_pdb=protein_only_pdb, output_pdbqt=receptor_pdbqt)
    if not r.ok:
        return handle_tool_error(
            result=r,
            calling_node="protein_receptor_prep",
            state=state,
            retry_goto="protein_receptor_prep",
            skip_update={"need_protein": False, "protein_is_success": False},
        )

    return Command(
        update={
            "protein_receptor_pdbqt": receptor_pdbqt,
            "protein_result": receptor_pdbqt,
            "protein_is_success": True,
            "protein_summary": f"已生成受体 PDBQT: {receptor_pdbqt}",
        },
        goto="protein_qa",
    )


def protein_qa(state: AutoMDState) -> Command:
    receptor = state.get("protein_receptor_pdbqt")
    if not receptor or not Path(receptor).exists():
        user_choice = interrupt(
            f"蛋白质 QA 失败：未找到受体 PDBQT 文件。\n"
            f"当前受体文件路径: {receptor}\n"
            "请选择: 输入 'retry' 重新准备受体，或输入 'skip' 跳过蛋白质 QA 步骤"
        )
        if user_choice.strip().lower() == "retry":
            return Command(
                update={
                    "protein_summary": "用户要求重试 protein_receptor_prep",
                    "calling_node": "protein_qa",
                },
                goto="protein_receptor_prep",
            )
        return Command(
            update={
                "protein_is_success": False,
                "protein_summary": "用户跳过蛋白质 QA，蛋白路线终止",
                "need_protein": False,
            },
            goto="plan_route",
        )

    state_update: dict = {}
    protein_prmtop = state.get("protein_prmtop")
    protein_inpcrd = state.get("protein_inpcrd")
    if not protein_prmtop or not protein_inpcrd:
        user_choice = interrupt(
            f"蛋白质 QA 失败：未找到 protein_prmtop 和 protein_inpcrd 文件。\n"
            f"当前文件路径: {protein_prmtop}，{protein_inpcrd}\n"
            "请选择: 输入 'retry' 重新准备文件（重新运行tleap_prep节点），或输入 'skip' 跳过蛋白质 QA 步骤"
        )
        if user_choice.strip().lower() == "retry":
            return Command(
                update={
                    "protein_summary": "用户要求重试 tleap_prep",
                    "calling_node": "protein_qa",
                },
                goto="tleap_prep",
            )
        else:
            return Command(
                update={
                    "protein_is_success": False,
                    "protein_summary": "用户跳过蛋白质 QA，蛋白路线终止",
                    "need_protein": False,
                },
                goto="plan_route",
            )

    need_ligand = bool(state.get("need_ligand"))
    need_docking = bool(state.get("need_docking"))
    need_md = bool(state.get("need_md"))
    need_analysis = bool(state.get("need_analysis"))

    if not (need_ligand or need_docking or need_md or need_analysis):
        return Command(
            update={**state_update, "protein_summary": state_update.get("protein_summary") or f"蛋白 QA 通过: {receptor}"},
            goto="report",
        )

    if need_ligand and not state.get("ligand_is_success"):
        state_update["protein_summary"] = state_update.get("protein_summary") or "蛋白 QA 通过，进入配体路线。"
        return Command(update=state_update, goto="ligand_resolve")

    if need_docking or need_md or need_analysis:
        if state.get("ligand_is_success"):
            goto_next = "merge_inputs" if need_docking else "plan_route"
            return Command(
                update={**state_update, "protein_summary": state_update.get("protein_summary") or f"蛋白 QA 通过: {receptor}"},
                goto=goto_next,
            )
        return Command(
            update={**state_update, "protein_summary": state_update.get("protein_summary") or f"蛋白 QA 通过: {receptor}"},
            goto="ligand_resolve",
        )

    return Command(
        update={**state_update, "protein_summary": state_update.get("protein_summary") or f"蛋白 QA 通过: {receptor}"},
        goto="report",
    )
