"""
cpptraj-based trajectory analysis tools.

All functions accept AMBER prmtop + trajectory DCD, generate cpptraj input
scripts, invoke cpptraj via subprocess, parse outputs, and return ToolResult
with structured analysis data suitable for downstream plotting and reporting.

Requirement: cpptraj on PATH (provided by AmberTools conda environment).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from tools.shared import success, degraded, failed, ToolResult, is_tool_available, run_in_conda_env


def _find_cpptraj() -> str | None:
    # Fast path: local PATH (avoids the 10s conda call when possible).
    for candidate in [
        shutil.which("cpptraj"),
        os.path.expanduser("~/miniconda3/envs/AutoMD/bin/cpptraj"),
        os.path.expanduser("~/miniforge3/envs/AutoMD/bin/cpptraj"),
        os.path.expanduser("~/anaconda3/envs/AutoMD/bin/cpptraj"),
    ]:
        if candidate and os.path.exists(candidate):
            return candidate
    # Slow path: ask the LangGraph conda env.
    if is_tool_available("cpptraj"):
        return "cpptraj"  # placeholder; we'll route via run_in_conda_env
    return None


def _run_cpptraj(prmtop: str, traj: str, script: str, cwd: str) -> tuple[bool, str, str]:
    cpptraj = _find_cpptraj()
    if not cpptraj:
        return False, "", "cpptraj not found on PATH or in LangGraph conda env"

    script_file = os.path.join(cwd, "cpptraj.in")
    with open(script_file, "w") as f:
        f.write(f"parm {prmtop}\n")
        f.write(f"trajin {traj}\n")
        f.write(script)
        f.write("\nrun\nquit\n")

    # If _find_cpptraj returned a real absolute path, use it directly via conda
    # env (still safer than trusting the local PATH). If it returned the bare
    # "cpptraj" placeholder, conda run resolves it inside the env.
    cmd = [cpptraj, "-i", script_file] if os.path.isabs(cpptraj) else ["cpptraj", "-i", script_file]
    try:
        result = run_in_conda_env(cmd, cwd=cwd, timeout=600)
    except Exception as e:
        return False, "", f"cpptraj invocation failed: {e}"
    return result.returncode == 0, result.stdout, result.stderr


# ── Individual analyses ────────────────────────────────────────────────────


def run_rmsd_analysis(
    prmtop: str, trajectory: str, output_dir: str,
    mask: str = "@CA", ref: str = "first",
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    dat_file = str(out / "rmsd_ca.dat")
    script = f"rms {ref} {mask} out {dat_file} nofit\n"
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))

    if not ok or not os.path.exists(dat_file):
        return failed(errors=[f"cpptraj RMSD failed: {stderr or stdout}"])

    frames = []
    with open(dat_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                frames.append({"frame": int(parts[0]), "rmsd_angstrom": float(parts[1])})

    if not frames:
        return failed(errors=["No RMSD data parsed"])
    import numpy as np
    vals = [f["rmsd_angstrom"] for f in frames]
    return success({
        "rmsd_values": frames,
        "mean_angstrom": round(float(np.mean(vals)), 3),
        "std_angstrom": round(float(np.std(vals)), 3),
        "final_angstrom": round(vals[-1], 3),
        "n_frames": len(frames),
        "out_file": dat_file,
    })


def run_rmsf_analysis(
    prmtop: str, trajectory: str, output_dir: str, mask: str = "@CA",
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    dat_file = str(out / "rmsf.dat")
    script = f"atomicfluct out {dat_file} {mask} byres\n"
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))

    if not ok or not os.path.exists(dat_file):
        return failed(errors=[f"cpptraj RMSF failed: {stderr or stdout}"])

    profile = []
    with open(dat_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                profile.append({"residue": int(parts[0]), "rmsf_angstrom": float(parts[1])})

    top10 = sorted(profile, key=lambda x: x["rmsf_angstrom"], reverse=True)[:10]
    return success({
        "rmsf_profile": profile,
        "top10": top10,
        "mean_angstrom": round(sum(p["rmsf_angstrom"] for p in profile) / max(len(profile), 1), 3) if profile else 0,
        "out_file": dat_file,
    })


def run_rog_analysis(
    prmtop: str, trajectory: str, output_dir: str, mask: str = "@CA",
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    dat_file = str(out / "rog.dat")
    script = f"radgyr {mask} out {dat_file}\n"
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))

    if not ok or not os.path.exists(dat_file):
        return failed(errors=[f"cpptraj Rg failed: {stderr or stdout}"])
    frames = []
    with open(dat_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                frames.append({"frame": int(parts[0]), "rg_angstrom": float(parts[1])})
    if not frames:
        return failed(errors=["No Rg data"])
    import numpy as np
    vals = [f["rg_angstrom"] for f in frames]
    return success({
        "rg_values": frames,
        "mean_angstrom": round(float(np.mean(vals)), 3),
        "std_angstrom": round(float(np.std(vals)), 3),
        "out_file": dat_file,
    })


def run_hbond_analysis(
    prmtop: str, trajectory: str, output_dir: str,
    donor_mask: str = ":LIG", acceptor_mask: str = ":1-999@N,O",
    dist_cutoff: float = 3.0, angle_cutoff: float = 135.0,
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    dat_file = str(out / "hbonds.dat")
    avg_file = str(out / "hbond_avg.dat")
    script = (
        f"hbond HB out {dat_file} avgout {avg_file} "
        f"donormask {donor_mask} acceptormask {acceptor_mask} "
        f"dist {dist_cutoff} angle {angle_cutoff} series hbt\n"
    )
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))

    counts = []
    occupancy = []
    if os.path.exists(dat_file):
        with open(dat_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    counts.append(int(parts[1]))
    if os.path.exists(avg_file):
        with open(avg_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    occupancy.append({"donor": parts[0], "acceptor": parts[1], "fraction": float(parts[2])})

    return success({
        "hbond_counts": counts,
        "mean_per_frame": round(sum(counts) / max(len(counts), 1), 1) if counts else 0,
        "max_per_frame": max(counts) if counts else 0,
        "occupancy_top": sorted(occupancy, key=lambda x: x["fraction"], reverse=True)[:10],
        "n_frames": len(counts),
        "out_file": dat_file,
        "avg_file": avg_file,
    })


def run_sasa_analysis(
    prmtop: str, trajectory: str, output_dir: str, mask: str = ":1-999",
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    dat_file = str(out / "sasa.dat")
    script = f"surf {mask} out {dat_file}\n"
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))
    frames = []
    if ok and os.path.exists(dat_file):
        with open(dat_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    frames.append({"frame": int(parts[0]), "sasa_angstrom2": float(parts[1])})
    if not frames:
        return failed(errors=[f"cpptraj SASA failed: {stderr}"])
    import numpy as np
    vals = [f["sasa_angstrom2"] for f in frames]
    return success({
        "sasa_values": frames,
        "mean_angstrom2": round(float(np.mean(vals)), 1),
        "out_file": dat_file,
    })


def run_dssp_analysis(
    prmtop: str, trajectory: str, output_dir: str, mask: str = ":1-999",
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    dat_file = str(out / "dssp.dat")
    script = f"secstruct {mask} out {dat_file}\n"
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))
    ss_summary = {}
    if ok and os.path.exists(dat_file):
        with open(dat_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    ss_summary[parts[1]] = round(float(parts[2]), 3)
    return success({"secondary_structure": ss_summary, "out_file": dat_file})


def run_pca_analysis(
    prmtop: str, trajectory: str, output_dir: str,
    mask: str = "@CA", n_components: int = 3,
) -> ToolResult:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    proj_file = str(out / "pca_proj.dat")
    evec_file = str(out / "pca_evecs.dat")
    eval_file = str(out / "pca_evals.dat")
    script = (
        f"matrix covar {mask} out covar.dat name COV\n"
        f"diagmatrix COV out evecs {evec_file} vecs {n_components} "
        f"name EIGVAL out {eval_file}\n"
        f"crdaction trajectory.dcd projection PROJ {mask} "
        f"out {proj_file} beg 1 end {n_components}\n"
    )
    ok, stdout, stderr = _run_cpptraj(prmtop, trajectory, script, str(out))
    projections = []
    eigenvalues = []
    if ok and os.path.exists(proj_file):
        with open(proj_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    proj = {"frame": int(parts[0])}
                    for i, v in enumerate(parts[1:n_components + 1]):
                        proj[f"pc{i + 1}"] = float(v)
                    projections.append(proj)
    if os.path.exists(eval_file):
        with open(eval_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    eigenvalues.append(float(line.split()[0]))
                except (ValueError, IndexError):
                    pass
    if not projections:
        return failed(errors=[f"cpptraj PCA failed: {stderr}"])
    total_ev = sum(eigenvalues) if eigenvalues else 1.0
    explained = [round(ev / total_ev, 4) for ev in eigenvalues[:n_components]] if eigenvalues else []
    return success({
        "projections": projections,
        "eigenvalues": eigenvalues,
        "explained_variance": explained,
        "n_frames": len(projections),
        "out_file": proj_file,
    })


# ── Combined analysis entry point ──────────────────────────────────────────


def run_full_analysis(
    prmtop: str,
    trajectory: str,
    output_dir: str,
    ligand_resname: str = "LIG",
) -> ToolResult:
    """Run all standard cpptraj analyses and return a combined report.

    This is the main entry point used by the trajectory_analysis node.
    """
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)

    if not prmtop or not os.path.exists(prmtop):
        return failed(errors=[f"prmtop not found: {prmtop}"])
    if not trajectory or not os.path.exists(trajectory):
        return failed(errors=[f"trajectory not found: {trajectory}"])

    results = {}
    degradations = []
    errors_list = []

    analyses = [
        ("rmsd_ca", lambda: run_rmsd_analysis(prmtop, trajectory, str(out / "rmsd"))),
        ("rmsf", lambda: run_rmsf_analysis(prmtop, trajectory, str(out / "rmsf"))),
        ("rg", lambda: run_rog_analysis(prmtop, trajectory, str(out / "rog"))),
        ("hbonds", lambda: run_hbond_analysis(
            prmtop, trajectory, str(out / "hbonds"),
            donor_mask=f":{ligand_resname}",
        )),
        ("sasa", lambda: run_sasa_analysis(prmtop, trajectory, str(out / "sasa"))),
        ("dssp", lambda: run_dssp_analysis(prmtop, trajectory, str(out / "dssp"))),
        ("pca", lambda: run_pca_analysis(prmtop, trajectory, str(out / "pca"))),
        ("rmsd_ligand", lambda: run_rmsd_analysis(
            prmtop, trajectory, str(out / "rmsd_lig"),
            mask=f":{ligand_resname}&!@H",
        )),
    ]

    for key, fn in analyses:
        try:
            r = fn()
            if r.ok:
                results[key] = r.data
            else:
                degradations.append(f"{key} failed: {'; '.join(r.errors)}")
                results[key] = {"error": "; ".join(r.errors)}
        except Exception as e:
            degradations.append(f"{key} exception: {e}")
            results[key] = {"error": str(e)}

    summary_file = out / "analysis_summary.json"
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    if degradations:
        return degraded(data=results, degradation=degradations, errors=errors_list)

    return success(results)
