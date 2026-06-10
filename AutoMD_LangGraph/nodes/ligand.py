"""
Ligand nodes: resolve, to_3d, antechamber, parmchk, tleap, pdbqt, qa.
Each node calls one tool from tools/ligand.py and uses handle_tool_error for failures.
"""

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
from tools.ligand import (
    smiles_to_pdb,
    run_antechamber,
    run_parmchk2,
    _deduplicate_mol2_bonds,
    run_tleap,
    run_prepare_ligand4_py,
)

from .common import (
    work_root,
    ensure_dir,
    extract_file_path,
    extract_smiles,
    handle_tool_error,
)


def ligand_resolve(state: AutoMDState) -> Command:
    ligand_smiles = state.get("ligand_smiles") or extract_smiles(
        state.get("normalized_task") or state.get("raw_task", "")
    )
    ligand_input_file = state.get("ligand_input_file") or extract_file_path(
        state.get("normalized_task") or state.get("raw_task", "")
    )

    if not ligand_smiles and not ligand_input_file:
        user_choice = interrupt(
            "缺少配体输入（SMILES 或文件）。\n"
            "请提供配体 SMILES 字符串或文件路径，输入 'skip' 跳过配体路线。"
        )
        if user_choice.strip().lower() == "skip":
            return Command(
                update={"need_ligand": False, "ligand_is_success": False},
                goto="plan_route",
            )
        return Command(
            update={"ligand_smiles": user_choice.strip()},
            goto="ligand_resolve",
        )

    return Command(
        update={"ligand_smiles": ligand_smiles or "", "ligand_input_file": ligand_input_file or ""},
        goto="ligand_to_3d",
    )


def ligand_to_3d(state: AutoMDState) -> Command:
    smiles = state.get("ligand_smiles")
    out_dir = ensure_dir(work_root(state) / "ligand" / (state.get("project_id") or "default"))

    if smiles:
        r = smiles_to_pdb(
            smiles,
            str(ensure_dir(out_dir / "conformer") / "input_from_smiles.pdb"),
            add_hydrogens=True,
            protein_pdb_path=state.get("protein_raw_pdb") or "",
            pdb_id=state.get("protein_pdb_id") or "",
        )
        if not r.ok:
            return handle_tool_error(
                result=r,
                state=state,
                calling_node="ligand_to_3d",
                retry_goto="ligand_to_3d",
                skip_update={"need_ligand": False, "ligand_is_success": False},
            )
        return Command(
            update={
                "ligand_input_file": r.data,
                "ligand_summary": f"已从 SMILES 生成 PDB: {r.data}",
            },
            goto="ligand_antechamber",
        )

    inp = state.get("ligand_input_file")
    if not inp:
        return Command(
            update={"need_ligand": False, "ligand_summary": "缺少 SMILES 或输入文件"},
            goto="plan_route",
        )
    return Command(
        update={"ligand_summary": "使用现有输入文件", "ligand_input_file": inp},
        goto="ligand_antechamber",
    )


def ligand_antechamber(state: AutoMDState) -> Command:
    inp = state.get("ligand_input_file")
    if not inp:
        user_choice = interrupt(
            "缺少配体输入文件。请提供文件路径，或输入 'skip' 跳过。"
        )
        if user_choice.strip().lower() == "skip":
            return Command(
                update={"need_ligand": False, "ligand_summary": "用户跳过"},
                goto="plan_route",
            )
        return Command(update={"ligand_input_file": user_choice.strip()}, goto="ligand_antechamber")

    out_dir = ensure_dir(work_root(state) / "ligand" / (state.get("project_id") or "default") / "amber")
    mol2_out = str(out_dir / "ligand.mol2")

    net_charge = 0
    smiles = state.get("ligand_smiles", "")
    if smiles:
        try:
            from rdkit import Chem
            mol = Chem.MolFromSmiles(smiles)
            if mol is not None:
                net_charge = Chem.GetFormalCharge(mol)
        except Exception:
            pass

    ligand_ff = state.get("md_ligand_ff", "gaff2")

    r = run_antechamber(inp, mol2_out, input_format="pdb",
                        charge_method="bcc", net_charge=net_charge,
                        force_field=ligand_ff)
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="ligand_antechamber",
            retry_goto="ligand_antechamber",
            skip_update={"need_ligand": False, "ligand_is_success": False},
        )

    return Command(
        update={"ligand_mol2": mol2_out, "ligand_summary": f"已生成 mol2: {mol2_out}"},
        goto="ligand_parmchk",
    )


