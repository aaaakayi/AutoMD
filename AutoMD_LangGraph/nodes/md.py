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
from tools.md_simulation import run_md_simulation
from tools.postdock import predict_admet
from tools.trajectory_analysis import run_full_analysis

from .common import work_root, ensure_dir, handle_tool_error, text, tool_data


def md_preflight(state: AutoMDState) -> Command:
    md_prmtop = state.get("md_prmtop") or ""
    md_inpcrd = state.get("md_inpcrd") or ""

    # Complex mode: use pre-built complex from complex_prep node
    if md_prmtop and md_inpcrd:
        if state.get("submit_to_cluster"):
            return Command(
                update={"md_summary": "MD 预检通过（复合物模式），提交到集群。"},
                goto="submit_to_cluster",
            )
        return Command(
            update={"md_summary": "MD 预检通过（复合物模式），开始本地模拟。请等待运行结束..."},
            goto="md_run",
        )

    # Route gap fix: have docked ligand but complex not yet prepared
    docked_ligand = state.get("docked_ligand_pdb") or ""
    if docked_ligand and not md_prmtop:
        return Command(
            update={"md_summary": "检测到对接结果，先准备复合物"},
            goto="complex_prep",
        )

    # Fallback: separate protein + ligand files
    protein_prmtop = state.get("protein_prmtop") or ""
    protein_inpcrd = state.get("protein_inpcrd") or ""
    ligand_prmtop = state.get("ligand_prmtop") or ""
    ligand_inpcrd = state.get("ligand_inpcrd") or ""

    if not all([protein_prmtop, protein_inpcrd, ligand_prmtop, ligand_inpcrd]):
        user_choice = interrupt(
            "MD 预检失败：缺少 protein/ligand 的 prmtop/inpcrd。\n"
            "输入 'retry' 返回重试，或 'skip' 跳过 MD 路线。"
        )
        if user_choice.strip().lower() == "retry":
            return Command(goto="complex_prep")
        return Command(
            update={"need_md": False, "md_is_success": False, "md_summary": "用户跳过 MD"},
            goto="plan_route",
        )

    if state.get("submit_to_cluster"):
        return Command(
            update={"md_summary": "MD 预检通过，提交到集群。"},
            goto="submit_to_cluster",
        )

    return Command(update={"md_summary": "MD 预检通过，开始本地模拟。"}, goto="md_run")


def md_run(state: AutoMDState) -> Command:
    out_dir = ensure_dir(work_root(state) / "md" / (state.get("project_id") or "default"))
    md_prmtop = state.get("md_prmtop") or ""
    md_inpcrd = state.get("md_inpcrd") or ""
    duration_ns = state.get("md_duration_ns", 10.0)
    temperature_k = state.get("md_temperature_k", 300.0)
    pressure_atm = state.get("md_pressure_atm", 1.0)
    timestep_fs = state.get("md_timestep_fs", 2.0)
    save_interval_ps = state.get("md_save_interval_ps", 100.0)
    nvt_equil_ps = state.get("md_nvt_equil_ps", 100.0)
    npt_equil_ps = state.get("md_npt_equil_ps", 100.0)

    if md_prmtop and md_inpcrd:
        r = run_md_simulation(
            complex_prmtop=md_prmtop,
            complex_inpcrd=md_inpcrd,
            output_dir=str(out_dir),
            duration_ns=duration_ns,
            temperature_k=temperature_k,
            pressure_atm=pressure_atm,
            timestep_fs=timestep_fs,
            save_interval_ps=save_interval_ps,
            nvt_equil_ps=nvt_equil_ps,
            npt_equil_ps=npt_equil_ps,
        )
    else:
        r = run_md_simulation(
            protein_prmtop=state.get("protein_prmtop") or "",
            protein_inpcrd=state.get("protein_inpcrd") or "",
            ligand_prmtop=state.get("ligand_prmtop") or "",
            ligand_inpcrd=state.get("ligand_inpcrd") or "",
            output_dir=str(out_dir),
            duration_ns=duration_ns,
            temperature_k=temperature_k,
            pressure_atm=pressure_atm,
            timestep_fs=timestep_fs,
            save_interval_ps=save_interval_ps,
            nvt_equil_ps=nvt_equil_ps,
            npt_equil_ps=npt_equil_ps,
        )

    if not r.ok:
        return handle_tool_error(
            result=r,
            state=state,
            calling_node="md_run",
            retry_goto="md_run",
            skip_update={"need_md": False, "md_is_success": False},
        )

    data = tool_data(r)
    md_trajectory = ""
    if isinstance(data, dict):
        md_trajectory = str(
            data.get("trajectory_dcd")
            or data.get("trajectory_path")
            or data.get("trajectory")
            or data.get("output_dir")
            or ""
        )

    return Command(
        update={
            "md_result": md_trajectory or text(r),
            "md_trajectory": md_trajectory,
            "md_is_success": True,
            "md_summary": text(r),
        },
        goto="trajectory_analysis",
    )


