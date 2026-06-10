"""
Complex preparation node: combine protein + docked ligand into solvated complex
via tLEaP, producing complex.prmtop / complex.inpcrd for MD simulation.

Runs AFTER docking so the ligand coordinates reflect the binding-site pose,
not the vacuum conformation from ligand_tleap.
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
from tools.ligand import run_complex_tleap

from .common import work_root, ensure_dir, handle_tool_error


def complex_prep(state: AutoMDState) -> Command:
    """Build solvated, neutralized protein-ligand complex using docked ligand pose.

    Inputs from state:
        protein_filtered_pdb (or protein_clean_pdb) — protein structure
        docked_ligand_pdb — docked ligand coordinates in binding site
        ligand_frcmod — GAFF force field parameters for the ligand
        ligand_mol2 — fallback mol2 if loadpdb fails in tLEaP

    Outputs to state:
        md_prmtop — path to complex.prmtop
        md_inpcrd — path to complex.inpcrd
        complex_is_success — True
    """
    protein_pdb = state.get("protein_filtered_pdb") or state.get("protein_clean_pdb") or ""
    docked_ligand_pdb = state.get("docked_ligand_pdb") or ""
    frcmod = state.get("ligand_frcmod") or ""
    fallback_mol2 = state.get("ligand_mol2") or ""
    force_field = state.get("md_force_field", "ff14SB")
    water_model = state.get("md_water_model", "tip3p")
    ligand_ff = state.get("md_ligand_ff", "gaff2")

    if not protein_pdb:
        user_choice = interrupt(
            "复合物准备失败：缺少蛋白质结构。\n"
            "输入 'retry' 重试，或 'skip' 跳过 MD 路线。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="plan_route")
        return Command(
            update={"need_md": False, "md_is_success": False, "md_summary": "用户跳过复合物准备"},
            goto="plan_route",
        )

    if not docked_ligand_pdb:
        user_choice = interrupt(
            "复合物准备失败：缺少对接后配体构象。\n"
            "请确保对接步骤已成功完成。\n"
            "输入 'retry' 重试，或 'skip' 跳过 MD 路线。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="plan_route")
        return Command(
            update={"need_md": False, "md_is_success": False, "md_summary": "用户跳过复合物准备"},
            goto="plan_route",
        )

    out_dir = ensure_dir(work_root(state) / "complex" / (state.get("project_id") or "default") / "tleap")
    ligand_smiles = state.get("ligand_smiles") or ""

    # 🔧 双保险: run_complex_tleap 内部已经有 try/except 兜底, 这里再加一层
    # 防止任何漏网异常冒到 langgraph stream 卡死主线程
    try:
        r = run_complex_tleap(
            protein_pdb=protein_pdb,
            docked_ligand_pdb=docked_ligand_pdb,
            ligand_frcmod=frcmod,
            output_dir=str(out_dir),
            fallback_mol2=fallback_mol2,
            ligand_smiles=ligand_smiles,
            force_field=force_field,
            water_model=water_model,
            ligand_ff=ligand_ff,
        )
    except Exception as e:
        import traceback as _tb
        from tools.shared import failed
        r = failed(
            errors=[f"complex_prep 调用 run_complex_tleap 时内部异常 ({type(e).__name__}): {e}\n{_tb.format_exc()}"],
            degradation=["internal_exception_in_complex_prep"],
        )

    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="complex_prep",
            retry_goto="plan_route",
            skip_update={"need_md": False, "md_is_success": False},
        )

    return Command(
        update={
            "md_prmtop": r.data["prmtop"],
            "md_inpcrd": r.data["inpcrd"],
            "complex_is_success": True,
            "md_summary": f"复合物已准备: {r.data['prmtop']}, {r.data['inpcrd']}（配体为对接后构象）",
        },
        goto="md_preflight",
    )
