#!/usr/bin/env python3
"""
Custom MD simulation for protein-ligand complex using ParmEd + OpenMM.
v3 - Fixed minor bug, continues from NPT checkpoint.
"""
import os
import sys
import time
import numpy as np
import parmed as pmd
from parmed import unit as u
from parmed.openmm import StateDataReporter, NetCDFReporter
import openmm as mm
import openmm.app as app
import openmm.unit as omm_unit

# ========== CONFIGURATION ==========
PRMTOP = "./output/md/complex_with_lig.prmtop"
INPCRD = "./output/md/complex_with_lig.inpcrd"
OUTPUT_DIR = "./output/md"
DURATION_NS = 10.0
TEMPERATURE_K = 300.0
PRESSURE_ATM = 1.0
TIMESTEP_FS = 2.0
SAVE_INTERVAL_PS = 100.0
NVT_EQUIL_PS = 100.0
NPT_EQUIL_PS = 100.0

os.makedirs(OUTPUT_DIR, exist_ok=True)

print("=" * 60)
print("Loading AMBER topology with ParmEd...")
print("=" * 60)

# Load topology and coordinates
top = pmd.load_file(PRMTOP, xyz=INPCRD)
print(f"  Total atoms: {len(top.atoms)}")
print(f"  Total residues: {len(top.residues)}")

# Create OpenMM system from ParmEd
print("\nCreating OpenMM system...")
system = top.createSystem(
    nonbondedMethod=app.PME,
    nonbondedCutoff=1.0 * u.nanometers,
    constraints=app.HBonds,
    rigidWater=True,
    ewaldErrorTolerance=0.0005,
)

# Get topology for OpenMM
openmm_top = top.topology
print(f"  OpenMM topology: {openmm_top.getNumAtoms()} atoms, {openmm_top.getNumResidues()} residues")

positions = top.positions

# ========== 1. ENERGY MINIMIZATION ==========
print("\n" + "=" * 60)
print("PHASE 1: Energy Minimization")
print("=" * 60)

integrator = mm.LangevinMiddleIntegrator(
    TEMPERATURE_K * omm_unit.kelvin,
    1.0 / omm_unit.picosecond,
    TIMESTEP_FS * omm_unit.femtoseconds,
)

# Position restraints for heavy atoms during minimization
pos_restraint = mm.CustomExternalForce("k * ((x-x0)^2 + (y-y0)^2 + (z-z0)^2)")
pos_restraint.addPerParticleParameter("k")
pos_restraint.addPerParticleParameter("x0")
pos_restraint.addPerParticleParameter("y0")
pos_restraint.addPerParticleParameter("z0")

k_restraint = 10.0 * omm_unit.kilocalories_per_mole / omm_unit.angstroms**2

for atom in top.atoms:
    if atom.residue.name == 'LIG':
        if atom.element_name not in ('H', 'hydrogen'):
            pos_restraint.addParticle(atom.idx, [k_restraint, 
                positions[atom.idx][0], positions[atom.idx][1], positions[atom.idx][2]])
    elif atom.name == 'CA':
        pos_restraint.addParticle(atom.idx, [k_restraint,
            positions[atom.idx][0], positions[atom.idx][1], positions[atom.idx][2]])

system.addForce(pos_restraint)

simulation = app.Simulation(openmm_top, system, integrator)
simulation.context.setPositions(positions)

print("  Running minimization...")
t0 = time.time()
simulation.minimizeEnergy(maxIterations=500, tolerance=100.0 * omm_unit.kilojoules_per_mole / omm_unit.nanometer)
simulation.minimizeEnergy(maxIterations=10000, tolerance=1.0 * omm_unit.kilojoules_per_mole / omm_unit.nanometer)
t1 = time.time()
print(f"  Minimization complete in {t1-t0:.1f}s")

state = simulation.context.getState(getEnergy=True)
print(f"  Final potential energy: {state.getPotentialEnergy().value_in_unit(omm_unit.kilojoules_per_mole):.1f} kJ/mol")

