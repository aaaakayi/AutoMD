#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Antechamber 路线配体参数化工具

从 SMILES 或 PDB 文件开始，生成 GAFF 力场参数和 AM1-BCC/RESP 电荷(mol2文件)，
并输出对接所需的 PDBQT 文件、MD 所需的 AMBER 拓扑/坐标文件，
以及可选的 GROMACS 格式拓扑。

依赖条件：
- AmberTools (包含 antechamber, parmchk2, tleap, acpype)
- Open Babel (用于mgltools失败下的格式转换)
- RDKit (用于从 SMILES 生成 3D 结构)

参考：
- Antechamber 参数详解: https://blog.csdn.net/lihui261431/article/details/154800102
- tLEaP 使用教程: https://blog.csdn.net/lihui261431/article/details/154793284
- GAFF2 力场参数: http://ambermd.org/antechamber/gaff.html
- MGLTools 脚本: ${MGLTools}/AutoDockTools/Utilities24/prepare_ligand4.py
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Union

from tools.shared import (
    PROJECT_ROOT,
    TEMP_ROOT,
    MGLTOOLS_PCKGS_PATH,
    CONDA_MGLTOOLS_ENV,
    PREPARE_LIGAND4_SCRIPT,
    success,
    degraded,
    failed,
    ToolResult,
)


def _ensure_temp_subdir(*parts: str) -> Path:
    path = TEMP_ROOT.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path

# ============================================================================
# 1. 输入处理：SMILES -> 3D 结构
# ============================================================================

