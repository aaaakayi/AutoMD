#!/usr/bin/env python3
"""
纯蛋白 MD 预处理脚本 (pdb4amber + tleap)
功能：下载 PDB，标准化残基名并加氢，构建溶剂盒并中和电荷，生成 Amber 拓扑和坐标文件。
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional

from tools.shared import (
    PROJECT_ROOT,
    MGLTOOLS_PCKGS_PATH,
    CONDA_MGLTOOLS_ENV,
    PREPARE_RECEPTOR4_SCRIPT,
    success,
    degraded,
    failed,
    ToolResult,
)

# ==================== 工具函数 ====================

STANDARD_PROTEIN_RESIDUES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "CYX", "CYM", "GLN", "GLU", "GLH",
    "GLY", "HIS", "HID", "HIE", "HIP", "ILE", "LEU", "LYS", "LYN", "MET",
    "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "ASH",
}


def filter_standard_protein_residues(input_pdb: str, output_pdb: str) -> bool:
    """仅保留标准蛋白残基，避免 tleap 因非标准残基缺少类型而失败。"""
    try:
        kept = 0
        with open(input_pdb, "r", encoding="utf-8", errors="ignore") as src, open(output_pdb, "w", encoding="utf-8") as dst:
            for line in src:
                record = line[:6].strip()
                if record in {"ATOM", "HETATM"}:
                    resname = line[17:20].strip().upper()
                    if resname in STANDARD_PROTEIN_RESIDUES:
                        dst.write(line)
                        kept += 1
                    continue

                if record in {"TER", "END", "ENDMDL"}:
                    dst.write(line)

        if kept == 0:
            print("警告：过滤后没有保留任何标准蛋白原子")
            return False
        return True
    except Exception as exc:
        print(f"过滤标准蛋白残基失败: {exc}")
        return False

def fetch_pdb(pdb_id: str, output_dir: str = "./data/pdb") -> str:
    """从 RCSB 下载 PDB 文件。"""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    pdb_file = out_path / f"{pdb_id}.pdb"
    if pdb_file.exists():
        return str(pdb_file.resolve())
    url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
    import requests
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    pdb_file.write_text(r.text)
    return str(pdb_file.resolve())

def run_pdb4amber(pdb_file: str, output_file: str, keep_hetatm: bool = False) -> bool:
    """
    使用 pdb4amber 清洗 PDB：标准化残基名、加氢、可选保留 HETATM。
    注意：--reduce 调用 reduce 程序加氢，需要 AmberTools 已安装。
    """
    if shutil.which('pdb4amber') is None:
        raise RuntimeError("未找到 pdb4amber 命令，请安装 AmberTools。")
    cmd = ['pdb4amber', '-i', pdb_file, '-o', output_file, '--reduce']
    if keep_hetatm:
        cmd.append('--keep-hetatm')
    # 可选：添加 --dry 可以只做重命名不加氢（但这里需要加氢，所以不加）
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"pdb4amber 错误输出:\n{result.stderr}")
        return False
    # pdb4amber 有时会生成额外的 .pdb 后缀，检查输出文件是否存在
    out_path = Path(output_file)
    if not out_path.exists():
        # 尝试查找默认输出名称（有时为 input_basename.pdb）
        default = Path(pdb_file).stem + ".pdb"
        if Path(default).exists():
            shutil.move(default, out_path)
        else:
            return False
    return True

def run_tleap(clean_pdb: str, output_dir: Path, box_padding: float = 10.0, neutralize: bool = True) -> tuple[Path, Path]:
    """
    使用 tleap 构建 Amber 体系：加载力场、溶剂化、中和电荷，生成 prmtop/inpcrd。
    返回 (prmtop_path, inpcrd_path)
    """
    if shutil.which('tleap') is None:
        raise RuntimeError("未找到 tleap 命令，请安装 AmberTools。")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_pdb_path = Path(clean_pdb).resolve()
    
    # 构建 tleap 脚本
    leap_script = f"""
source leaprc.protein.ff19SB
source leaprc.water.tip3p
prot = loadpdb {clean_pdb_path}
solvateBox prot TIP3PBOX {box_padding}
"""
    if neutralize:
        leap_script += "addions prot Na+ 0\n"
    leap_script += f"""
