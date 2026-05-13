#!/usr/bin/env python3
"""
完整的MD模拟脚本：对1IEP蛋白+配体复合物进行10ns分子动力学模拟
使用OpenMM GPU加速
"""

import os
import sys
import numpy as np

# ============================================================
# 步骤1: 从对接结果中提取最佳pose的配体坐标
# ============================================================
print("="*60)
print("步骤1: 提取最佳对接pose的配体坐标")
print("="*60)

# 读取docked.pdb，提取第一个MODEL的配体部分
docked_pdb = "../docking/docked.pdb"
ligand_pdb = "ligand_best_pose.pdb"

with open(docked_pdb, 'r') as f:
    lines = f.readlines()

# 找到第一个MODEL的配体部分（HETATM或ATOM记录，残基名LIG）
in_model1 = False
ligand_atoms = []
for line in lines:
    if line.startswith("MODEL") and "1" in line:
        in_model1 = True
        continue
    if line.startswith("ENDMDL") and in_model1:
        break
    if in_model1:
        if line.startswith("HETATM") or line.startswith("ATOM"):
            resname = line[17:20].strip()
            if resname == "LIG":
                ligand_atoms.append(line)

print(f"提取到 {len(ligand_atoms)} 个配体原子")

# 写入配体PDB
with open(ligand_pdb, 'w') as f:
    f.write("REMARK Ligand from docking best pose\n")
    for atom in ligand_atoms:
        f.write(atom)
    f.write("END\n")

print(f"配体坐标已保存到: {ligand_pdb}")

# ============================================================
# 步骤2: 使用OpenMM构建系统并运行MD
# ============================================================
print("\n" + "="*60)
print("步骤2: 使用OpenMM构建系统并运行MD模拟")
print("="*60)

from openmm import *
from openmm.app import *
from openmm.unit import *

# 加载蛋白
print("加载蛋白结构...")
protein_pdb = "../prepared/1IEP_protein_only.pdb"
pdb = PDBFile(protein_pdb)

# 加载配体
print("加载配体结构...")
lig_pdb_file = PDBFile(ligand_pdb)

# 合并蛋白和配体的拓扑和坐标
print("合并蛋白和配体...")
from openmm.app import Topology
import copy

# 获取蛋白拓扑和坐标
protein_top = pdb.topology
protein_pos = pdb.positions

# 获取配体拓扑和坐标
lig_top = lig_pdb_file.topology
lig_pos = lig_pdb_file.positions

# 合并拓扑
combined_top = Topology()
combined_pos = []

# 添加蛋白链
protein_chain = list(protein_top.chains())
protein_residues = []
for chain in protein_chain:
    for res in chain.residues():
        protein_residues.append(res)

# 创建新的拓扑
# 先复制蛋白
for chain in protein_top.chains():
    new_chain = combined_top.addChain(chain.id)
    for res in chain.residues():
        new_res = combined_top.addResidue(res.name, new_chain, res.id)
        for atom in res.atoms():
            combined_top.addAtom(atom.name, atom.element, new_res, atom.id)

# 添加配体作为新链
lig_chain = list(lig_top.chains())
new_lig_chain = combined_top.addChain('L')
for res in lig_chain[0].residues():
    new_res = combined_top.addResidue('LIG', new_lig_chain, res.id)
    for atom in res.atoms():
        combined_top.addAtom(atom.name, atom.element, new_res, atom.id)

# 合并坐标
combined_pos = list(protein_pos) + list(lig_pos)

print(f"复合物总原子数: {len(combined_pos)}")
print(f"蛋白残基数: {len(protein_residues)}")
print(f"配体原子数: {len(list(lig_top.atoms()))}")

# ============================================================
# 步骤3: 力场设置和系统构建
# ============================================================
print("\n" + "="*60)
print("步骤3: 设置力场和构建系统")
print("="*60)

# 使用ff19SB蛋白力场 + GAFF配体力场
print("加载力场...")
forcefield = ForceField('amber19sb.xml', 'gaff.xml', 'tip3p.xml')

