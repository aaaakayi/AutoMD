#!/usr/bin/env python3
"""
自定义MD模拟脚本：直接使用AMBER prmtop/inpcrd拓扑文件运行MD模拟
使用OpenMM GPU加速
模拟时长：1ns（快速验证）
"""

import os
import sys
import time
import numpy as np

print("="*60)
print("1IEP-配体复合物 MD模拟 (1ns, GPU加速)")
print("="*60)

from openmm import *
from openmm.app import *
from openmm.unit import *

# ============================================================
# 步骤1: 加载AMBER拓扑和坐标
# ============================================================
print("\n步骤1: 加载AMBER拓扑和坐标文件")
print("-"*40)

prmtop_file = "../md/complex_with_lig.prmtop"
inpcrd_file = "../md/complex_with_lig.inpcrd"

print(f"拓扑文件: {prmtop_file}")
print(f"坐标文件: {inpcrd_file}")

prmtop = AmberPrmtopFile(prmtop_file)
inpcrd = AmberInpcrdFile(inpcrd_file)

print(f"总原子数: {prmtop.topology.getNumAtoms()}")
print(f"总残基数: {prmtop.topology.getNumResidues()}")

# 统计各组分
num_protein = 0
num_lig = 0
num_water = 0
num_ions = 0
lig_atom_count = 0
for res in prmtop.topology.residues():
    if res.name == 'LIG':
        num_lig += 1
        lig_atom_count = len(list(res.atoms()))
    elif res.name in ['WAT', 'HOH']:
        num_water += 1
    elif res.name in ['Na+', 'Cl-']:
        num_ions += 1
    else:
        num_protein += 1

print(f"蛋白残基数: {num_protein}")
print(f"配体原子数: {lig_atom_count}")
print(f"水分子数: {num_water}")
print(f"离子数: {num_ions}")

# ============================================================
# 步骤2: 创建系统
# ============================================================
print("\n步骤2: 创建系统")
print("-"*40)

# 直接从prmtop创建系统 - 保留AMBER力场参数
system = prmtop.createSystem(
    nonbondedMethod=PME,
    nonbondedCutoff=1.0*nanometer,
    constraints=HBonds,
    rigidWater=True,
    ewaldErrorTolerance=0.0005
)

print("系统创建成功")

# ============================================================
# 步骤3: 设置平台（GPU加速）
# ============================================================
print("\n步骤3: 设置计算平台")
print("-"*40)

# 检查可用平台
platforms = [Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]
print(f"可用平台: {platforms}")

if 'CUDA' in platforms:
    platform = Platform.getPlatformByName('CUDA')
    properties = {'DeviceIndex': '0', 'Precision': 'mixed'}
    print("使用CUDA平台 (GPU加速)")
elif 'OpenCL' in platforms:
    platform = Platform.getPlatformByName('OpenCL')
    properties = {}
    print("使用OpenCL平台")
else:
    platform = Platform.getPlatformByName('CPU')
    properties = {}
    print("使用CPU平台 (无GPU加速)")

# ============================================================
# 步骤4: 能量最小化
# ============================================================
print("\n步骤4: 能量最小化")
print("-"*40)

integrator_min = LangevinMiddleIntegrator(
    300*kelvin, 1.0/picosecond, 0.002*picosecond
)

simulation = Simulation(prmtop.topology, system, integrator_min, platform, properties)
simulation.context.setPositions(inpcrd.positions)

if inpcrd.boxVectors is not None:
    simulation.context.setPeriodicBoxVectors(*inpcrd.boxVectors)

print("运行能量最小化 (最多5000步)...")
start_time = time.time()
simulation.minimizeEnergy(maxIterations=5000)
min_time = time.time() - start_time
print(f"能量最小化完成，耗时: {min_time:.1f}秒")

# 保存最小化结构
min_pos = simulation.context.getState(getPositions=True).getPositions()
with open('minimized.pdb', 'w') as f:
    PDBFile.writeFile(simulation.topology, min_pos, f)
print("最小化结构已保存到: minimized.pdb")

# ============================================================
# 步骤5: NVT平衡 (100ps)
# ============================================================
print("\n步骤5: NVT平衡 (100ps)")
print("-"*40)

# 添加位置约束 - 约束蛋白重原子
print("添加位置约束 (蛋白重原子, 10 kcal/mol/A^2)...")
force = CustomExternalForce('k*periodicdistance(x, y, z, x0, y0, z0)^2')
force.addGlobalParameter('k', 10.0*kilocalorie_per_mole/angstrom**2)
force.addPerParticleParameter('x0')
force.addPerParticleParameter('y0')
force.addPerParticleParameter('z0')

# 获取蛋白重原子索引
n_restrained = 0
for atom in prmtop.topology.atoms():
    if atom.residue.name != 'LIG' and atom.element.symbol != 'H':
        pos = min_pos[atom.index]
        force.addParticle(atom.index, [pos[0], pos[1], pos[2]])
        n_restrained += 1

system.addForce(force)
print(f"约束 {n_restrained} 个蛋白重原子")

# 创建新的integrator和simulation（因为system已修改）
integrator_nvt = LangevinMiddleIntegrator(
    300*kelvin, 1.0/picosecond, 0.002*picosecond
)

simulation = Simulation(prmtop.topology, system, integrator_nvt, platform, properties)
simulation.context.setPositions(min_pos)
if inpcrd.boxVectors is not None:
    simulation.context.setPeriodicBoxVectors(*inpcrd.boxVectors)
simulation.context.setVelocitiesToTemperature(300*kelvin)