def trajectory_analysis(state: AutoMDState) -> Command:
    pid = state.get("project_id") or "default"
    summary_lines = []

    # ── cpptraj trajectory analysis ──
    md_prmtop = state.get("md_prmtop") or ""
    md_traj = state.get("md_trajectory") or ""

    analysis_ok = False

    if md_prmtop and md_traj and os.path.exists(md_prmtop) and os.path.exists(md_traj):
        analysis_dir = ensure_dir(work_root(state) / "analysis" / pid / "cpptraj")
        r = run_full_analysis(
            prmtop=md_prmtop,
            trajectory=md_traj,
            output_dir=str(analysis_dir),
            ligand_resname="LIG",
        )
        if r.ok or r.degradation:
            data = r.data if isinstance(r.data, dict) else {}
            # Build concise summary
            rmsd = data.get("rmsd_ca", {})
            rmsf = data.get("rmsf", {})
            hb = data.get("hbonds", {})
            pca = data.get("pca", {})
            ss = data.get("dssp", {}).get("secondary_structure", {})

            parts = []
            if rmsd:
                parts.append(f"RMSD(Cα): {rmsd.get('mean_angstrom', 'N/A')}±{rmsd.get('std_angstrom', 'N/A')} Å")
            if hb:
                parts.append(f"H-bonds: {hb.get('mean_per_frame', 'N/A')}/frame")
            if pca:
                ev = pca.get("explained_variance", [])
                if len(ev) >= 2:
                    parts.append(f"PCA: PC1={ev[0]:.2%} PC2={ev[1]:.2%}")
            if ss:
                top_ss = sorted(ss.items(), key=lambda x: x[1], reverse=True)
                parts.append(f"二级结构: {', '.join(f'{k}:{v:.0%}' for k,v in top_ss[:3])}")

            summary_lines.append("── cpptraj 轨迹分析 ──")
            summary_lines.append("  " + "  |  ".join(parts))

            if r.degradation:
                summary_lines.append(f"  [降级] {'; '.join(r.degradation)}")

            # Check equilibration convergence from production log
            prod_log = work_root(state) / "md" / pid / "production.log"
            if prod_log.exists():
                summary_lines.append(f"  平衡检查: 参见 {prod_log}")
            analysis_ok = True
        else:
            summary_lines.append(f"  cpptraj 分析失败: {'; '.join(r.errors)}")
    else:
        summary_lines.append(f"  [跳过] 缺少 MD 拓扑/轨迹文件，无法进行轨迹分析")
        if state.get("md_summary"):
            summary_lines.append(f"  MD摘要: {state.get('md_summary')}")

    # ── ADMET prediction (unchanged) ──
    if state.get("ligand_smiles"):
        admet_dir = ensure_dir(work_root(state) / "analysis" / pid / "admet")
        r = predict_admet(state.get("ligand_smiles") or "", str(admet_dir))
        if r.ok:
            summary_lines.append(f"ADMET: {text(r)}")

    return Command(
        update={
            "analysis_result": "\n".join(summary_lines),
            "analysis_is_success": analysis_ok,
            "analysis_summary": "\n".join(summary_lines),
        },
        goto="md_plot",
    )