# 创建系统
print("创建系统...")
system = forcefield.createSystem(
    combined_top, 
    nonbondedMethod=PME,
    nonbondedCutoff=1.0*nanometer,
    constraints=HBonds,
    rigidWater=True,
    ewaldErrorTolerance=0.0005
)

# ============================================================
# 步骤4: 溶剂化和离子中和
# ============================================================
print("\n" + "="*60)
print("步骤4: 溶剂化和离子中和")
print("="*60)

# 使用Modeller添加溶剂
print("添加溶剂盒子...")
modeller = Modeller(combined_top, combined_pos)
modeller.addSolvent(
    forcefield, 
    model='tip3p',
    padding=1.0*nanometer,
    ionicStrength=0.15*molar,
    neutralize=True
)

print(f"溶剂化后总原子数: {modeller.getNumAtoms()}")

# 重新创建系统（包含溶剂）
print("创建溶剂化系统...")
system = forcefield.createSystem(
    modeller.topology,
    nonbondedMethod=PME,
    nonbondedCutoff=1.0*nanometer,
    constraints=HBonds,
    rigidWater=True,
    ewaldErrorTolerance=0.0005
)

# ============================================================
# 步骤5: 能量最小化
# ============================================================
print("\n" + "="*60)
print("步骤5: 能量最小化")
print("="*60)

# 设置平台
platform = Platform.getPlatformByName('CUDA')
properties = {'DeviceIndex': '0', 'Precision': 'mixed'}

# 创建模拟对象
integrator = LangevinMiddleIntegrator(
    300*kelvin, 1.0/picosecond, 0.002*picosecond
)
simulation = Simulation(modeller.topology, system, integrator, platform, properties)
simulation.context.setPositions(modeller.positions)

# 能量最小化
print("运行能量最小化...")
simulation.minimizeEnergy(maxIterations=5000)
minimized_pos = simulation.context.getState(getPositions=True).getPositions()

# 保存最小化后的结构
print("保存最小化结构...")
with open('minimized.pdb', 'w') as f:
    PDBFile.writeFile(simulation.topology, minimized_pos, f)

print("能量最小化完成")

# ============================================================
# 步骤6: NVT平衡
# ============================================================
print("\n" + "="*60)
print("步骤6: NVT平衡 (100ps)")
print("="*60)

# 设置位置约束（先约束蛋白重原子）
print("设置位置约束...")
# 对蛋白重原子施加约束
force = CustomExternalForce('k*periodicdistance(x, y, z, x0, y0, z0)^2')
force.addGlobalParameter('k', 10.0*kilocalorie_per_mole/angstrom**2)
force.addPerParticleParameter('x0')
force.addPerParticleParameter('y0')
force.addPerParticleParameter('z0')

# 获取蛋白原子索引
protein_atoms = []
for atom in modeller.topology.atoms():
    if atom.residue.chain.id != 'L':
        protein_atoms.append(atom.index)

print(f"约束 {len(protein_atoms)} 个蛋白原子")

# 添加约束
for idx in protein_atoms:
    pos = minimized_pos[idx]
    force.addParticle(idx, [pos[0], pos[1], pos[2]])

system.addForce(force)

# 重新创建模拟（因为system已修改）
simulation = Simulation(modeller.topology, system, integrator, platform, properties)
simulation.context.setPositions(minimized_pos)

# 设置初始速度
simulation.context.setVelocitiesToTemperature(300*kelvin)

# NVT平衡
print("运行NVT平衡 (100ps)...")
simulation.step(50000)  # 100ps @ 2fs

# 保存NVT平衡后的结构
nvt_pos = simulation.context.getState(getPositions=True).getPositions()
with open('nvt_equilibrated.pdb', 'w') as f:
    PDBFile.writeFile(simulation.topology, nvt_pos, f)

print("NVT平衡完成")

# ============================================================
# 步骤7: NPT平衡 (200ps)
# ============================================================
print("\n" + "="*60)
print("步骤7: NPT平衡 (200ps)")
print("="*60)