# NVT平衡
nvt_steps = 50000  # 100ps @ 2fs
print(f"运行NVT平衡 ({nvt_steps}步, 100ps)...")
start_time = time.time()
simulation.step(nvt_steps)
nvt_time = time.time() - start_time
print(f"NVT平衡完成，耗时: {nvt_time:.1f}秒")

# 保存NVT结构
nvt_pos = simulation.context.getState(getPositions=True).getPositions()
with open('nvt_equilibrated.pdb', 'w') as f:
    PDBFile.writeFile(simulation.topology, nvt_pos, f)
print("NVT平衡结构已保存到: nvt_equilibrated.pdb")

# ============================================================
# 步骤6: NPT平衡 (100ps)
# ============================================================
print("\n步骤6: NPT平衡 (100ps)")
print("-"*40)

# 移除位置约束，添加压力控制
# 重新创建系统（无约束）
print("重新创建系统 (移除约束，添加压力控制)...")
system_npt = prmtop.createSystem(
    nonbondedMethod=PME,
    nonbondedCutoff=1.0*nanometer,
    constraints=HBonds,
    rigidWater=True,
    ewaldErrorTolerance=0.0005
)

# 添加MonteCarloBarostat
system_npt.addForce(MonteCarloBarostat(1*bar, 300*kelvin, 25))

# 创建新的模拟
integrator_npt = LangevinMiddleIntegrator(
    300*kelvin, 1.0/picosecond, 0.002*picosecond
)

simulation_npt = Simulation(prmtop.topology, system_npt, integrator_npt, platform, properties)
simulation_npt.context.setPositions(nvt_pos)
if inpcrd.boxVectors is not None:
    simulation_npt.context.setPeriodicBoxVectors(*inpcrd.boxVectors)
simulation_npt.context.setVelocitiesToTemperature(300*kelvin)

# NPT平衡
npt_steps = 50000  # 100ps @ 2fs
print(f"运行NPT平衡 ({npt_steps}步, 100ps)...")
start_time = time.time()
simulation_npt.step(npt_steps)
npt_time = time.time() - start_time
print(f"NPT平衡完成，耗时: {npt_time:.1f}秒")

# 保存NPT结构
npt_pos = simulation_npt.context.getState(getPositions=True).getPositions()
with open('npt_equilibrated.pdb', 'w') as f:
    PDBFile.writeFile(simulation_npt.topology, npt_pos, f)
print("NPT平衡结构已保存到: npt_equilibrated.pdb")

# ============================================================
# 步骤7: 生产MD模拟 (1ns)
# ============================================================
print("\n步骤7: 生产MD模拟 (1ns, GPU加速)")
print("-"*40)

# 设置报告器
print("设置输出报告器...")
# 每50000步(100ps)保存一次轨迹 -> 1ns = 10帧
simulation_npt.reporters.append(DCDReporter('trajectory.dcd', 50000, append=False))
# 每500步(1ps)输出一次能量信息
simulation_npt.reporters.append(StateDataReporter(
    'md_output.log', 500, 
    step=True, time=True, potentialEnergy=True, kineticEnergy=True,
    totalEnergy=True, temperature=True, volume=True, density=True,
    speed=True, separator='\t'
))
# 每250000步(500ps)保存一次检查点
simulation_npt.reporters.append(CheckpointReporter('checkpoint.chk', 250000))

# 运行生产模拟
prod_steps = 500000  # 1ns @ 2fs
print(f"开始1ns生产MD模拟...")
print(f"  时间步长: 2fs")
print(f"  总步数: {prod_steps}")
print(f"  轨迹保存间隔: 100ps (每50000步)")
print(f"  预计帧数: 10帧")
print(f"  使用GPU: CUDA")

start_time = time.time()
simulation_npt.step(prod_steps)
prod_time = time.time() - start_time

print(f"\n1ns MD模拟完成!")
print(f"  生产模拟耗时: {prod_time:.1f}秒 ({prod_time/60:.1f}分钟)")
print(f"  模拟性能: {1.0/(prod_time/3600):.1f} ns/天")

# 保存最终结构
final_pos = simulation_npt.context.getState(getPositions=True).getPositions()
with open('final_structure.pdb', 'w') as f:
    PDBFile.writeFile(simulation_npt.topology, final_pos, f)
print("最终结构已保存到: final_structure.pdb")

# ============================================================
# 步骤8: 输出模拟摘要
# ============================================================
print("\n" + "="*60)
print("MD模拟摘要")
print("="*60)
print(f"蛋白: 1IEP")
print(f"配体: Cc1ccc(cc1Nc2nccc(n2)c3cccnc3)NC(=O)c4ccc(cc4)CN5CCN(CC5)C")
print(f"力场: AMBER (来自prmtop)")
print(f"模拟时间: 1ns")
print(f"时间步长: 2fs")
print(f"温度: 300K (Langevin Middle Integrator)")
print(f"压力: 1bar (MonteCarlo Barostat)")
print(f"非键方法: PME")
print(f"截断距离: 1.0nm")
print(f"约束: 氢键约束 (SHAKE)")
print(f"GPU: CUDA")
print(f"总原子数: {prmtop.topology.getNumAtoms()}")
print(f"各阶段耗时:")
print(f"  能量最小化: {min_time:.1f}秒")
print(f"  NVT平衡:     {nvt_time:.1f}秒")
print(f"  NPT平衡:     {npt_time:.1f}秒")
print(f"  生产模拟:    {prod_time:.1f}秒")
print(f"  总耗时:      {min_time+nvt_time+npt_time+prod_time:.1f}秒")
print("="*60)