state = simulation.context.getState(getPositions=True, enforcePeriodicBox=True)
with open(os.path.join(OUTPUT_DIR, 'minimized.pdb'), 'w') as f:
    app.PDBFile.writeFile(openmm_top, state.getPositions(), f, keepIds=True)
print("  Saved: minimized.pdb")

# Remove position restraints
system.removeForce(system.getNumForces() - 1)

# ========== 2. NVT EQUILIBRATION with GRADUAL HEATING ==========
print("\n" + "=" * 60)
print(f"PHASE 2: NVT Equilibration ({NVT_EQUIL_PS} ps)")
print("=" * 60)

# Add protein CA restraints (10 kcal/mol/A^2)
pos_restraint_nvt = mm.CustomExternalForce("k * ((x-x0)^2 + (y-y0)^2 + (z-z0)^2)")
pos_restraint_nvt.addPerParticleParameter("k")
pos_restraint_nvt.addPerParticleParameter("x0")
pos_restraint_nvt.addPerParticleParameter("y0")
pos_restraint_nvt.addPerParticleParameter("z0")

k_10 = 10.0 * omm_unit.kilocalories_per_mole / omm_unit.angstroms**2
for atom in top.atoms:
    if atom.name == 'CA':
        pos_restraint_nvt.addParticle(atom.idx, [k_10,
            positions[atom.idx][0], positions[atom.idx][1], positions[atom.idx][2]])

system.addForce(pos_restraint_nvt)

minimized_positions = simulation.context.getState(getPositions=True).getPositions()

nvt_steps = int(NVT_EQUIL_PS * 1000 / TIMESTEP_FS)
heating_steps = nvt_steps // 4

print(f"  Running NVT for {nvt_steps} steps ({NVT_EQUIL_PS} ps)...")
print(f"  Gradual heating: 100K -> 300K over {heating_steps} steps")

integrator_heat = mm.LangevinMiddleIntegrator(
    100.0 * omm_unit.kelvin,
    1.0 / omm_unit.picosecond,
    TIMESTEP_FS * omm_unit.femtoseconds,
)

simulation_nvt = app.Simulation(openmm_top, system, integrator_heat)
simulation_nvt.context.setPositions(minimized_positions)
simulation_nvt.context.setVelocitiesToTemperature(100.0 * omm_unit.kelvin)

print("  Phase 2a: Heating 100K -> 300K...")
simulation_nvt.reporters.append(app.StateDataReporter(
    sys.stdout, 500, step=True, time=True, potentialEnergy=True, 
    temperature=True, speed=True, separator='\t'))