# 移除位置约束，添加压力控制
# 重新创建系统（无约束）
system_npt = forcefield.createSystem(
    modeller.topology,
    nonbondedMethod=PME,
    nonbondedCutoff=1.0*nanometer,
    constraints=HBonds,
    rigidWater=True,
    ewaldErrorTolerance=0.0005
)

# 添加压力控制
system_npt.addForce(MonteCarloBarostat(1*bar, 300*kelvin, 25))

# 创建新的模拟
integrator_npt = LangevinMiddleIntegrator(
    300*kelvin, 1.0/picosecond, 0.002*picosecond
)
simulation_npt = Simulation(modeller.topology, system_npt, integrator_npt, platform, properties)
simulation_npt.context.setPositions(nvt_pos)
simulation_npt.context.setVelocitiesToTemperature(300*kelvin)

# NPT平衡
print("运行NPT平衡 (200ps)...")
simulation_npt.step(100000)  # 200ps @ 2fs

# 保存NPT平衡后的结构
npt_pos = simulation_npt.context.getState(getPositions=True).getPositions()
with open('npt_equilibrated.pdb', 'w') as f:
    PDBFile.writeFile(simulation_npt.topology, npt_pos, f)

print("NPT平衡完成")

# ============================================================
# 步骤8: 生产MD模拟 (10ns)
# ============================================================
print("\n" + "="*60)
print("步骤8: 生产MD模拟 (10ns, GPU加速)")
print("="*60)

# 设置报告器
print("设置输出报告器...")
# 每500步(1ps)保存一次轨迹
simulation_npt.reporters.append(DCDReporter('trajectory.dcd', 500, append=False))
# 每500步输出一次能量信息
simulation_npt.reporters.append(StateDataReporter(
    'md_output.log', 500, 
    step=True, time=True, potentialEnergy=True, kineticEnergy=True,
    totalEnergy=True, temperature=True, volume=True, density=True,
    speed=True, separator='\t'
))
# 每5000步(10ps)保存一次检查点
simulation_npt.reporters.append(CheckpointReporter('checkpoint.chk', 5000))

# 运行生产模拟
print("开始10ns生产MD模拟...")
print("使用GPU: NVIDIA GeForce RTX 3050")
print("时间步长: 2fs, 总步数: 5,000,000")
print("轨迹保存间隔: 1ps (每500步)")

import time
start_time = time.time()

simulation_npt.step(5000000)  # 10ns @ 2fs

end_time = time.time()
elapsed = end_time - start_time

print(f"\n10ns MD模拟完成!")
print(f"运行时间: {elapsed:.1f}秒 ({elapsed/60:.1f}分钟)")
print(f"模拟性能: {10000/(elapsed/3600):.1f} ns/天")

# 保存最终结构
final_pos = simulation_npt.context.getState(getPositions=True).getPositions()
with open('final_structure.pdb', 'w') as f:
    PDBFile.writeFile(simulation_npt.topology, final_pos, f)

print("\n最终结构已保存到: final_structure.pdb")
print("轨迹已保存到: trajectory.dcd")
print("能量日志已保存到: md_output.log")
print("检查点已保存到: checkpoint.chk")

# ============================================================
# 步骤9: 输出模拟摘要
# ============================================================
print("\n" + "="*60)
print("MD模拟摘要")
print("="*60)
print(f"蛋白: 1IEP (PDB ID)")
print(f"配体: Cc1ccc(cc1Nc2nccc(n2)c3cccnc3)NC(=O)c4ccc(cc4)CN5CCN(CC5)C")
print(f"力场: ff19SB (蛋白) + GAFF (配体) + TIP3P (水)")
print(f"溶剂盒子: 1.0nm padding, TIP3P水模型")
print(f"离子: NaCl 0.15M + 中和")
print(f"模拟时间: 10ns")
print(f"时间步长: 2fs")
print(f"温度: 300K (Langevin Middle Integrator)")
print(f"压力: 1bar (MonteCarlo Barostat)")
print(f"非键方法: PME")
print(f"截断距离: 1.0nm")
print(f"约束: 氢键约束")
print(f"GPU: NVIDIA GeForce RTX 3050 (CUDA)")
print(f"总原子数: {modeller.getNumAtoms()}")
print("="*60)