def smiles_to_pdb(smiles: str, output_pdb: str, add_hydrogens: bool = True) -> bool:
    """
    使用 RDKit 将 SMILES 转换为 3D PDB 文件。
    
    Args:
        smiles: SMILES 字符串
        output_pdb: 输出 PDB 文件路径
        add_hydrogens: 是否添加氢原子
    
    Returns:
        成功返回 True，失败返回 False
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        print("错误：需要安装 RDKit (pip install rdkit)")
        return False
    
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        print(f"错误：无效的 SMILES 字符串: {smiles}")
        return False
    
    if add_hydrogens:
        mol = Chem.AddHs(mol)
    
    # 生成 3D 构象
    AllChem.EmbedMolecule(mol, randomSeed=42)
    AllChem.MMFFOptimizeMolecule(mol) # MMFF 构像优化
    
    Chem.MolToPDBFile(mol, output_pdb)
    return True


# ============================================================================
# 2. Antechamber 核心功能
# ============================================================================

def run_antechamber(
    input_file: str,
    output_mol2: str,
    input_format: str = 'pdb',
    charge_method: str = 'bcc', # 可选，默认 AM1-BCC
    net_charge: int = 0,
    residue_name: str = 'LIG',
    force_field: str = 'gaff2',
    extra_args: Optional[List[str]] = None,
    intermediate_dir: Optional[str] = None,
    **kwargs
) -> Tuple[bool, str]:
    """
    运行 antechamber 生成带有 GAFF 原子类型和电荷的 MOL2 文件。
    
    Args:
        input_file: 输入文件路径（PDB 或 MOL2）
        output_mol2: 输出 MOL2 文件路径
        input_format: 输入格式 ('pdb', 'mol2')
        charge_method: 电荷计算方法 ('bcc', 'gas', 'resp')
        net_charge: 分子净电荷
        residue_name: 残基名称（3-4 字符）
        force_field: 力场类型 ('gaff', 'gaff2')
        extra_args: 额外命令行参数列表，例如 ['-j', '4', '-m', '1']
        intermediate_dir: antechamber 中间文件输出目录（如 sqm.in/sqm.out）
        **kwargs: 其他键值对参数，将转换为 '-key value' 形式
    
    Returns:
        (成功标志, 错误信息或空字符串)
    """
    if shutil.which('antechamber') is None:
        return False, "未找到 antechamber 命令，请安装 AmberTools"

    input_abs = os.path.abspath(input_file)
    output_abs = os.path.abspath(output_mol2)

    run_cwd = None
    if intermediate_dir:
        run_cwd = os.path.abspath(intermediate_dir)
        os.makedirs(run_cwd, exist_ok=True)
    else:
        run_cwd = str(_ensure_temp_subdir('antechamber', 'default'))
    
    cmd = [
        'antechamber',
        '-i', input_abs,
        '-fi', input_format,
        '-o', output_abs,
        '-fo', 'mol2',
        '-c', charge_method,
        '-nc', str(net_charge),
        '-rn', residue_name,
        '-at', force_field
    ]
    
    # 处理额外参数列表
    if extra_args:
        cmd.extend(extra_args)
    
    # 处理关键字参数（转换为命令行参数）
    for key, value in kwargs.items():
        cmd.append(f'-{key}')
        if value is not None:
            cmd.append(str(value))
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=run_cwd)
    if result.returncode != 0:
        return False, result.stderr or result.stdout
    return True, ""

def run_parmchk2(
    mol2_file: str,
    output_frcmod: str,
    force_field: str = 'gaff2',
    work_dir: Optional[str] = None,
) -> bool:
    """
    运行 parmchk2 生成 frcmod 文件，补充 GAFF 中缺失的参数。
    
    Args:
        mol2_file: antechamber 生成的 MOL2 文件
        output_frcmod: 输出 frcmod 文件路径
        force_field: 力场类型 ('gaff', 'gaff2')
    
    Returns:
        成功返回 True，失败返回 False
    """
    if shutil.which('parmchk2') is None:
        print("错误：未找到 parmchk2 命令，请安装 AmberTools")
        return False
    
    cmd = [
        'parmchk2',
        '-i', mol2_file,
        '-f', 'mol2',
        '-o', output_frcmod,
        '-s', force_field
    ]

    run_cwd = os.path.abspath(work_dir) if work_dir else str(_ensure_temp_subdir('parmchk2'))
    os.makedirs(run_cwd, exist_ok=True)
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=run_cwd)
    if result.returncode != 0:
        print(f"parmchk2 执行失败:\n{result.stderr}")
        return False
    
    # 检查 frcmod 文件是否包含缺失参数
    with open(output_frcmod, 'r') as f:
        content = f.read()
        if 'ATTN, need revision' in content or 'MISSING' in content:
            print(f"警告：frcmod 文件包含缺失参数，可能需要手动修正:\n{output_frcmod}")
    
    return True


# ============================================================================
# 3. tLEaP 生成 AMBER 拓扑/坐标文件
# ============================================================================

def _deduplicate_mol2_bonds(input_mol2: str, output_mol2: str) -> bool:
    """去重 MOL2 的 BOND 记录，避免 tleap 因重复键连而崩溃。"""
    try:
        with open(input_mol2, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        bond_start = None
        bond_end = None
        molecule_tag = None
        counts_line = None

        for i, line in enumerate(lines):
            tag = line.strip().upper()
            if tag == '@<TRIPOS>MOLECULE':
                molecule_tag = i
                counts_line = i + 2
            elif tag == '@<TRIPOS>BOND':
                bond_start = i + 1
            elif bond_start is not None and line.startswith('@<TRIPOS>') and i > bond_start:
                bond_end = i
                break

        if bond_start is None:
            shutil.copyfile(input_mol2, output_mol2)
            return True

        if bond_end is None:
            bond_end = len(lines)

        seen_pairs = set()
        kept_bonds = []
        for raw in lines[bond_start:bond_end]:
            parts = raw.split()
            if len(parts) < 4:
                continue
            a1 = parts[1]
            a2 = parts[2]
            if a1 == a2:
                continue
            key = tuple(sorted((a1, a2)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            kept_bonds.append(parts)

        new_bond_lines = []
        for idx, parts in enumerate(kept_bonds, start=1):
            parts[0] = str(idx)
            new_bond_lines.append(' '.join(parts) + '\n')

        if counts_line is not None and counts_line < len(lines):
            counts_parts = lines[counts_line].split()
            if len(counts_parts) >= 2:
                counts_parts[1] = str(len(new_bond_lines))
                lines[counts_line] = ' '.join(counts_parts) + '\n'

        new_content = lines[:bond_start] + new_bond_lines + lines[bond_end:]
        with open(output_mol2, 'w', encoding='utf-8') as f:
            f.writelines(new_content)
        return True
    except Exception as exc:
        print(f"MOL2 键连去重失败: {exc}")
        return False

def run_tleap(
    mol2_file: str,
    frcmod_file: Optional[str],
    output_prmtop: str,
    output_inpcrd: str,
    residue_name: str = 'LIG',
    protein_pdb: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> bool:
    """
    使用 tLEaP 生成 AMBER 格式的拓扑文件和坐标文件。
    
    Args:
        mol2_file: antechamber 生成的 MOL2 文件
        frcmod_file: parmchk2 生成的 frcmod 文件（可选）
        output_prmtop: 输出拓扑文件路径 (.prmtop)
        output_inpcrd: 输出坐标文件路径 (.inpcrd)
        residue_name: 残基名称
        protein_pdb: 如果提供，将配体与蛋白结合生成复合物拓扑
    
    Returns:
        成功返回 True，失败返回 False
    """
    if shutil.which('tleap') is None:
        print("错误：未找到 tleap 命令，请安装 AmberTools")
        return False
    
    mol2_abs = os.path.abspath(mol2_file)
    frcmod_abs = os.path.abspath(frcmod_file) if frcmod_file else None
    prmtop_abs = os.path.abspath(output_prmtop)
    inpcrd_abs = os.path.abspath(output_inpcrd)
    protein_abs = os.path.abspath(protein_pdb) if protein_pdb else None
    run_cwd = os.path.abspath(work_dir) if work_dir else str(_ensure_temp_subdir('tleap'))
    os.makedirs(run_cwd, exist_ok=True)

    # 创建 tLEaP 脚本
    leap_script = []
    if protein_abs and os.path.exists(protein_abs):
        leap_script.append('source leaprc.protein.ff19SB')
    leap_script.append('source leaprc.gaff2')

    if frcmod_abs and os.path.exists(frcmod_abs):
        leap_script.append(f'loadamberparams {frcmod_abs}')

    leap_script.append(f'LIG = loadmol2 {mol2_abs}')

    if protein_abs and os.path.exists(protein_abs):
        leap_script.append(f'PRO = loadpdb {protein_abs}')
        leap_script.append('COM = combine { PRO LIG }')
        leap_script.append(f'saveamberparm COM {prmtop_abs} {inpcrd_abs}')
    else:
        leap_script.append(f'saveamberparm LIG {prmtop_abs} {inpcrd_abs}')
    
    leap_script.append('quit')
    
    def _run_with_script(script_lines: List[str]) -> Tuple[bool, str, str]:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.in', delete=False, dir=run_cwd) as f:
            f.write('\n'.join(script_lines))
            script_file = f.name

        cmd = ['tleap', '-f', script_file, '-s']
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=run_cwd)
        os.unlink(script_file)

        out = result.stdout or ''
        err = result.stderr or ''
        has_fatal = 'fatal error' in out.lower() or '!fatal' in out.lower()
        has_nonzero_errors = re.search(r'Exiting LEaP:\s+Errors\s*=\s*[1-9]\d*', out) is not None
        failed = result.returncode != 0 or has_fatal or has_nonzero_errors
        return (not failed), out, err

    ok, stdout_text, stderr_text = _run_with_script(leap_script)
    if not ok:
        failure_text = f"{stdout_text}\n{stderr_text}".lower()
        if 'cannot add bond' in failure_text or 'duplicate bond' in failure_text:
            dedup_mol2 = str(Path(prmtop_abs).with_name(f"{Path(mol2_abs).stem}_dedup.mol2"))
            if _deduplicate_mol2_bonds(mol2_abs, dedup_mol2):
                print(f"检测到重复键连，使用去重后的 MOL2 重试: {dedup_mol2}")
                retry_script = [line if not line.startswith('LIG = loadmol2 ') else f'LIG = loadmol2 {dedup_mol2}' for line in leap_script]
                ok, stdout_text, stderr_text = _run_with_script(retry_script)

        if not ok:
            print(f"tLEaP 执行失败:\n{stdout_text}\n{stderr_text}")
            return False
    
    # 检查是否生成文件
    if not os.path.exists(output_prmtop) or not os.path.exists(output_inpcrd):
        print("tLEaP 执行成功但未生成目标文件")
        return False
    
    return True


# ============================================================================
# 4. PDBQT 生成（用于对接）
# ============================================================================

def run_prepare_ligand4_py(
    input_file: str,
    output_pdbqt: str,
) -> ToolResult:
    """
    使用 MGLTools 的 prepare_ligand4.py 脚本生成 PDBQT 文件。
    优先在指定的 conda 环境（默认 mgltools）中执行，以确保 Python 2.7 兼容性。

    降级层次: L0 MGLTools prepare_ligand4.py → L1 OpenBabel obabel → L2 fail

    Args:
        input_file: 输入文件（PDB 或 MOL2）
        output_pdbqt: 输出 PDBQT 文件路径

    Returns:
        ToolResult
    """

    input_abs = os.path.abspath(input_file)
    output_abs = os.path.abspath(output_pdbqt)
    output_dir = os.path.dirname(output_abs)

    if not os.path.exists(input_abs):
        return failed(errors=[f"输入文件不存在: {input_abs}"])

    script_path = PREPARE_LIGAND4_SCRIPT
    mgltools_pckgs_path = MGLTOOLS_PCKGS_PATH
    conda_env = CONDA_MGLTOOLS_ENV

    env = os.environ.copy()
    if 'PYTHONPATH' in env:
        env['PYTHONPATH'] = f"{str(mgltools_pckgs_path)}:{env['PYTHONPATH']}"
    else:
        env['PYTHONPATH'] = str(mgltools_pckgs_path)

    conda_executable = shutil.which("conda")

    if not conda_executable:
        return _pdbqt_fallback_obabel(input_abs, output_abs,
            degradation=["conda unavailable→OpenBabel"],
            errors=["未找到 conda 命令"])

    result = subprocess.run(
        [conda_executable, "env", "list"],
        capture_output=True, text=True
    )
    if conda_env not in result.stdout:
        return _pdbqt_fallback_obabel(input_abs, output_abs,
            degradation=["conda mgltools env missing→OpenBabel"],
            errors=[f"conda 环境 '{conda_env}' 不存在"])

    # L0: MGLTools prepare_ligand4.py
    cmd = [
        conda_executable, "run", "-n", conda_env,
        "python", str(script_path),
        "-l", input_abs,
        "-o", output_abs,
        "-v"
    ]

    os.makedirs(output_dir, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=output_dir)

    if result.returncode != 0:
        # L0 失败 → L1 OpenBabel
        return _pdbqt_fallback_obabel(input_abs, output_abs,
            degradation=["MGLTools→OpenBabel"],
            errors=[f"prepare_ligand4.py 执行失败: {result.stderr.strip()}"])

    return success(data=f"MGLTools 生成 PDBQT 文件: {output_abs}")


def _pdbqt_fallback_obabel(
    input_abs: str,
    output_abs: str,
    *,
    degradation: list,
    errors: list,
) -> ToolResult:
    """L1 降级: 使用 OpenBabel 生成 PDBQT。"""
    ok = run_obabel_pdbqt(input_abs, output_abs)
    if ok:
        return degraded(
            data=f"降级使用 OpenBabel 生成 PDBQT: {output_abs}（电荷精度较低，为 Gasteiger 电荷）",
            degradation=degradation,
            errors=errors,
            warnings=["OpenBabel 使用 Gasteiger 电荷，精度低于 MGLTools"],
        )
    return failed(
        errors=errors + ["OpenBabel PDBQT 转换也失败"],
        degradation=degradation + ["OpenBabel→failed"],
    )

def run_obabel_pdbqt(input_file: str, output_pdbqt: str) -> bool:
    """
    使用 Open Babel 将 MOL2 或 PDB 转换为 PDBQT 格式。
    
    注意：此方法生成的电荷为 Gasteiger 电荷，精度较低，但速度快。
    
    Args:
        input_file: 输入文件路径（MOL2 或 PDB）
        output_pdbqt: 输出 PDBQT 文件路径
    
    Returns:
        成功返回 True，失败返回 False
    """
    if shutil.which('obabel') is None:
        print("错误：未找到 obabel 命令，请安装 Open Babel")
        return False
    
    # 检测输入格式
    input_ext = Path(input_file).suffix.lower()
    if input_ext == '.mol2':
        in_fmt = 'mol2'
    elif input_ext == '.pdb':
        in_fmt = 'pdb'
    else:
        in_fmt = 'pdb'
    
    cmd = ['obabel', f'-i{in_fmt}', input_file, '-opdbqt', '-O', output_pdbqt]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Open Babel 转换失败:\n{result.stderr}")
        return False
    
    return True


# ============================================================================
# 5. 辅助工具：文件格式转换
# ============================================================================

def run_obabel(
    input_file: str,
    output_file: str,
    input_format: str = 'pdb',
    output_format: str = 'mol2'
) -> bool:
    """
    使用 Open Babel 进行通用格式转换。
    
    Args:
        input_file: 输入文件路径
        output_file: 输出文件路径
        input_format: 输入格式
        output_format: 输出格式
    
    Returns:
        成功返回 True，失败返回 False
    """
    if shutil.which('obabel') is None:
        print("错误：未找到 obabel 命令，请安装 Open Babel")
        return False
    
    cmd = ['obabel', f'-i{input_format}', input_file, f'-o{output_format}', '-O', output_file]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Open Babel 转换失败: {input_file} -> {output_file}")
        return False
    
    return True


# ============================================================================
# 6. ACPYPE：生成 GROMACS 拓扑文件
# ============================================================================

def run_acpype(
    input_file: str,
    output_dir: str,
    net_charge: Optional[int] = None,
    output_format: str = 'gmx'
) -> bool:
    """
    使用 ACPYPE 生成 GROMACS 或 AMBER 格式的拓扑文件。
    
    ACPYPE 是基于 Antechamber 的自动化工具，特别适合 GROMACS 用户。
    
    Args:
        input_file: 输入 MOL2 文件
        output_dir: 输出目录
        net_charge: 净电荷（可选，会覆盖 MOL2 中的电荷）
        output_format: 输出格式 ('gmx' for GROMACS, 'amb' for AMBER)
    
    Returns:
        成功返回 True，失败返回 False
    """
    if shutil.which('acpype') is None:
        print("警告：未找到 acpype 命令，跳过 GROMACS 拓扑生成")
        return False
    
    cmd = ['acpype', '-i', input_file, '-o', output_format, '-d']
    
    if net_charge is not None:
        cmd.extend(['-n', str(net_charge)])
    
    # 在输出目录中运行
    original_cwd = os.getcwd()
    os.chdir(output_dir)
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    os.chdir(original_cwd)
    
    if result.returncode != 0:
        print(f"ACPYPE 执行失败:\n{result.stderr}")
        return False
    
    return True


# ============================================================================
# 7. 主流程：端到端配体参数化
# ============================================================================

def prepare_ligand_amber_route(
    input_smiles: Optional[str] = None,
    input_pdb: Optional[str] = None,
    input_file: Optional[str] = None,
    input_format: Optional[str] = None,
    output_dir: str = './ligand_output',
    net_charge: Optional[int] = None,
    residue_name: str = 'LIG',
    charge_method: str = 'bcc',
    force_field: str = 'gaff2',
    antechamber_extra_args: Optional[List[str]] = None,
    antechamber_kwargs: Optional[Dict[str, Union[str, int, float, bool, None]]] = None,
    antechamber_intermediate_dir: Optional[str] = None,
    robust_input: bool = True,
    fallback_charge_methods: Optional[List[str]] = None,
    generate_pdbqt: bool = True,
    generate_md_files: bool = True,
    generate_gmx_files: bool = False,
    protein_pdb: Optional[str] = None
) -> Dict[str, Union[str, List[str]]]:
    """
    完整的高精度配体参数化流程。
    
    输入：SMILES 或结构文件（PDB/MOL2/SDF 等，取决于 antechamber 支持）
    输出：GAFF 参数、电荷、PDBQT（对接）、PRMTOP/INPCRD（AMBER MD）
    
    Args:
        input_smiles: SMILES 字符串（与 input_pdb/input_file 三选一）
        input_pdb: 输入 PDB 文件路径（兼容旧参数）
        input_file: 通用输入文件路径（推荐，支持多格式）
        input_format: 输入格式，若不传则按扩展名自动推断（pdb/mol2/sdf）
        output_dir: 输出目录
        net_charge: 分子净电荷。若为 None，会在 SMILES 输入时尝试自动推断，否则默认 0
        residue_name: 残基名称
        charge_method: 电荷方法 ('bcc', 'resp')
        force_field: 力场类型 ('gaff', 'gaff2')
        antechamber_extra_args: 传给 antechamber 的额外参数列表
        antechamber_kwargs: 传给 antechamber 的额外键值参数（会转为 -key value）
        antechamber_intermediate_dir: antechamber 中间文件输出目录；会按尝试轮次分子目录保存
        robust_input: 是否启用输入格式和电荷方法回退重试
        fallback_charge_methods: 失败时额外尝试的电荷方法列表（如 ['gas']）
        generate_pdbqt: 是否生成对接用的 PDBQT 文件
        generate_md_files: 是否生成 AMBER MD 拓扑/坐标文件
        generate_gmx_files: 是否生成 GROMACS 拓扑文件（需要 acpype）
        protein_pdb: 蛋白 PDB 文件路径（生成复合物拓扑时使用）
    
    Returns:
        包含输出文件路径的字典
    """
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    result_files = {
        'mol2': None,
        'frcmod': None,
        'prmtop': None,
        'inpcrd': None,
        'pdbqt': None,
        'gmx_top': None,
        'gmx_itp': None,
        'gmx_gro': None,
    }
    
    # Step 1: 获取输入文件与输入格式
    ante_input_file = None
    ante_input_format = None
    temp_files: List[str] = []

    if input_smiles:
        ante_input_file = str(output_path / 'input_from_smiles.pdb')
        if not smiles_to_pdb(input_smiles, ante_input_file):
            return {'error': 'SMILES 转 PDB 失败'}
        ante_input_format = 'pdb'

        if net_charge is None:
            try:
                from rdkit import Chem
                mol = Chem.MolFromSmiles(input_smiles)
                net_charge = int(Chem.GetFormalCharge(mol)) if mol is not None else 0
            except Exception:
                net_charge = 0
    else:
        candidate_file = input_file or input_pdb
        if candidate_file and os.path.exists(candidate_file):
            ante_input_file = candidate_file
            if input_format:
                ante_input_format = input_format.lower()
            else:
                ext = Path(candidate_file).suffix.lower()
                ext_map = {
                    '.pdb': 'pdb',
                    '.mol2': 'mol2',
                    '.sdf': 'sdf',
                    '.mdl': 'mdl',
                    '.ac': 'ac',
                    '.mol': 'mdl',
                }
                ante_input_format = ext_map.get(ext, 'pdb')
        else:
            return {'error': '必须提供 input_smiles 或有效的 input_pdb/input_file'}

    if net_charge is None:
        net_charge = 0

    # antechamber 对 MOL2 输入常见做法是仅重打类型/电荷（不强制重新建连接）
    base_kwargs = dict(antechamber_kwargs or {})
    if ante_input_format == 'mol2' and 'j' not in base_kwargs:
        base_kwargs['j'] = 4

    intermediate_root: Optional[Path] = None
    if antechamber_intermediate_dir:
        inter_path = Path(antechamber_intermediate_dir)
        if not inter_path.is_absolute():
            inter_path = output_path / inter_path
        inter_path.mkdir(parents=True, exist_ok=True)
        intermediate_root = inter_path
    else:
        intermediate_root = _ensure_temp_subdir('antechamber', output_path.name or residue_name.lower())

    input_candidates: List[Tuple[str, str, str]] = [(ante_input_file, ante_input_format, 'original')]
    if robust_input and shutil.which('obabel') is not None:
        if ante_input_format != 'pdb':
            fallback_pdb = str(output_path / 'input_fallback.pdb')
            if run_obabel(ante_input_file, fallback_pdb, input_format=ante_input_format, output_format='pdb'):
                input_candidates.append((fallback_pdb, 'pdb', 'obabel->pdb'))
                temp_files.append(fallback_pdb)
        if ante_input_format != 'mol2':
            fallback_mol2 = str(output_path / 'input_fallback.mol2')
            if run_obabel(ante_input_file, fallback_mol2, input_format=ante_input_format, output_format='mol2'):
                input_candidates.append((fallback_mol2, 'mol2', 'obabel->mol2'))
                temp_files.append(fallback_mol2)

    charge_methods: List[str] = [charge_method]
    if fallback_charge_methods:
        for method in fallback_charge_methods:
            method_l = str(method).lower()
            if method_l and method_l not in charge_methods:
                charge_methods.append(method_l)
    if robust_input and 'gas' not in charge_methods:
        charge_methods.append('gas')

    # Step 2: Antechamber 生成 MOL2（GAFF 类型 + 电荷）
    mol2_file = str(output_path / f'{residue_name.lower()}.mol2')
    success = False
    err_msg = ''
    attempt_logs: List[str] = []
    attempt_index = 0

    for candidate_file, candidate_fmt, candidate_desc in input_candidates:
        current_kwargs = dict(base_kwargs)
        if candidate_fmt == 'mol2' and 'j' not in current_kwargs:
            current_kwargs['j'] = 4

        for method in charge_methods:
            attempt_index += 1
            attempt_intermediate_dir = None
            if intermediate_root is not None:
                safe_desc = re.sub(r'[^A-Za-z0-9_.-]+', '_', candidate_desc)
                attempt_dir = intermediate_root / f'attempt_{attempt_index:02d}_{safe_desc}_{candidate_fmt}_{method}'
                attempt_dir.mkdir(parents=True, exist_ok=True)
                attempt_intermediate_dir = str(attempt_dir)

            success, err_msg = run_antechamber(
                candidate_file,
                mol2_file,
                input_format=candidate_fmt,
                charge_method=method,
                net_charge=net_charge,
                residue_name=residue_name,
                force_field=force_field,
                extra_args=antechamber_extra_args,
                intermediate_dir=attempt_intermediate_dir,
                **current_kwargs,
            )
            if success:
                break
            attempt_logs.append(
                f"[{candidate_desc}|fi={candidate_fmt}|charge={method}] {err_msg.strip()}"
            )
        if success:
            break

    for tmp_file in temp_files:
        if os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass

    if not success:
        return {
            'error': 'Antechamber 执行失败',
            'error_detail': err_msg,
            'input_file': ante_input_file,
            'input_format': ante_input_format,
            'antechamber_intermediate_dir': str(intermediate_root) if intermediate_root is not None else None,
            'attempts': attempt_logs,
        }

    result_files['mol2'] = mol2_file

    # Step 3: Parmchk2 生成 frcmod 补丁
    frcmod_file = str(output_path / f'{residue_name.lower()}.frcmod')
    parmchk2_work_dir = _ensure_temp_subdir('parmchk2', output_path.name or residue_name.lower())
    run_parmchk2(mol2_file, frcmod_file, force_field=force_field, work_dir=str(parmchk2_work_dir))
    if os.path.exists(frcmod_file):
        result_files['frcmod'] = frcmod_file

    # Step 4: 生成 PDBQT（对接）
    if generate_pdbqt:
        pdbqt_file = str(output_path / f'{residue_name.lower()}.pdbqt')
        pdbqt_result = run_prepare_ligand4_py(mol2_file, pdbqt_file)
        if pdbqt_result.ok:
            result_files['pdbqt'] = pdbqt_file
            pdbqt_degradation = pdbqt_result.degradation
        else:
            # L1 fallback already handled inside run_prepare_ligand4_py
            pdbqt_degradation = ["PDBQT generation failed"]

    # Step 5: 生成 AMBER 拓扑文件（MD）
    if generate_md_files:
        prmtop_file = str(output_path / f'{residue_name.lower()}.prmtop')
        inpcrd_file = str(output_path / f'{residue_name.lower()}.inpcrd')
        tleap_work_dir = _ensure_temp_subdir('tleap', output_path.name or residue_name.lower())
        if run_tleap(
            mol2_file,
            frcmod_file,
            prmtop_file,
            inpcrd_file,
            residue_name,
            protein_pdb,
            work_dir=str(tleap_work_dir),
        ):
            result_files['prmtop'] = prmtop_file
            result_files['inpcrd'] = inpcrd_file

    # Step 6: 生成 GROMACS 拓扑文件（可选）
    if generate_gmx_files and run_acpype(mol2_file, output_dir, net_charge, 'gmx'):
        prefix = Path(mol2_file).stem
        for ext in ['_GMX.top', '_GMX.itp', '_GMX.gro']:
            src = output_path / f'{prefix}{ext}'
            dst = output_path / f'{residue_name.lower()}{ext}'
            if src.exists():
                shutil.move(str(src), str(dst))
                if ext == '_GMX.top':
                    result_files['gmx_top'] = str(dst)
                elif ext == '_GMX.itp':
                    result_files['gmx_itp'] = str(dst)
                elif ext == '_GMX.gro':
                    result_files['gmx_gro'] = str(dst)

    # Collect degradation info from sub-steps
    degradation_steps: list = []
    # Ensure result_files includes degradation metadata
    return result_files


# ============================================================================
# 使用示例
# ============================================================================

if __name__ == "__main__":
    result = prepare_ligand_amber_route(
        input_smiles = "O=C(O)[C@@H](N)CC[S+](C)C[C@H]1O[C@H]([C@H](O)[C@@H]1O)n2cnc3c(N)ncnc23",
        output_dir = './ligand_output',
        net_charge = 0,                          # 修正净电荷
        residue_name = 'LIG',
        charge_method = 'gas',
        antechamber_kwargs = {'m': 1, 'j': 4},           # 添加 -m 1 参数
        generate_pdbqt = True,
        generate_md_files = True,
        generate_gmx_files = False
    )

    print("生成结果：")
    for key, value in result.items():
        if value:
            print(f"  {key}: {value}")