t0 = time.time()
for i in range(4):
    temp = 100.0 + (i + 1) * 50.0
    if temp > TEMPERATURE_K:
        temp = TEMPERATURE_K
    integrator_heat.setTemperature(temp * omm_unit.kelvin)
    print(f"    Heating to {temp:.0f}K...")
    simulation_nvt.step(heating_steps // 4)
t1 = time.time()
print(f"  Heating complete in {t1-t0:.1f}s")

print("  Phase 2b: Equilibration at 300K...")
integrator_heat.setTemperature(TEMPERATURE_K * omm_unit.kelvin)
simulation_nvt.step(nvt_steps - heating_steps)
t2 = time.time()
print(f"  NVT equilibration complete in {t2-t1:.1f}s")

state = simulation_nvt.context.getState(getEnergy=True)
print(f"  Final potential energy: {state.getPotentialEnergy().value_in_unit(omm_unit.kilojoules_per_mole):.1f} kJ/mol")

simulation_nvt.saveCheckpoint(os.path.join(OUTPUT_DIR, 'nvt_checkpoint.chk'))
print("  Saved: nvt_checkpoint.chk")

# Remove NVT restraints
system.removeForce(system.getNumForces() - 1)

# ========== 3. NPT EQUILIBRATION ==========
print("\n" + "=" * 60)
print(f"PHASE 3: NPT Equilibration ({NPT_EQUIL_PS} ps)")
print("=" * 60)

# Add barostat
barostat = mm.MonteCarloBarostat(PRESSURE_ATM * omm_unit.atmosphere, TEMPERATURE_K * omm_unit.kelvin, 25)
system.addForce(barostat)

# Add weaker restraints (5 kcal/mol/A^2) for NPT
pos_restraint_npt = mm.CustomExternalForce("k * ((x-x0)^2 + (y-y0)^2 + (z-z0)^2)")
pos_restraint_npt.addPerParticleParameter("k")
pos_restraint_npt.addPerParticleParameter("x0")
pos_restraint_npt.addPerParticleParameter("y0")
pos_restraint_npt.addPerParticleParameter("z0")

k_5 = 5.0 * omm_unit.kilocalories_per_mole / omm_unit.angstroms**2
for atom in top.atoms:
    if atom.name == 'CA':
        pos_restraint_npt.addParticle(atom.idx, [k_5,
            positions[atom.idx][0], positions[atom.idx][1], positions[atom.idx][2]])

system.addForce(pos_restraint_npt)

integrator_npt = mm.LangevinMiddleIntegrator(
    TEMPERATURE_K * omm_unit.kelvin,
    1.0 / omm_unit.picosecond,
    TIMESTEP_FS * omm_unit.femtoseconds,
)

simulation_npt = app.Simulation(openmm_top, system, integrator_npt)
simulation_npt.context.setPositions(minimized_positions)
simulation_npt.context.setVelocitiesToTemperature(TEMPERATURE_K * omm_unit.kelvin)

npt_steps = int(NPT_EQUIL_PS * 1000 / TIMESTEP_FS)
print(f"  Running NPT for {npt_steps} steps ({NPT_EQUIL_PS} ps)...")

simulation_npt.reporters.append(app.StateDataReporter(
    sys.stdout, 500, step=True, time=True, potentialEnergy=True, 
    temperature=True, density=True, speed=True, separator='\t'))

t0 = time.time()
simulation_npt.step(npt_steps)
t1 = time.time()
print(f"  NPT complete in {t1-t0:.1f}s")

state = simulation_npt.context.getState(getEnergy=True)
print(f"  Final potential energy: {state.getPotentialEnergy().value_in_unit(omm_unit.kilojoules_per_mole):.1f} kJ/mol")

# Get box volume
state_box = simulation_npt.context.getState(getPositions=True)
box_vectors = state_box.getPeriodicBoxVectors()
volume = box_vectors[0][0] * box_vectors[1][1] * box_vectors[2][2]
print(f"  Box volume: {volume.value_in_unit(omm_unit.nanometers**3):.1f} nm^3")
print(f"  Density: ~{volume.value_in_unit(omm_unit.nanometers**3):.1f} nm^3")

simulation_npt.saveCheckpoint(os.path.join(OUTPUT_DIR, 'npt_checkpoint.chk'))
print("  Saved: npt_checkpoint.chk")

# Remove NPT restraints and barostat
system.removeForce(system.getNumForces() - 1)  # restraints
system.removeForce(system.getNumForces() - 1)  # barostat

# ========== 4. PRODUCTION MD ==========
print("\n" + "=" * 60)
print(f"PHASE 4: Production MD ({DURATION_NS} ns)")
print("=" * 60)

# Add barostat back for NPT production
barostat_prod = mm.MonteCarloBarostat(PRESSURE_ATM * omm_unit.atmosphere, TEMPERATURE_K * omm_unit.kelvin, 25)
system.addForce(barostat_prod)

integrator_prod = mm.LangevinMiddleIntegrator(
    TEMPERATURE_K * omm_unit.kelvin,
    1.0 / omm_unit.picosecond,
    TIMESTEP_FS * omm_unit.femtoseconds,
)

simulation_prod = app.Simulation(openmm_top, system, integrator_prod)
simulation_prod.context.setPositions(minimized_positions)
simulation_prod.context.setVelocitiesToTemperature(TEMPERATURE_K * omm_unit.kelvin)

# Trajectory file
traj_file = os.path.join(OUTPUT_DIR, 'production.nc')
save_steps = int(SAVE_INTERVAL_PS * 1000 / TIMESTEP_FS)
total_steps = int(DURATION_NS * 1000 * 1000 / TIMESTEP_FS)
n_frames = int(DURATION_NS * 1000 / SAVE_INTERVAL_PS)

print(f"  Total steps: {total_steps}")
print(f"  Save interval: {save_steps} steps ({SAVE_INTERVAL_PS} ps)")
print(f"  Expected frames: {n_frames}")

# NetCDF trajectory reporter
simulation_prod.reporters.append(NetCDFReporter(
    traj_file, save_steps, crds=True, vels=False, frcs=False))

# State data reporter
simulation_prod.reporters.append(app.StateDataReporter(
    sys.stdout, save_steps, step=True, time=True, potentialEnergy=True,
    kineticEnergy=True, totalEnergy=True, temperature=True, volume=True,
    density=True, progress=True, remainingTime=True, speed=True,
    totalSteps=total_steps, separator='\t'))

# Checkpoint reporter
simulation_prod.reporters.append(app.CheckpointReporter(
    os.path.join(OUTPUT_DIR, 'production_checkpoint.chk'), save_steps))

t0 = time.time()
simulation_prod.step(total_steps)
t1 = time.time()
prod_time = t1 - t0
print(f"  Production complete in {prod_time:.1f}s ({prod_time/60:.1f} min)")
print(f"  Performance: {DURATION_NS/prod_time*3600:.2f} ns/hour")

# Save final structure
state = simulation_prod.context.getState(getPositions=True, enforcePeriodicBox=True)
with open(os.path.join(OUTPUT_DIR, 'final.pdb'), 'w') as f:
    app.PDBFile.writeFile(openmm_top, state.getPositions(), f, keepIds=True)
print("  Saved: final.pdb")

# ========== 5. TRAJECTORY ANALYSIS ==========
print("\n" + "=" * 60)
print("PHASE 5: Trajectory Analysis")
print("=" * 60)

try:
    import mdtraj as md
    
    # Load trajectory
    print("  Loading trajectory...")
    traj = md.load(traj_file, top=PRMTOP)
    print(f"  Loaded {traj.n_frames} frames, {traj.n_atoms} atoms")
    
    # Select protein CA atoms
    ca_indices = traj.topology.select("name CA")
    print(f"  Protein CA atoms: {len(ca_indices)}")
    
    # Select ligand heavy atoms
    lig_indices = traj.topology.select("resname LIG and not element H")
    print(f"  Ligand heavy atoms: {len(lig_indices)}")
    
    # 1. RMSD of protein CA
    ca_rmsd = md.rmsd(traj, traj, 0, atom_indices=ca_indices)
    ca_rmsd_mean = float(np.mean(ca_rmsd))
    ca_rmsd_std = float(np.std(ca_rmsd))
    ca_rmsd_final = float(ca_rmsd[-1])
    print(f"\n  Protein CA RMSD: {ca_rmsd_mean:.3f} +/- {ca_rmsd_std:.3f} nm (final: {ca_rmsd_final:.3f} nm)")
    
    # 2. Ligand RMSD
    if len(lig_indices) > 0:
        lig_rmsd = md.rmsd(traj, traj, 0, atom_indices=lig_indices)
        lig_rmsd_mean = float(np.mean(lig_rmsd))
        lig_rmsd_std = float(np.std(lig_rmsd))
        lig_rmsd_final = float(lig_rmsd[-1])
        print(f"  Ligand heavy atom RMSD: {lig_rmsd_mean:.3f} +/- {lig_rmsd_std:.3f} nm (final: {lig_rmsd_final:.3f} nm)")
    else:
        lig_rmsd = None
        print("  No ligand heavy atoms found for RMSD calculation")
    
    # 3. RMSF (CA atoms)
    ca_rmsf = md.rmsf(traj, traj, 0, atom_indices=ca_indices)
    ca_residues = [traj.topology.atom(i).residue for i in ca_indices]
    ca_resnames = [f"{r.name}{r.resSeq}" for r in ca_residues]
    top10_idx = np.argsort(ca_rmsf)[-10:][::-1]
    print(f"\n  Top 10 flexible residues (CA RMSF):")
    top10_list = []
    for idx in top10_idx:
        print(f"    {ca_resnames[idx]}: {ca_rmsf[idx]:.4f} nm")
        top10_list.append((ca_resnames[idx], float(ca_rmsf[idx])))
    
    # 4. Radius of gyration
    rg = md.compute_rg(traj)
    rg_mean = float(np.mean(rg))
    rg_std = float(np.std(rg))
    print(f"\n  Radius of gyration: {rg_mean:.3f} +/- {rg_std:.3f} nm")
    
    # 5. Hydrogen bond analysis (protein-ligand)
    print(f"\n  Protein-Ligand Hydrogen Bonds:")
    hbonds = md.baker_hubbard(traj, freq=0.0, exclude_water=True)
    
    pl_hbonds = []
    for hbond in hbonds:
        d_idx, h_idx, a_idx = hbond
        d_res = traj.topology.atom(d_idx).residue
        a_res = traj.topology.atom(a_idx).residue
        if (d_res.name == 'LIG' and a_res.name != 'LIG') or \
           (a_res.name == 'LIG' and d_res.name != 'LIG'):
            pl_hbonds.append(hbond)
    
    if len(pl_hbonds) > 0:
        print(f"    Found {len(pl_hbonds)} protein-ligand H-bonds")
        hbond_data = []
        for hbond in pl_hbonds:
            d_idx, h_idx, a_idx = hbond
            d_atom = traj.topology.atom(d_idx)
            a_atom = traj.topology.atom(a_idx)
            print(f"    {d_atom.residue.name}{d_atom.residue.resSeq}:{d_atom.name} - "
                  f"{a_atom.residue.name}{a_atom.residue.resSeq}:{a_atom.name}")
        
        print(f"\n    H-bond occupancy (distance < 3.5A):")
        for hbond in pl_hbonds:
            d_idx, h_idx, a_idx = hbond
            count = 0
            for frame in range(traj.n_frames):
                d = np.linalg.norm(traj.xyz[frame, d_idx] - traj.xyz[frame, a_idx])
                if d < 0.35:
                    count += 1
            occupancy = count / traj.n_frames * 100
            d_atom = traj.topology.atom(d_idx)
            a_atom = traj.topology.atom(a_idx)
            print(f"      {d_atom.residue.name}{d_atom.residue.resSeq}:{d_atom.name} - "
                  f"{a_atom.residue.name}{a_atom.residue.resSeq}:{a_atom.name}: "
                  f"{occupancy:.1f}%")
            hbond_data.append({
                'donor': f"{d_atom.residue.name}{d_atom.residue.resSeq}:{d_atom.name}",
                'acceptor': f"{a_atom.residue.name}{a_atom.residue.resSeq}:{a_atom.name}",
                'occupancy': occupancy
            })
    else:
        print("    No persistent protein-ligand H-bonds found")
        hbond_data = []
    
    # Save analysis results
    results = {
        'ca_rmsd_mean': ca_rmsd_mean,
        'ca_rmsd_std': ca_rmsd_std,
        'ca_rmsd_final': ca_rmsd_final,
        'lig_rmsd_mean': lig_rmsd_mean if lig_rmsd is not None else None,
        'lig_rmsd_std': lig_rmsd_std if lig_rmsd is not None else None,
        'lig_rmsd_final': lig_rmsd_final if lig_rmsd is not None else None,
        'rg_mean': rg_mean,
        'rg_std': rg_std,
        'n_frames': traj.n_frames,
        'prod_time_seconds': prod_time,
        'top10_flexible': top10_list,
        'hbonds': hbond_data,
    }
    
    import json
    with open(os.path.join(OUTPUT_DIR, 'analysis_results.json'), 'w') as f:
        json.dump(results, f, indent=2)
    print("\n  Analysis results saved to analysis_results.json")
    
except ImportError:
    print("  mdtraj not available, skipping trajectory analysis")
except Exception as e:
    print(f"  Analysis error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("MD SIMULATION COMPLETE")
print("=" * 60)
print(f"Output directory: {OUTPUT_DIR}")
print(f"Trajectory: {traj_file}")
