"""
MD Plot node: read standard MD outputs (system.pdb + trajectory.dcd + analysis.json
+ production.log) and generate RMSD / energy / temperature / RMSF plots.
Outputs to md/{project_id}/plots/.
"""

from __future__ import annotations

import json
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

from langgraph.types import Command

from State import AutoMDState
from tools.shared import failed

from .common import work_root, ensure_dir, handle_tool_error


def _safe_import():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    return plt, np


def _read_analysis_json(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return None


def _read_production_log(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            lines = f.readlines()
        headers, data = None, {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", "\t").replace('"', "").split("\t")
            if headers is None:
                if parts[0].startswith("#") or parts[0].startswith('"#'):
                    headers = [h.strip('#" ') for h in parts]
                    for h in headers:
                        data[h] = []
                    continue
                headers = ["Step", "Time", "PotentialEnergy", "KineticEnergy",
                           "TotalEnergy", "Temperature", "Volume", "Density"]
                for h in headers:
                    data[h] = []
            if headers:
                try:
                    vals = [float(v) for v in parts]
                    for i, h in enumerate(headers):
                        if i < len(vals):
                            data[h].append(vals[i])
                except ValueError:
                    continue
        return data if headers and len(data.get(headers[0], [])) > 0 else None
    except Exception:
        return None


def _plot_rmsd(plt, np, analysis, out_path):
    rmsd_ca = analysis.get("rmsd_ca", {})
    if not rmsd_ca:
        return False
    fig, ax = plt.subplots(figsize=(10, 4))
    mean_val = rmsd_ca.get("mean_nm", 0)
    std_val = rmsd_ca.get("std_nm", 0)
    final_val = rmsd_ca.get("final_nm", 0)
    ax.bar(["Mean", "Final"], [mean_val, final_val], yerr=[std_val, 0],
           color=["steelblue", "darkorange"])
    ax.set_ylabel("RMSD (nm)")
    ax.set_title("Protein C-alpha RMSD")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


def _plot_rmsd_from_traj(plt, np, top_path, traj_path, out_path):
    try:
        import mdtraj as md
    except ImportError:
        return False
    try:
        traj = md.load(traj_path, top=top_path)
        ca = traj.topology.select("protein and name CA")
        if len(ca) == 0:
            ca = traj.topology.select("name CA")
        if len(ca) == 0:
            return False
        rmsd = md.rmsd(traj, traj, atom_indices=ca) * 10
        time_ns = traj.time / 1000.0
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(time_ns, rmsd, color="steelblue", linewidth=0.8)
        ax.set_xlabel("Time (ns)")
        ax.set_ylabel("RMSD (nm)")
        ax.set_title("Protein C-alpha RMSD")
        fig.tight_layout()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        return True
    except Exception:
        return False


def _plot_energy(plt, np, log_data, out_path):
    pe_key = next((k for k in log_data if "potential" in k.lower()), None)
    if pe_key is None:
        return False
    time_key = "Time" if "Time" in log_data else list(log_data.keys())[0]
    time = np.array(log_data.get(time_key, range(len(log_data[pe_key]))))
    pe = np.array(log_data[pe_key])
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(time, pe, color="darkorange", linewidth=0.8)
    ax.set_xlabel("Time (ps)" if "ps" in time_key.lower() else "Step")
    ax.set_ylabel("Potential Energy (kJ/mol)")
    ax.set_title("Potential Energy")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


def _plot_temperature(plt, np, log_data, out_path):
    temp_key = next((k for k in log_data if "temp" in k.lower()), None)
    if temp_key is None:
        return False
    time_key = "Time" if "Time" in log_data else list(log_data.keys())[0]
    time = np.array(log_data.get(time_key, range(len(log_data[temp_key]))))
    temp = np.array(log_data[temp_key])
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(time, temp, color="firebrick", linewidth=0.8)
    ax.axhline(300, color="gray", linestyle="--", alpha=0.5, label="Target 300K")
    ax.set_xlabel("Time (ps)" if "ps" in time_key.lower() else "Step")
    ax.set_ylabel("Temperature (K)")
    ax.set_title("Temperature")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


def _plot_rmsf(plt, np, analysis, out_path):
    top10 = analysis.get("rmsf_top10", [])
    if not top10:
        return False
    names = [r["residue"] for r in top10]
    vals = [r["rmsf_nm"] for r in top10]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(names, vals, color="steelblue")
    ax.set_ylabel("RMSF (nm)")
    ax.set_title("Top 10 Residues by RMSF")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


def _plot_rmsf_full(plt, np, analysis, out_path):
    profile = analysis.get("rmsf_profile", [])
    if not profile:
        return False
    residues = [p["residue"] for p in profile]
    vals = [p["rmsf_angstrom"] for p in profile]
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.bar(residues, vals, color="steelblue", width=0.8)
    ax.set_xlabel("Residue"); ax.set_ylabel("RMSF (A)")
    ax.set_title("Per-Residue RMSF")
    fig.tight_layout(); fig.savefig(out_path, dpi=150); plt.close(fig)
    return True


def _plot_rmsd_time(plt, np, analysis, out_path):
    vals = analysis.get("rmsd_values", [])
    if not vals: return False
    rmsd_list = [f["rmsd_angstrom"] for f in vals]
    frames = [f["frame"] for f in vals]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(frames, rmsd_list, color="steelblue", linewidth=0.8)
    ax.axhline(np.mean(rmsd_list), color="darkorange", linestyle="--", alpha=0.7,
               label=f"Mean: {np.mean(rmsd_list):.2f} A")
    ax.set_xlabel("Frame"); ax.set_ylabel("RMSD (A)")
    ax.set_title("Protein C-alpha RMSD"); ax.legend()
    fig.tight_layout(); fig.savefig(out_path, dpi=150); plt.close(fig)
    return True


def _plot_rg(plt, np, analysis, out_path):
    vals = analysis.get("rg_values", [])
    if not vals: return False
    rg_list = [f["rg_angstrom"] for f in vals]
    frames = [f["frame"] for f in vals]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(frames, rg_list, color="darkgreen", linewidth=0.8)
    ax.axhline(np.mean(rg_list), color="gray", linestyle="--", alpha=0.5,
               label=f"Mean: {np.mean(rg_list):.1f} A")
    ax.set_xlabel("Frame"); ax.set_ylabel("Rg (A)")
    ax.set_title("Radius of Gyration"); ax.legend()
    fig.tight_layout(); fig.savefig(out_path, dpi=150); plt.close(fig)
    return True


def _plot_sasa(plt, np, analysis, out_path):
    vals = analysis.get("sasa_values", [])
    if not vals: return False
    sasa_list = [f["sasa_angstrom2"] for f in vals]
    frames = [f["frame"] for f in vals]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(frames, sasa_list, color="mediumblue", linewidth=0.8)
    ax.set_xlabel("Frame"); ax.set_ylabel("SASA (A^2)")
    ax.set_title("Solvent Accessible Surface Area")
    fig.tight_layout(); fig.savefig(out_path, dpi=150); plt.close(fig)
    return True


def _plot_hbond(plt, np, analysis, out_path):
    counts = analysis.get("hbond_counts", [])
    if not counts: return False
    frames = list(range(1, len(counts) + 1))
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(frames, counts, color="teal", linewidth=0.8)
    ax.axhline(np.mean(counts), color="gray", linestyle="--", alpha=0.5,
               label=f"Mean: {np.mean(counts):.1f}")
    ax.set_xlabel("Frame"); ax.set_ylabel("H-bonds")
    ax.set_title("Protein-Ligand Hydrogen Bonds"); ax.legend()
    fig.tight_layout(); fig.savefig(out_path, dpi=150); plt.close(fig)
    return True


def _plot_fel(plt, np, analysis, out_path):
    projs = analysis.get("projections", [])
    if len(projs) < 10: return False
    pc1 = np.array([p["pc1"] for p in projs])
    pc2 = np.array([p["pc2"] for p in projs])
    fig, ax = plt.subplots(figsize=(7, 6))
    hist, xedges, yedges = np.histogram2d(pc1, pc2, bins=50)
    hist = np.where(hist > 0, hist, 1)
    kt = 0.593
    fel = -kt * np.log(hist / hist.max())
    im = ax.contourf((xedges[:-1]+xedges[1:])/2, (yedges[:-1]+yedges[1:])/2,
                      fel.T, levels=15, cmap="viridis")
    plt.colorbar(im, ax=ax, label="Free Energy (kcal/mol)")
    ax.set_xlabel("PC1"); ax.set_ylabel("PC2")
    ax.set_title("Free Energy Landscape (PC1 vs PC2)")
    fig.tight_layout(); fig.savefig(out_path, dpi=150); plt.close(fig)
    return True


def md_plot(state: AutoMDState) -> Command:
    pid = state.get("project_id") or "default"
    md_dir = work_root(state) / "md" / pid
    plots_dir = ensure_dir(md_dir / "plots")

    top_path = str(md_dir / "system.pdb")
    traj_path = str(md_dir / "trajectory.dcd")
    analysis_path = str(md_dir / "analysis.json")
    log_path = str(md_dir / "production.log")

    analysis = _read_analysis_json(analysis_path)
    log_data = _read_production_log(log_path)
    has_top = os.path.exists(top_path)
    has_traj = os.path.exists(traj_path)

    # Try loading cpptraj analysis summary
    cpptraj_summary = _read_analysis_json(
        str(work_root(state) / "analysis" / pid / "cpptraj" / "analysis_summary.json")
    ) or {}

    if not has_top and not analysis and not log_data:
        return handle_tool_error(
            result=failed(errors=["MD 产物缺失: system.pdb / analysis.json / production.log 均不存在"]),
            state=state, calling_node="md_plot", retry_goto="md_plot", skip_update={},
        )

    try:
        plt, np = _safe_import()
    except ImportError:
        return handle_tool_error(
            result=failed(errors=["matplotlib not installed"], env_packages=["matplotlib"]),
            state=state, calling_node="md_plot", retry_goto="md_plot", skip_update={},
        )

    generated = []

    # RMSD
    rmsd_path = str(plots_dir / "rmsd.png")
    if analysis and _plot_rmsd(plt, np, analysis, rmsd_path):
        generated.append(rmsd_path)
    elif has_top and has_traj and _plot_rmsd_from_traj(plt, np, top_path, traj_path, rmsd_path):
        generated.append(rmsd_path)

    # RMSF
    rmsf_path = str(plots_dir / "rmsf.png")
    if analysis and _plot_rmsf(plt, np, analysis, rmsf_path):
        generated.append(rmsf_path)

    # Energy
    energy_path = str(plots_dir / "energy.png")
    if log_data and _plot_energy(plt, np, log_data, energy_path):
        generated.append(energy_path)

    # Temperature
    temp_path = str(plots_dir / "temperature.png")
    if log_data and _plot_temperature(plt, np, log_data, temp_path):
        generated.append(temp_path)

    # ── cpptraj-based plots ──
    cpptraj = cpptraj_summary

    rmsd_ca_cpp = cpptraj.get("rmsd_ca", {})
    if rmsd_ca_cpp and _plot_rmsd_time(plt, np, rmsd_ca_cpp, str(plots_dir / "rmsd_timeseries.png")):
        generated.append(str(plots_dir / "rmsd_timeseries.png"))

    rmsf_cpp = cpptraj.get("rmsf", {})
    if rmsf_cpp and _plot_rmsf_full(plt, np, rmsf_cpp, str(plots_dir / "rmsf_full.png")):
        generated.append(str(plots_dir / "rmsf_full.png"))

    rg_cpp = cpptraj.get("rg", {})
    if rg_cpp and _plot_rg(plt, np, rg_cpp, str(plots_dir / "rg.png")):
        generated.append(str(plots_dir / "rg.png"))

    sasa_cpp = cpptraj.get("sasa", {})
    if sasa_cpp and _plot_sasa(plt, np, sasa_cpp, str(plots_dir / "sasa.png")):
        generated.append(str(plots_dir / "sasa.png"))

    hb_cpp = cpptraj.get("hbonds", {})
    if hb_cpp and _plot_hbond(plt, np, hb_cpp, str(plots_dir / "hbonds.png")):
        generated.append(str(plots_dir / "hbonds.png"))

    pca_cpp = cpptraj.get("pca", {})
    if pca_cpp and _plot_fel(plt, np, pca_cpp, str(plots_dir / "fel.png")):
        generated.append(str(plots_dir / "fel.png"))

    if not generated:
        return Command(
            update={"analysis_summary": "MD 图表: 全部生成失败（文件格式不支持）"},
            goto="report",
        )

    names = ", ".join(Path(p).name for p in generated)
    return Command(
        update={
            "md_plot_result": f"MD 图表 ({len(generated)} 张): {names}",
        },
        goto="report",
    )