saveamberparm prot complex.prmtop complex.inpcrd
quit
"""
    script_file = output_dir / "tleap.in"
    script_file.write_text(leap_script)
    
    # 运行 tleap
    result = subprocess.run(['tleap', '-f', script_file.name], cwd=output_dir,
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"tleap 错误输出:\n{result.stderr}\n{result.stdout}")
        raise RuntimeError("tleap 执行失败")
    
    prmtop = output_dir / "complex.prmtop"
    inpcrd = output_dir / "complex.inpcrd"
    if not prmtop.exists() or not inpcrd.exists():
        raise FileNotFoundError("tleap 未能生成 prmtop/inpcrd 文件")
    return prmtop, inpcrd

# ==================== 主流程 ====================

def prepare_pure_protein(pdb_id: str, output_root: str = "./output") -> dict:
    """
    纯蛋白处理主函数：
    1. 下载 PDB
    2. 用 pdb4amber 清洗并加氢
    3. 用 tleap 构建体系并生成 Amber 拓扑/坐标
    """
    output_root = Path(output_root)
    pdb_dir = output_root / "pdb"
    prep_dir = output_root / "prepared"
    md_dir = output_root / "md"
    for d in [pdb_dir, prep_dir, md_dir]:
        d.mkdir(parents=True, exist_ok=True)
    
    # 1. 下载
    raw_pdb = fetch_pdb(pdb_id, str(pdb_dir))
    
    # 2. pdb4amber 清洗
    clean_pdb = prep_dir / f"{pdb_id}_clean.pdb"
    if not run_pdb4amber(raw_pdb, str(clean_pdb), keep_hetatm=False):
        raise RuntimeError("pdb4amber 清洗失败")
    
    # 3. 仅保留标准蛋白残基，避免非标准残基导致 tleap 缺参
    protein_only_pdb = prep_dir / f"{pdb_id}_protein_only.pdb"
    if not filter_standard_protein_residues(str(clean_pdb), str(protein_only_pdb)):
        raise RuntimeError("标准蛋白残基过滤失败")

    # 4. tleap 构建拓扑
    prmtop, inpcrd = run_tleap(str(protein_only_pdb), md_dir, box_padding=10.0, neutralize=True)
    
    return {
        "raw_pdb": raw_pdb,
        "clean_pdb": str(clean_pdb),
        "protein_only_pdb": str(protein_only_pdb),
        "prmtop": str(prmtop),
        "inpcrd": str(inpcrd),
    }

# 使用 MGLTools 的 prepare_receptor4.py 脚本将蛋白质 PDB 转换为 PDBQT 格式
def run_prepare_receptor4_py(
    input_pdb: str,
    output_pdbqt: str
) -> bool:
    """
    使用 MGLTools 的 prepare_receptor4.py 脚本将蛋白质 PDB 转换为 PDBQT 格式。
    优先在指定的 conda 环境（默认 mgltools）中执行，以确保 Python 2.7 兼容性。

    Args:
        input_pdb: 输入的蛋白质 PDB 文件路径
        output_pdbqt: 输出 PDBQT 文件路径
        conda_env: mgltools 运行过程中需要的 conda 环境名称（默认 mgltools）

    Returns:
        成功返回 True，失败返回 False
    """
    import os
    import shutil
    import subprocess
    from pathlib import Path

    input_abs = os.path.abspath(input_pdb)
    output_abs = os.path.abspath(output_pdbqt)
    output_dir = os.path.dirname(output_abs)

    conda_env = CONDA_MGLTOOLS_ENV  # 使用共享配置

    if not os.path.exists(input_abs):
        print(f"输入文件不存在: {input_abs}")
        return False

    # 1. 定位 prepare_receptor4.py 脚本（使用共享配置）
    script_path = PREPARE_RECEPTOR4_SCRIPT
    mgltools_pckgs_path = MGLTOOLS_PCKGS_PATH

    env = os.environ.copy()
    if 'PYTHONPATH' in env:
        env['PYTHONPATH'] = f"{str(mgltools_pckgs_path)}:{env['PYTHONPATH']}"
    else:
        env['PYTHONPATH'] = str(mgltools_pckgs_path)

    # 2. 确定执行命令：优先使用 conda 环境中的 Python
    conda_executable = shutil.which("conda")
    use_conda_run = False

    if conda_executable:
        # 检查指定的 conda 环境是否存在
        result = subprocess.run(
            [conda_executable, "env", "list"],
            capture_output=True, text=True
        )
        if conda_env in result.stdout:
            use_conda_run = True
            print(f"使用 conda 环境 '{conda_env}' 执行 MGLTools 脚本")
        else:
            print(f"警告：conda 环境 '{conda_env}' 不存在，将使用当前 Python 环境")
    else:
        print("警告：未找到 conda 命令，将使用当前 Python 环境")

    # 3. 构建命令
    if use_conda_run:
        # 使用 conda run 在指定环境中执行
        # 常用参数：-r 受体文件，-o 输出，-A hydrogens 添加氢原子，-U nphs 处理非极性氢
        cmd = [
            conda_executable, "run", "-n", conda_env,
            "python", str(script_path),
            "-r", input_abs,
            "-o", output_abs,
            "-A", "hydrogens",   # 添加氢原子
            "-U", "nphs"         # 处理非极性氢（可选）
        ]
    else:
        print(f"错误：无法使用 {conda_env} 环境执行 MGLTools 脚本")
        return False

    # 4. 执行
    os.makedirs(output_dir, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=output_dir)

    if result.returncode != 0:
        print(f"prepare_receptor4.py 执行失败:\n{result.stderr}")
        # L0 MGLTools 失败 → L1 OpenBabel
        from tools.ligand import run_obabel_pdbqt
        if use_conda_run:
            print("conda 环境执行失败，降级使用 Open Babel 作为备选")
            if run_obabel_pdbqt(input_abs, output_pdbqt):
                return degraded(
                    data=f"降级使用 OpenBabel 生成 PDBQT: {output_pdbqt}",
                    degradation=["MGLTools→OpenBabel"],
                    errors=[f"MGLTools 失败: {result.stderr.strip()}"],
                    warnings=["OpenBabel 使用 Gasteiger 电荷，精度低于 MGLTools"],
                )
            return failed(
                errors=["MGLTools 和 OpenBabel 均失败"],
                degradation=["MGLTools→OpenBabel→failed"],
            )
        return failed(errors=[f"prepare_receptor4.py 执行失败: {result.stderr.strip()}"])
    else:
        print(f"成功通过 mgltools 生成 PDBQT 文件: {output_abs}")
        return success(data=f"MGLTools 生成 PDBQT 文件: {output_abs}")

# ==================== 命令行入口 ====================
if __name__ == "__main__":
    result = run_prepare_receptor4_py(
        input_pdb="../Agents/output/prepared/1AKE_protein_clean.pdb",
        output_pdbqt="./output/prepared/1AKE_clean.pdbqt"
    )
    if not result:
        print("prepare_receptor4.py 执行失败")
        sys.exit(1)