def ligand_parmchk(state: AutoMDState) -> Command:
    mol2 = state.get("ligand_mol2")
    if not mol2:
        return Command(
            update={"need_ligand": False, "ligand_summary": "缺少 mol2 文件"},
            goto="plan_route",
        )

    out_dir = ensure_dir(work_root(state) / "ligand" / (state.get("project_id") or "default") / "amber")
    frcmod = str(out_dir / "ligand.frcmod")

    ligand_ff = state.get("md_ligand_ff", "gaff2")

    r = run_parmchk2(mol2, frcmod, force_field=ligand_ff)
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="ligand_parmchk",
            retry_goto="ligand_parmchk",
            skip_update={"need_ligand": False, "ligand_is_success": False},
        )

    deduped = str(out_dir / "ligand_dedup.mol2")
    ok = _deduplicate_mol2_bonds(mol2, deduped); deduped = deduped if ok and os.path.exists(deduped) else mol2

    return Command(
        update={
            "ligand_frcmod": frcmod,
            "ligand_mol2": deduped,
            "ligand_summary": f"已生成 frcmod: {frcmod}",
        },
        goto="ligand_tleap",
    )


def ligand_tleap(state: AutoMDState) -> Command:
    mol2 = state.get("ligand_mol2")
    frcmod = state.get("ligand_frcmod")
    if not mol2 or not frcmod:
        user_choice = interrupt(
            f"缺少 {'mol2' if not mol2 else 'frcmod'} 文件，无法生成 MD 文件。\n"
            f"输入 'retry' 重新准备，或 'skip' 跳过。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="ligand_parmchk")
        return Command(
            update={"need_ligand": False, "ligand_is_success": False},
            goto="plan_route",
        )

    out_dir = ensure_dir(work_root(state) / "ligand" / (state.get("project_id") or "default") / "topology")
    prmtop = str(Path(out_dir) / "ligand.prmtop")
    inpcrd = str(Path(out_dir) / "ligand.inpcrd")
    r = run_tleap(
        mol2, frcmod, prmtop, inpcrd,
        work_dir=str(out_dir),
    )
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="ligand_tleap",
            retry_goto="ligand_tleap",
            skip_update={"need_ligand": False, "ligand_is_success": False},
        )

    return Command(
        update={
            "ligand_prmtop": prmtop,
            "ligand_inpcrd": inpcrd,
            "ligand_summary": "已生成 ligand prmtop/inpcrd",
        },
        goto="ligand_pdbqt",
    )


def ligand_pdbqt(state: AutoMDState) -> Command:
    mol2 = state.get("ligand_mol2")
    inp = state.get("ligand_input_file") or mol2
    out_dir = ensure_dir(work_root(state) / "ligand" / (state.get("project_id") or "default") / "pdbqt")
    pdbqt_path = str(Path(out_dir) / "ligand.pdbqt")

    r = run_prepare_ligand4_py(inp, pdbqt_path)
    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="ligand_pdbqt",
            retry_goto="ligand_pdbqt",
            skip_update={"need_ligand": False, "ligand_is_success": False},
        )

    return Command(
        update={
            "ligand_pdbqt": pdbqt_path,
            "ligand_result": pdbqt_path,
            "ligand_is_success": True,
            "ligand_summary": f"已生成 PDBQT: {pdbqt_path}",
        },
        goto="ligand_qa",
    )


def ligand_qa(state: AutoMDState) -> Command:
    ligand_ready = state.get("ligand_pdbqt") or state.get("ligand_prmtop") or state.get("ligand_mol2")
    if not ligand_ready:
        user_choice = interrupt("配体 QA 未通过，未找到可用配体文件。输入 'retry' 重试，或 'skip' 跳过。")
        if user_choice.strip().lower() == "retry":
            return Command(goto="ligand_resolve")
        return Command(
            update={"need_ligand": False, "ligand_is_success": False},
            goto="plan_route",
        )

    need_protein = bool(state.get("need_protein"))
    need_docking = bool(state.get("need_docking"))
    need_md = bool(state.get("need_md"))
    need_analysis = bool(state.get("need_analysis"))

    if not (need_protein or need_docking or need_md or need_analysis):
        return Command(
            update={"ligand_summary": f"配体 QA 通过: {ligand_ready}"},
            goto="report",
        )

    if need_protein and not state.get("protein_is_success"):
        return Command(
            update={"ligand_summary": "配体 QA 通过，回到蛋白路线。"},
            goto="protein_fetch",
        )

    goto_next = "merge_inputs" if state.get("need_docking") else "plan_route"
    return Command(
        update={"ligand_summary": f"配体 QA 通过: {ligand_ready}"},
        goto=goto_next,
    )
