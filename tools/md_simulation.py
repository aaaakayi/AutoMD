"""
OpenMM-based molecular dynamics simulation tool.

Pipeline: build system -> minimize -> NVT equil -> NPT equil -> production -> analysis

Uses OpenMM (already in requirements.txt). Python-native, no subprocess calls needed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.shared import PROJECT_ROOT, success, degraded, failed, ToolResult


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ============================================================================
# Main entry point
# ============================================================================


def run_md_simulation(
    protein_prmtop: str,
    protein_inpcrd: str,
    ligand_prmtop: str,
    ligand_inpcrd: str,
    output_dir: str,
    duration_ns: float = 10.0,
    temperature_k: float = 300.0,
    pressure_atm: float = 1.0,
    timestep_fs: float = 2.0,
    save_interval_ps: float = 100.0,
    nvt_equil_ps: float = 100.0,
    npt_equil_ps: float = 100.0,
    restart_from: str = "",
) -> ToolResult:
    """Run a full MD simulation pipeline using OpenMM.

    Args:
        protein_prmtop: Path to protein AMBER topology (prmtop).
        protein_inpcrd: Path to protein AMBER coordinates (inpcrd).
        ligand_prmtop: Path to ligand AMBER topology (prmtop).
        ligand_inpcrd: Path to ligand AMBER coordinates (inpcrd).
        output_dir: Output directory.
        duration_ns: Production simulation duration in ns (default 10).
        temperature_k: Temperature in Kelvin.
        pressure_atm: Pressure in atm.
        timestep_fs: Integration timestep in fs.
        save_interval_ps: Trajectory frame saving interval in ps.
        nvt_equil_ps: NVT equilibration duration in ps.
        npt_equil_ps: NPT equilibration duration in ps.
        restart_from: Optional checkpoint directory to resume from.

    Returns:
        ToolResult with summary data including trajectory path and analysis.
    """
    out = _ensure_dir(Path(output_dir))
    degradations: list[str] = []
    errors: list[str] = []
    stage_times: dict[str, float] = {}

    try:
        from openmm import unit, app
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    try:
        checkpoint_dir = out / "checkpoints"

        # --- Stage 1: Build system ---
        t0 = time.time()
        build_result = _build_system(
            protein_prmtop, protein_inpcrd,
            ligand_prmtop, ligand_inpcrd, out,
        )
        if not build_result.ok:
            return build_result
        system_xml = build_result.data["system_xml"]
        topology_pdb = build_result.data["topology_path"]
        stage_times["build_system"] = round(time.time() - t0, 1)
        degradations.extend(build_result.degradation)
        errors.extend(build_result.errors)

        # --- Stage 2: Minimize ---
        t0 = time.time()
        min_ckpt = str(checkpoint_dir / "stage2_minimized")
        min_result = _minimize_energy(system_xml, topology_pdb, out, min_ckpt)
        if not min_result.ok:
            return min_result
        stage_times["minimize"] = round(time.time() - t0, 1)
        degradations.extend(min_result.degradation)
        errors.extend(min_result.errors)

        # --- Stage 3: NVT Equilibration ---
        t0 = time.time()
        nvt_ckpt = str(checkpoint_dir / "stage3_nvt")
        nvt_result = _equilibrate_nvt(
            system_xml, topology_pdb,
            min_result.data["positions_path"],
            out, nvt_ckpt,
            duration_ps=nvt_equil_ps,
            temperature_k=temperature_k,
            timestep_fs=timestep_fs,
            save_interval_ps=save_interval_ps,
        )
        if not nvt_result.ok:
            return degraded(
                data=nvt_result.data,
                degradation=degradations,
                errors=errors + nvt_result.errors,
            )
        stage_times["nvt_equil"] = round(time.time() - t0, 1)
        degradations.extend(nvt_result.degradation)
        errors.extend(nvt_result.errors)

        # --- Stage 4: NPT Equilibration ---
        t0 = time.time()
        npt_ckpt = str(checkpoint_dir / "stage4_npt")
        npt_result = _equilibrate_npt(
            system_xml, topology_pdb,
            nvt_result.data["checkpoint_path"],
            out, npt_ckpt,
            duration_ps=npt_equil_ps,
            temperature_k=temperature_k,
            pressure_atm=pressure_atm,
            timestep_fs=timestep_fs,
            save_interval_ps=save_interval_ps,
        )
        if not npt_result.ok:
            return degraded(
                data=npt_result.data,
                degradation=degradations,
                errors=errors + npt_result.errors,
            )
        stage_times["npt_equil"] = round(time.time() - t0, 1)
        degradations.extend(npt_result.degradation)
        errors.extend(npt_result.errors)

        # --- Stage 5: Production ---
        t0 = time.time()
        prod_ckpt = str(checkpoint_dir / "stage5_production")
        prod_result = _run_production(
            system_xml, topology_pdb,
            npt_result.data["checkpoint_path"],
            out, prod_ckpt,
            duration_ns=duration_ns,
            temperature_k=temperature_k,
            pressure_atm=pressure_atm,
            timestep_fs=timestep_fs,
            save_interval_ps=save_interval_ps,
        )
        if not prod_result.ok:
            return degraded(
                data=prod_result.data,
                degradation=degradations,
                errors=errors + prod_result.errors,
            )
        stage_times["production"] = round(time.time() - t0, 1)
        degradations.extend(prod_result.degradation)
        errors.extend(prod_result.errors)

        # --- Stage 6: Analysis ---
        t0 = time.time()
        analysis_result = _analyze_trajectory(
            topology_pdb,
            prod_result.data["trajectory_dcd"],
            out,
        )
        stage_times["analysis"] = round(time.time() - t0, 1)

        summary = {
            "trajectory_dcd": prod_result.data.get("trajectory_dcd", ""),
            "topology_pdb": topology_pdb,
            "duration_ns": duration_ns,
            "temperature_k": temperature_k,
            "n_frames": prod_result.data.get("n_frames", 0),
            "stage_times_seconds": stage_times,
            "total_wall_time_s": round(sum(stage_times.values()), 1),
            "analysis": analysis_result.data if analysis_result.ok else {},
        }

        if not errors:
            return success(summary, warnings=degradations)
        return degraded(summary, degradation=degradations, errors=errors)

    except Exception as exc:
        errors.append(str(exc))
        return failed(errors=errors, degradation=degradations)


# ============================================================================
# Stage 1: System Builder
# ============================================================================


def _build_system(
    protein_prmtop: str,
    protein_inpcrd: str,
    ligand_prmtop: str,
    ligand_inpcrd: str,
    output_dir: Path,
) -> ToolResult:
    """Build solvated, neutralized protein-ligand system."""
    try:
        from openmm import unit, app
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    degradations: list[str] = []
    errors: list[str] = []

    try:
        prot_prmtop = app.AmberPrmtopFile(protein_prmtop)
        prot_inpcrd = app.AmberInpcrdFile(protein_inpcrd)
        lig_prmtop = app.AmberPrmtopFile(ligand_prmtop)
        lig_inpcrd = app.AmberInpcrdFile(ligand_inpcrd)

        forcefield = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")

        modeller = app.Modeller(prot_prmtop.topology, prot_inpcrd.positions)
        modeller.add(lig_prmtop.topology, lig_inpcrd.positions)

        modeller.addSolvent(
            forcefield,
            model="tip3p",
            padding=1.0 * unit.nanometers,
            ionicStrength=0.15 * unit.molar,
        )

        topology_path = str(output_dir / "system.pdb")
        with open(topology_path, "w") as f:
            app.PDBFile.writeFile(modeller.topology, modeller.positions, f)

        system = forcefield.createSystem(
            modeller.topology,
            nonbondedMethod=app.PME,
            nonbondedCutoff=1.0 * unit.nanometers,
            constraints=app.HBonds,
            rigidWater=True,
        )

        from openmm import XmlSerializer

        system_xml = XmlSerializer.serialize(system)
        system_xml_path = output_dir / "system.xml"
        with open(system_xml_path, "w") as f:
            f.write(system_xml)

        ckpt_dir = _ensure_dir(output_dir / "checkpoints")
        with open(ckpt_dir / "stage1_system.json", "w") as f:
            f.write(system_xml)

        return success({
            "system_xml": system_xml,
            "topology_path": topology_path,
            "n_atoms": modeller.topology.getNumAtoms(),
            "n_residues": modeller.topology.getNumResidues(),
        })

    except Exception as exc:
        errors.append(f"System build failed: {exc}")
        return failed(errors=errors, degradation=degradations)


# ============================================================================
# Stage 2: Energy Minimization
# ============================================================================


def _minimize_energy(
    system_xml: str,
    topology_pdb: str,
    output_dir: Path,
    checkpoint_dir: str,
    max_iterations: int = 1000,
    tolerance: float = 10.0,
) -> ToolResult:
    """Minimize system energy via steepest descent."""
    try:
        from openmm import unit, app
        from openmm import XmlSerializer, VerletIntegrator
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    errors: list[str] = []

    try:
        system = XmlSerializer.deserialize(system_xml)
        pdb = app.PDBFile(topology_pdb)
        integrator = VerletIntegrator(0.001 * unit.picoseconds)
        simulation = app.Simulation(pdb.topology, system, integrator)
        simulation.context.setPositions(pdb.positions)

        simulation.minimizeEnergy(
            maxIterations=max_iterations,
            tolerance=tolerance * unit.kilojoules_per_mole / unit.nanometer,
        )

        min_state = simulation.context.getState(getPositions=True, getEnergy=True)
        min_positions = min_state.getPositions()

        min_pdb_path = str(output_dir / "minimized.pdb")
        with open(min_pdb_path, "w") as f:
            app.PDBFile.writeFile(pdb.topology, min_positions, f)

        ckpt = _ensure_dir(Path(checkpoint_dir))
        positions_list = [
            {"x": float(p[0].value_in_unit(unit.nanometer)),
             "y": float(p[1].value_in_unit(unit.nanometer)),
             "z": float(p[2].value_in_unit(unit.nanometer))}
            for p in min_positions
        ]
        with open(ckpt / "positions.json", "w") as f:
            json.dump(positions_list, f)

        pe = min_state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)

        return success({
            "positions_path": min_pdb_path,
            "checkpoint_path": str(ckpt / "positions.json"),
            "potential_energy_kj_per_mol": round(pe, 2),
        })

    except Exception as exc:
        errors.append(f"Minimization failed: {exc}")
        return failed(errors=errors)


# ============================================================================
# Stage 3: NVT Equilibration
# ============================================================================


def _equilibrate_nvt(
    system_xml: str,
    topology_pdb: str,
    positions_path: str,
    output_dir: Path,
    checkpoint_dir: str,
    duration_ps: float = 100.0,
    temperature_k: float = 300.0,
    timestep_fs: float = 2.0,
    save_interval_ps: float = 10.0,
) -> ToolResult:
    """NVT equilibration with protein heavy-atom restraints."""
    try:
        from openmm import unit, app
        from openmm import XmlSerializer, LangevinMiddleIntegrator
        import numpy as np
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    errors: list[str] = []

    try:
        system = XmlSerializer.deserialize(system_xml)
        pdb = app.PDBFile(topology_pdb)

        _add_position_restraints(system, pdb.topology, 10.0)

        integrator = LangevinMiddleIntegrator(
            temperature_k * unit.kelvin,
            1.0 / unit.picosecond,
            timestep_fs * unit.femtoseconds,
        )

        simulation = app.Simulation(pdb.topology, system, integrator)

        if positions_path.endswith(".json"):
            with open(positions_path, "r") as f:
                pos_data = json.load(f)
            positions = [
                unit.Quantity(np.array([p["x"], p["y"], p["z"]]), unit.nanometer)
                for p in pos_data
            ]
        elif positions_path.endswith(".pdb"):
            pos_pdb = app.PDBFile(positions_path)
            positions = pos_pdb.positions
        else:
            positions = pdb.positions

        simulation.context.setPositions(positions)
        simulation.context.setVelocitiesToTemperature(temperature_k * unit.kelvin)

        dcd_path = str(output_dir / "nvt.dcd")
        log_path = str(output_dir / "nvt.log")

        n_steps = int(duration_ps * 1000 / timestep_fs)
        report_interval = int(save_interval_ps * 1000 / timestep_fs)

        simulation.reporters.append(app.DCDReporter(dcd_path, report_interval))
        simulation.reporters.append(app.StateDataReporter(
            log_path, report_interval,
            step=True, time=True, potentialEnergy=True,
            kineticEnergy=True, temperature=True,
        ))

        simulation.step(n_steps)

        ckpt = _ensure_dir(Path(checkpoint_dir))
        chk_path = str(ckpt / "nvt.chk")
        simulation.saveCheckpoint(chk_path)

        state = simulation.context.getState(getPositions=True)
        final_pos = state.getPositions()
        pos_list = [
            {"x": float(p[0].value_in_unit(unit.nanometer)),
             "y": float(p[1].value_in_unit(unit.nanometer)),
             "z": float(p[2].value_in_unit(unit.nanometer))}
            for p in final_pos
        ]
        with open(ckpt / "positions.json", "w") as f:
            json.dump(pos_list, f)

        return success({
            "checkpoint_path": chk_path,
            "dcd_path": dcd_path,
            "log_path": log_path,
            "n_steps": n_steps,
        })

    except Exception as exc:
        errors.append(f"NVT equilibration failed: {exc}")
        return failed(errors=errors)


# ============================================================================
# Stage 4: NPT Equilibration
# ============================================================================


def _equilibrate_npt(
    system_xml: str,
    topology_pdb: str,
    checkpoint_path: str,
    output_dir: Path,
    checkpoint_dir: str,
    duration_ps: float = 100.0,
    temperature_k: float = 300.0,
    pressure_atm: float = 1.0,
    timestep_fs: float = 2.0,
    save_interval_ps: float = 10.0,
) -> ToolResult:
    """NPT equilibration with lighter restraints and barostat."""
    try:
        from openmm import unit, app
        from openmm import XmlSerializer, LangevinMiddleIntegrator, MonteCarloBarostat
        import numpy as np
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    errors: list[str] = []

    try:
        system = XmlSerializer.deserialize(system_xml)
        pdb = app.PDBFile(topology_pdb)

        _add_position_restraints(system, pdb.topology, 5.0)

        integrator = LangevinMiddleIntegrator(
            temperature_k * unit.kelvin,
            1.0 / unit.picosecond,
            timestep_fs * unit.femtoseconds,
        )

        simulation = app.Simulation(pdb.topology, system, integrator)

        if checkpoint_path.endswith(".chk"):
            simulation.loadCheckpoint(checkpoint_path)
        elif checkpoint_path.endswith(".json"):
            with open(checkpoint_path, "r") as f:
                pos_data = json.load(f)
            positions = [
                unit.Quantity(np.array([p["x"], p["y"], p["z"]]), unit.nanometer)
                for p in pos_data
            ]
            simulation.context.setPositions(positions)
            simulation.context.setVelocitiesToTemperature(temperature_k * unit.kelvin)

        system.addForce(
            MonteCarloBarostat(
                pressure_atm * unit.atmosphere,
                temperature_k * unit.kelvin,
            )
        )
        simulation.context.reinitialize(preserveState=True)

        dcd_path = str(output_dir / "npt.dcd")
        log_path = str(output_dir / "npt.log")

        n_steps = int(duration_ps * 1000 / timestep_fs)
        report_interval = int(save_interval_ps * 1000 / timestep_fs)

        simulation.reporters.append(app.DCDReporter(dcd_path, report_interval))
        simulation.reporters.append(app.StateDataReporter(
            log_path, report_interval,
            step=True, time=True, potentialEnergy=True,
            kineticEnergy=True, temperature=True, density=True, volume=True,
        ))

        simulation.step(n_steps)

        ckpt = _ensure_dir(Path(checkpoint_dir))
        chk_path = str(ckpt / "npt.chk")
        simulation.saveCheckpoint(chk_path)

        return success({
            "checkpoint_path": chk_path,
            "dcd_path": dcd_path,
            "log_path": log_path,
            "n_steps": n_steps,
        })

    except Exception as exc:
        errors.append(f"NPT equilibration failed: {exc}")
        return failed(errors=errors)


# ============================================================================
# Stage 5: Production MD
# ============================================================================


def _run_production(
    system_xml: str,
    topology_pdb: str,
    checkpoint_path: str,
    output_dir: Path,
    checkpoint_dir: str,
    duration_ns: float = 10.0,
    temperature_k: float = 300.0,
    pressure_atm: float = 1.0,
    timestep_fs: float = 2.0,
    save_interval_ps: float = 100.0,
) -> ToolResult:
    """Production NPT MD (unrestrained)."""
    try:
        from openmm import unit, app
        from openmm import XmlSerializer, LangevinMiddleIntegrator, MonteCarloBarostat
        import numpy as np
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    errors: list[str] = []

    try:
        system = XmlSerializer.deserialize(system_xml)
        pdb = app.PDBFile(topology_pdb)

        integrator = LangevinMiddleIntegrator(
            temperature_k * unit.kelvin,
            1.0 / unit.picosecond,
            timestep_fs * unit.femtoseconds,
        )

        simulation = app.Simulation(pdb.topology, system, integrator)

        if checkpoint_path.endswith(".chk"):
            simulation.loadCheckpoint(checkpoint_path)
        else:
            with open(checkpoint_path, "r") as f:
                pos_data = json.load(f)
            positions = [
                unit.Quantity(np.array([p["x"], p["y"], p["z"]]), unit.nanometer)
                for p in pos_data
            ]
            simulation.context.setPositions(positions)
            simulation.context.setVelocitiesToTemperature(temperature_k * unit.kelvin)

        system.addForce(
            MonteCarloBarostat(
                pressure_atm * unit.atmosphere,
                temperature_k * unit.kelvin,
            )
        )
        simulation.context.reinitialize(preserveState=True)

        dcd_path = str(output_dir / "production.dcd")
        log_path = str(output_dir / "production.log")

        total_ps = duration_ns * 1000
        n_steps = int(total_ps * 1000 / timestep_fs)
        report_interval = int(save_interval_ps * 1000 / timestep_fs)
        n_frames = n_steps // report_interval

        simulation.reporters.append(app.DCDReporter(dcd_path, report_interval))
        simulation.reporters.append(app.StateDataReporter(
            log_path, report_interval,
            step=True, time=True, potentialEnergy=True,
            kineticEnergy=True, temperature=True, density=True, volume=True,
        ))
        simulation.reporters.append(app.CheckpointReporter(
            str(_ensure_dir(Path(checkpoint_dir)) / "prod.chk"),
            report_interval * 10,
        ))

        simulation.step(n_steps)

        final_chk = str(Path(checkpoint_dir) / "prod_final.chk")
        simulation.saveCheckpoint(final_chk)

        return success({
            "checkpoint_path": final_chk,
            "trajectory_dcd": dcd_path,
            "log_path": log_path,
            "n_steps": n_steps,
            "n_frames": n_frames,
            "total_time_ns": duration_ns,
        })

    except Exception as exc:
        errors.append(f"Production MD failed: {exc}")
        return failed(errors=errors)


# ============================================================================
# Stage 6: Trajectory Analysis
# ============================================================================


def _analyze_trajectory(
    topology_pdb: str,
    trajectory_dcd: str,
    output_dir: Path,
) -> ToolResult:
    """Analyze MD trajectory: RMSD, RMSF, H-bonds, Rg."""
    try:
        from openmm import unit, app
        import numpy as np
    except ImportError:
        return failed(errors=["OpenMM not installed"])

    errors: list[str] = []

    try:
        pdb = app.PDBFile(topology_pdb)
        dcd = app.DCDFile(trajectory_dcd)

        protein_ca = [
            a.index for a in pdb.topology.atoms()
            if a.residue.chain.id == 0 and a.name == "CA"
        ]
        backbone = [
            a.index for a in pdb.topology.atoms()
            if a.residue.chain.id == 0 and a.name in ("N", "CA", "C")
        ]
        ligand_heavy = [
            a.index for a in pdb.topology.atoms()
            if a.residue.chain.id != 0 and a.element is not None and a.element.mass > 1.1
        ]

        rmsd_ca_vals: list[float] = []
        rmsd_lig_vals: list[float] = []
        rg_vals: list[float] = []
        hb_counts: list[int] = []
        ref_positions = None
        rmsf_acc: dict[int, list] = {}

        for positions in dcd:
            if ref_positions is None:
                ref_positions = positions

            if protein_ca:
                ca_rmsd = _compute_rmsd(positions, ref_positions, protein_ca)
                rmsd_ca_vals.append(ca_rmsd)

            if ligand_heavy:
                lig_rmsd = _compute_rmsd(positions, ref_positions, ligand_heavy)
                rmsd_lig_vals.append(lig_rmsd)

            if protein_ca:
                ca_xyz = np.array([
                    [float(positions[i][0].value_in_unit(unit.nanometer)),
                     float(positions[i][1].value_in_unit(unit.nanometer)),
                     float(positions[i][2].value_in_unit(unit.nanometer))]
                    for i in protein_ca
                ])
                center = ca_xyz.mean(axis=0)
                rg = float(np.sqrt(np.mean(np.sum((ca_xyz - center) ** 2, axis=1))))
                rg_vals.append(rg)

                for j, idx in enumerate(protein_ca):
                    if idx not in rmsf_acc:
                        rmsf_acc[idx] = []
                    rmsf_acc[idx].append(ca_xyz[j])

            if backbone and ligand_heavy:
                hb = _count_hbonds(positions, backbone, ligand_heavy)
                hb_counts.append(hb)

        rmsf_result: dict[int, float] = {}
        for idx, pos_list in rmsf_acc.items():
            arr = np.array(pos_list)
            avg = arr.mean(axis=0)
            msf = np.mean(np.sum((arr - avg) ** 2, axis=1))
            rmsf_result[idx] = float(np.sqrt(msf))

        atom_to_label: dict[int, str] = {}
        for atom in pdb.topology.atoms():
            if atom.index in protein_ca:
                atom_to_label[atom.index] = f"{atom.residue.name}{int(atom.residue.id)}"

        top_rmsf = sorted(rmsf_result.items(), key=lambda x: x[1], reverse=True)[:10]
        top_rmsf_list = [
            {"residue": atom_to_label.get(idx, str(idx)), "rmsf_nm": round(v, 4)}
            for idx, v in top_rmsf
        ]

        analysis = {
            "rmsd_ca": {
                "mean_nm": round(float(np.mean(rmsd_ca_vals)), 4) if rmsd_ca_vals else None,
                "std_nm": round(float(np.std(rmsd_ca_vals)), 4) if rmsd_ca_vals else None,
                "final_nm": round(rmsd_ca_vals[-1], 4) if rmsd_ca_vals else None,
            },
            "rmsd_ligand": {
                "mean_nm": round(float(np.mean(rmsd_lig_vals)), 4) if rmsd_lig_vals else None,
                "std_nm": round(float(np.std(rmsd_lig_vals)), 4) if rmsd_lig_vals else None,
                "final_nm": round(rmsd_lig_vals[-1], 4) if rmsd_lig_vals else None,
            },
            "rmsf_top10": top_rmsf_list,
            "radius_of_gyration": {
                "mean_nm": round(float(np.mean(rg_vals)), 4) if rg_vals else None,
            },
            "h_bonds": {
                "mean_per_frame": round(float(np.mean(hb_counts)), 1) if hb_counts else 0,
                "persistence": round(
                    sum(1 for h in hb_counts if h > 0) / max(len(hb_counts), 1), 2
                ),
            },
            "n_frames_analyzed": len(rmsd_ca_vals),
        }

        report_path = output_dir / "analysis.json"
        with open(report_path, "w") as f:
            json.dump(analysis, f, indent=2, default=str)

        return success(analysis)

    except Exception as exc:
        errors.append(f"Trajectory analysis failed: {exc}")
        return failed(errors=errors)


# ============================================================================
# Helpers
# ============================================================================


def _add_position_restraints(system: Any, topology: Any, force_constant: float) -> None:
    """Add harmonic position restraints on protein backbone heavy atoms (N,CA,C)."""
    from openmm import unit
    from openmm import CustomExternalForce

    restraint = CustomExternalForce(
        "0.5 * k * ((x - x0)^2 + (y - y0)^2 + (z - z0)^2)"
    )
    restraint.addGlobalParameter(
        "k", force_constant * unit.kilocalories_per_mole / unit.angstroms**2
    )
    restraint.addPerParticleParameter("x0")
    restraint.addPerParticleParameter("y0")
    restraint.addPerParticleParameter("z0")

    for atom in topology.atoms():
        if atom.name in ("N", "CA", "C") and atom.residue.chain.id == 0:
            restraint.addParticle(atom.index, [0.0, 0.0, 0.0])

    system.addForce(restraint)


def _compute_rmsd(
    positions: list, ref_positions: list, indices: list[int]
) -> float:
    """Compute RMSD with Kabsch alignment for selected atom indices."""
    from openmm import unit
    import numpy as np

    pos = np.array([
        [float(positions[i][0].value_in_unit(unit.nanometer)),
         float(positions[i][1].value_in_unit(unit.nanometer)),
         float(positions[i][2].value_in_unit(unit.nanometer))]
        for i in indices
    ])
    ref = np.array([
        [float(ref_positions[i][0].value_in_unit(unit.nanometer)),
         float(ref_positions[i][1].value_in_unit(unit.nanometer)),
         float(ref_positions[i][2].value_in_unit(unit.nanometer))]
        for i in indices
    ])

    pos_c = pos - pos.mean(axis=0)
    ref_c = ref - ref.mean(axis=0)

    try:
        u, s, vt = np.linalg.svd(pos_c.T @ ref_c)
        det = np.linalg.det(vt.T @ u.T)
        if det < 0:
            vt[-1] *= -1
        rot = vt.T @ u.T
        aligned = pos_c @ rot
    except np.linalg.LinAlgError:
        aligned = pos_c

    ssd = np.sum((aligned - ref_c) ** 2)
    return float(np.sqrt(ssd / len(indices)))


def _count_hbonds(
    positions: list,
    donor_indices: list[int],
    acceptor_indices: list[int],
    dist_cutoff: float = 0.35,
) -> int:
    """Count hydrogen bonds between two atom groups (distance-based)."""
    from openmm import unit
    import numpy as np

    count = 0
    for di in donor_indices:
        dp = np.array([
            float(positions[di][0].value_in_unit(unit.nanometer)),
            float(positions[di][1].value_in_unit(unit.nanometer)),
            float(positions[di][2].value_in_unit(unit.nanometer)),
        ])
        for ai in acceptor_indices:
            ap = np.array([
                float(positions[ai][0].value_in_unit(unit.nanometer)),
                float(positions[ai][1].value_in_unit(unit.nanometer)),
                float(positions[ai][2].value_in_unit(unit.nanometer)),
            ])
            if np.linalg.norm(dp - ap) < dist_cutoff:
                count += 1
    return count
