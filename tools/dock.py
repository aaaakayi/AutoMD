"""
对接工具模块，负责从蛋白质结构预测结合口袋并生成对接盒参数。
当前实现基于 P2Rank 进行口袋预测，并使用 PyMOL + GetBox 插件精确计算盒子尺寸和中心。后续可扩展支持其他口袋预测工具和对接软件。

工具接口：
- get_docking_box_from_p2rank (protein_pdb, output_dir, use_getbox=True, extension=8.0) -> dict
    从 P2Rank 预测结果中提取 top-1 口袋，并生成对接盒子参数。
    优先使用 GetBox 基于残基列表精确计算盒子尺寸和中心；
    若 GetBox 不可用或残基信息缺失，则降级为简单尺寸估算。
- dock (protein_file, ligand_file, output_dir, center_x, center_y, center_z, size_x, size_y, size_z, exhaustiveness, num_modes, energy_range) -> str
    Agent 对外接口（当前版本输出对接盒参数，不在此函数内执行 Vina）。


"""

import subprocess
import csv
import re
import sys
from pathlib import Path
import tempfile
import shutil
from typing import List, Dict, Any, Tuple, Optional, Union

P2RANK_TIMEOUT_SECONDS = 300
project_root = Path(__file__).resolve().parent.parent

PRANK_EXEC = project_root / "dock_tools" / "P2Rank" / "p2rank" / "distro" / "prank"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """去掉 CSV 表头和值两侧空格，兼容 P2Rank 的 spaced CSV 输出。"""
    normalized: Dict[str, Any] = {}
    for key, value in row.items():
        normalized[str(key).strip()] = value.strip() if isinstance(value, str) else value
    return normalized


def _count_residue_ids(value: Any) -> int:
    if not value:
        return 0
    if isinstance(value, str):
        return len([token for token in value.split() if token.strip()])
    return 0


def _is_invalid_box_value(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, (int, float)):
        return True
    # NaN check
    return value != value


def _is_implausible_getbox_result(box: Dict[str, float], pocket: Dict[str, Any]) -> bool:
    required = ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z")
    if any(_is_invalid_box_value(box.get(k)) for k in required):
        return True

    sx, sy, sz = float(box["size_x"]), float(box["size_y"]), float(box["size_z"])
    if sx <= 0 or sy <= 0 or sz <= 0:
        return True

    # 某些异常场景下 GetBox 会给出全 0 中心和固定小盒子，直接判为不可信。
    cx, cy, cz = float(box["center_x"]), float(box["center_y"]), float(box["center_z"])
    if abs(cx) < 1e-6 and abs(cy) < 1e-6 and abs(cz) < 1e-6 and max(sx, sy, sz) <= 20.0:
        return True

    px = float(pocket.get("center_x", 0.0) or 0.0)
    py = float(pocket.get("center_y", 0.0) or 0.0)
    pz = float(pocket.get("center_z", 0.0) or 0.0)
    # 若 GetBox 中心与 P2Rank 中心偏差极大，通常是选择器失效导致，回退到 CSV 结果更稳妥。
    if abs(cx - px) > 80 or abs(cy - py) > 80 or abs(cz - pz) > 80:
        return True

    return False

def _run_command(cmd: List[str], timeout: int, err_prefix: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{err_prefix}: 执行超时（>{timeout}s）")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{err_prefix}: {err}")
    return result

def parse_p2rank_predictions(csv_path: Path) -> List[Dict[str, Any]]:
    pockets = []
    with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            row = _normalize_row(row)

            # 跳过空行或无效行
            if not row.get('center_x') or not row.get('center_y') or not row.get('center_z'):
                continue

            residue_ids = row.get('residue_ids', '') or row.get('residues', '')
            num_residues = row.get('num_residues')
            if num_residues in (None, ''):
                num_residues = _count_residue_ids(residue_ids)

            pockets.append({
                'name': row.get('name', ''),
                'rank': _safe_int(row.get('rank', 0), 0),
                'score': _safe_float(row.get('score', 0.0), 0.0),
                'probability': _safe_float(row.get('probability', 0.0), 0.0),
                'center_x': _safe_float(row.get('center_x', 0.0), 0.0),
                'center_y': _safe_float(row.get('center_y', 0.0), 0.0),
                'center_z': _safe_float(row.get('center_z', 0.0), 0.0),
                'residues': residue_ids,
                'num_residues': _safe_int(num_residues or 0, 0),
            })

    # 按 rank 升序排序；若 rank 缺失则按 score 降序
    pockets.sort(key=lambda x: (x['rank'] if x['rank'] > 0 else 10**9, -x['score']))
    return pockets

def run_p2rank(protein_pdb: Path, output_dir: Path) -> Path:
    """
    运行 P2Rank 预测蛋白质结合口袋。
    
    Returns:
        生成的 predictions.csv 文件路径
    """
    if not PRANK_EXEC.exists():
        raise FileNotFoundError(f"P2Rank 可执行文件不存在: {PRANK_EXEC}")

    # P2Rank 的 prank 通常是 Linux 脚本；在 Windows PowerShell 下直接运行会报 WinError 193。
    if sys.platform.startswith("win") and PRANK_EXEC.suffix.lower() != ".exe":
        raise RuntimeError(
            "检测到 Windows shell 正在调用 Linux 版 P2Rank 可执行脚本。"
            "请在 WSL/Linux 环境中运行，或提供可在 Windows 运行的 P2Rank 可执行文件。"
        )
    
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(PRANK_EXEC),
        "predict",
        "-f", str(protein_pdb),
        "-o", str(output_dir)
    ]
    _run_command(cmd, timeout=P2RANK_TIMEOUT_SECONDS, err_prefix="P2Rank 预测失败")
    
    # 定位生成的 CSV 文件
    csv_files = list(output_dir.glob("*_predictions.csv"))
    if not csv_files:
        raise FileNotFoundError(f"未找到 P2Rank 输出文件: {output_dir}")

    # 优先匹配当前蛋白对应文件名；否则取最新文件避免误用旧结果。
    preferred_candidates = [
        output_dir / f"{protein_pdb.name}_predictions.csv",
        output_dir / f"{protein_pdb.stem}_predictions.csv",
    ]
    for preferred in preferred_candidates:
        if preferred.exists():
            return preferred

    # 再尝试按 stem 前缀匹配，适配部分版本输出命名差异。
    stem_prefixed = sorted(output_dir.glob(f"{protein_pdb.stem}*_predictions.csv"))
    if stem_prefixed:
        stem_prefixed.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return stem_prefixed[0]

    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return csv_files[0]


def _build_pymol_residue_selection(residue_ids: List[str]) -> str:
    """
    将 P2Rank 的 residue_ids（如 A_10 B_221）转换为 PyMOL selection。
    输出形如: (chain A and resi 10+11) or (chain B and resi 221)
    """
    chain_map: Dict[str, List[str]] = {}
    for token in residue_ids:
        token = token.strip()
        if not token:
            continue

        # 兼容 A_10、A:10、10 三种写法。
        if "_" in token:
            chain, resi = token.split("_", 1)
        elif ":" in token:
            chain, resi = token.split(":", 1)
        else:
            chain, resi = "", token

        chain = chain.strip()
        resi = resi.strip()
        if not resi:
            continue
        chain_map.setdefault(chain, []).append(resi)

    parts: List[str] = []
    for chain, residues in chain_map.items():
        uniq = sorted(set(residues), key=lambda x: (_safe_int(x, 10**9), x))
        joined = "+".join(uniq)
        if chain:
            parts.append(f"(chain {chain} and resi {joined})")
        else:
            parts.append(f"(resi {joined})")

    return " or ".join(parts) if parts else ""

def get_docking_box_from_p2rank(
    protein_pdb: Union[str, Path],
    output_dir: Union[str, Path],
    use_getbox: bool = True,
    extension: float = 8.0,
    fallback_to_simple: bool = True
) -> Dict[str, float]:
    """
    从 P2Rank 预测结果中提取 top-1 口袋，并生成对接盒子参数。
    优先使用 GetBox 基于残基列表精确计算盒子尺寸和中心；
    若 GetBox 不可用或残基信息缺失，则降级为简单尺寸估算。

    Args:
        protein_pdb: 蛋白质 PDB 文件路径
        output_dir: P2Rank 输出目录
        use_getbox: 是否尝试使用 GetBox 精确计算（需要 PyMOL 环境）
        extension: GetBox 扩展半径（Å）
        fallback_to_simple: GetBox 失败时是否降级到简单尺寸估算

    Returns:
        字典包含 center_x, center_y, center_z, size_x, size_y, size_z
    """
    protein_pdb = Path(protein_pdb).resolve()
    output_dir = Path(output_dir).resolve()

    # 1. 运行 P2Rank 获取口袋信息
    csv_path = run_p2rank(protein_pdb, output_dir)
    pockets = parse_p2rank_predictions(csv_path)
    if not pockets:
        raise ValueError("未预测到任何结合口袋")
    top = pockets[0]

    # 2. 如果启用 GetBox 且口袋包含残基信息，则使用 GetBox 精确计算
    if use_getbox:
        residues_str = top.get('residues', '')
        if residues_str:
            # 解析残基编号列表（空格分隔的数字）
            residue_ids = residues_str.split()
            if residue_ids:
                try:
                    # 调用之前封装的 GetBox 函数
                    box = get_docking_box_from_pymol_getbox(
                        protein_pdb=protein_pdb,
                        residue_ids=residue_ids,
                        extension=extension
                    )
                    if _is_implausible_getbox_result(box, top):
                        raise RuntimeError(f"GetBox 返回异常盒参数: {box}")
                    return box
                except Exception as e:
                    if fallback_to_simple:
                        print(f"GetBox 计算失败 ({e})，降级使用简单尺寸估算")
                    else:
                        raise

    # 3. 降级方案：简单尺寸估算（基于残基数）
    num_res = int(top.get('num_residues', 0) or 0)
    size = max(20.0, num_res * 1.5)
    return {
        'center_x': top['center_x'],
        'center_y': top['center_y'],
        'center_z': top['center_z'],
        'size_x': size,
        'size_y': size,
        'size_z': size,
    }


def get_docking_box_from_pymol_getbox(
    protein_pdb: Path,
    residue_ids: List[str],
    extension: float = 8.0,
    pymol_exec: str = "pymol",
    getbox_plugin_path: Optional[Path] = None,
    use_xvfb: bool = False
) -> Dict[str, float]:
    """
    使用 PyMOL + GetBox 插件，基于活性口袋的关键残基，计算对接盒子参数。

    Args:
        protein_pdb: 蛋白质 PDB 文件路径
        residue_ids: 关键残基编号列表（字符串形式），例如 ["214", "226", "245"]
        extension: 盒子扩展半径（Å），默认 8.0
        pymol_exec: PyMOL 可执行文件命令（默认 "pymol"）
        getbox_plugin_path: GetBox_Plugin.py 的路径，若为 None 则自动搜索常见位置
        use_xvfb: 是否使用 xvfb-run 包装 PyMOL（用于无图形界面的服务器）

    Returns:
        字典包含 center_x, center_y, center_z, size_x, size_y, size_z
    """
    # 1. 定位 GetBox 插件脚本
    if getbox_plugin_path is None:
        getbox_plugin_path = project_root / "dock_tools" / "GetBox" / "GetBox-PyMOL-Plugin" / "GetBox Plugin.py"
    if not getbox_plugin_path.exists():
        raise FileNotFoundError(f"GetBox 插件不存在: {getbox_plugin_path}")

    # 2. 检查 PyMOL 是否可用
    if not shutil.which(pymol_exec):
        raise RuntimeError(f"PyMOL 可执行文件 '{pymol_exec}' 未在 PATH 中找到。")

    # 3. 构造 PyMOL 命令脚本
    residue_sel = _build_pymol_residue_selection(residue_ids)
    if not residue_sel:
        raise ValueError("residue_ids 为空或格式无效，无法构造 PyMOL 选择器。")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.pml', delete=False) as f:
        script_path = Path(f.name)
        f.write(
f"""
load {protein_pdb}
run {getbox_plugin_path}
resibox {residue_sel}, {extension}
exit
"""
                )

    # 4. 执行 PyMOL
    cmd = [pymol_exec, "-c", str(script_path)]
    if use_xvfb:
        if shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
        else:
            print("警告: xvfb-run 未安装，将直接运行 PyMOL（可能在无显示器时失败）")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
    finally:
        script_path.unlink(missing_ok=True)  # 清理临时脚本

    if result.returncode != 0:
        raise RuntimeError(
            f"PyMOL/GetBox 执行失败 (code={result.returncode})。\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    output = result.stdout
    # 兼容 GetBox 常见输出格式：
    # 1) --center_x 21.3 --center_y 44.4 --center_z 20.9 --size_x 49.5 --size_y 46.2 --size_z 40.4
    # 2) center_x = 21.3, center_y = 44.4, center_z = 20.9
    #    size_x = 49.5, size_y = 46.2, size_z = 40.4
    num = r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"

    def _find_flag(flag: str) -> Optional[float]:
        m = re.search(rf"--{flag}\s+{num}", output)
        if not m:
            return None
        return float(m.group(1))

    cx = _find_flag("center_x")
    cy = _find_flag("center_y")
    cz = _find_flag("center_z")
    sx = _find_flag("size_x")
    sy = _find_flag("size_y")
    sz = _find_flag("size_z")

    # 若 flag 形式未命中，再尝试旧格式。
    if None in (cx, cy, cz, sx, sy, sz):
        pattern_center = rf"center_x\s*=\s*{num},\s*center_y\s*=\s*{num},\s*center_z\s*=\s*{num}"
        pattern_size = rf"size_x\s*=\s*{num},\s*size_y\s*=\s*{num},\s*size_z\s*=\s*{num}"

        center_match = re.search(pattern_center, output)
        size_match = re.search(pattern_size, output)

        if center_match and size_match:
            cx, cy, cz = float(center_match.group(1)), float(center_match.group(2)), float(center_match.group(3))
            sx, sy, sz = float(size_match.group(1)), float(size_match.group(2)), float(size_match.group(3))

    if None in (cx, cy, cz, sx, sy, sz):
        raise RuntimeError(
            f"无法从 PyMOL 输出中解析盒子参数。\n"
            f"PyMOL 输出内容:\n{output}\n"
            f"标准错误:\n{result.stderr}"
        )

    return {
        'center_x': float(cx),
        'center_y': float(cy),
        'center_z': float(cz),
        'size_x': float(sx),
        'size_y': float(sy),
        'size_z': float(sz),
    }

def dock(
    protein_file: str,
    ligand_file: str,
    output_dir: str = "./data/docking",
    center_x: Optional[float] = None,
    center_y: Optional[float] = None,
    center_z: Optional[float] = None,
    size_x: float = 15.0,
    size_y: float = 15.0,
    size_z: float = 15.0,
    use_getbox: bool = True, # 是否启用 GetBox 精确计算对接盒参数
    exhaustiveness: int = 8,
    num_modes: int = 9,
    energy_range: float = 3.0,
) -> str:
    """
    执行 AutoDock Vina 对接（仅接受 PDBQT 格式的蛋白和配体文件）。

    Args:
        protein_file: 蛋白质 PDBQT 文件路径（必须为 .pdbqt 后缀）
        ligand_file: 配体 PDBQT 文件路径（必须为 .pdbqt 后缀）
        output_dir: 输出目录
        center_x, center_y, center_z: 对接盒子中心（可选，不提供则自动计算）
        size_x, size_y, size_z: 盒子尺寸（埃），默认 20
        use_getbox: 是否启用 GetBox 精确计算对接盒参数（默认 True，需 PyMOL 环境）
        exhaustiveness: 搜索深度，默认 8
        num_modes: 输出结合模式数量，默认 9
        energy_range: 能量窗口（kcal/mol），默认 3.0

    Returns:
        对接结果摘要字符串。
    """
    # 创建输出目录
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 依赖检查
    if shutil.which("vina") is None:
        return "错误：未找到 AutoDock Vina (vina) 命令。请先安装（例如 conda install -c conda-forge vina）。"

    # 2. 解析输入文件路径并检查格式
    protein_path = Path(protein_file).resolve()
    ligand_path = Path(ligand_file).resolve()

    if not protein_path.exists():
        return f"错误：蛋白质文件不存在: {protein_path}"
    if not ligand_path.exists():
        return f"错误：配体文件不存在: {ligand_path}"

    if protein_path.suffix.lower() != ".pdbqt":
        return f"错误：蛋白质文件必须是 PDBQT 格式（后缀 .pdbqt），当前文件: {protein_path}"
    if ligand_path.suffix.lower() != ".pdbqt":
        return f"错误：配体文件必须是 PDBQT 格式（后缀 .pdbqt），当前文件: {ligand_path}"

    # 3. 确定对接盒子中心
    if center_x is None or center_y is None or center_z is None:
        try:
            # 使用 封装好的工具 get_docking_box_from_p2rank 得到蛋白质口袋和对接盒子大小
            box = get_docking_box_from_p2rank(protein_path, out_dir)
            center_x, center_y, center_z = box['center_x'], box['center_y'], box['center_z']
            if use_getbox:
                size_x, size_y, size_z = box['size_x'], box['size_y'], box['size_z']
        except Exception as e:
            return f"自动计算盒子中心失败: {e}"

    # 4. 准备 Vina 配置文件
    with tempfile.TemporaryDirectory(prefix="vina_", dir=out_dir) as tmpdir:
        work_dir = Path(tmpdir)
        config_file = work_dir / "config.txt"
        out_pdbqt = out_dir / "docked.pdbqt"

        with open(config_file, 'w') as f:
            f.write(f"receptor = {protein_path}\n")
            f.write(f"ligand = {ligand_path}\n")
            f.write(f"center_x = {center_x:.3f}\n")
            f.write(f"center_y = {center_y:.3f}\n")
            f.write(f"center_z = {center_z:.3f}\n")
            f.write(f"size_x = {size_x:.1f}\n")
            f.write(f"size_y = {size_y:.1f}\n")
            f.write(f"size_z = {size_z:.1f}\n")
            f.write(f"exhaustiveness = {exhaustiveness}\n")
            f.write(f"num_modes = {num_modes}\n")
            f.write(f"energy_range = {energy_range}\n")
            f.write(f"out = {out_pdbqt}\n")

        # 5. 运行 Vina
        cmd_vina = ["vina", "--config", str(config_file)]
        try:
            result = subprocess.run(
                cmd_vina,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=600
            )
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                return f"Vina 对接失败 (exit code {result.returncode}): {error_msg}"
        except subprocess.TimeoutExpired:
            return "Vina 对接超时（>600秒）。"

        # 6. 解析结果，提取最佳结合能
        best_energy = None
        output_lines = result.stdout.splitlines()
        for line in output_lines:
            if "REMARK VINA RESULT:" in line:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        best_energy = float(parts[3])
                    except ValueError:
                        pass
                break
        if best_energy is None:
            # 备选：从最后几行查找
            for line in reversed(output_lines):
                if "kcal/mol" in line and line.strip().startswith("1"):
                    try:
                        best_energy = float(line.split()[1])
                    except ValueError:
                        pass

        # 7. 可选：将结果 PDBQT 转换为 PDB 方便查看
        out_pdb = out_dir / "docked.pdb"
        if shutil.which("obabel") is not None:
            try:
                subprocess.run(
                    ["obabel", "-ipdbqt", str(out_pdbqt), "-opdb", "-O", str(out_pdb)],
                    capture_output=True,
                    timeout=30
                )
            except:
                pass

        # 8. 返回摘要
        summary = f"对接完成。最佳结合能: {best_energy:.2f} kcal/mol" if best_energy is not None else "对接完成，但未能解析结合能。"
        summary += f"\n结果文件保存为: {out_pdbqt.absolute()}"
        if out_pdb.exists():
            summary += f"\nPDB 格式结果: {out_pdb.absolute()}"
        summary += f"\n对接盒子中心: ({center_x:.2f}, {center_y:.2f}, {center_z:.2f})"
        summary += f"\n盒子尺寸: {size_x:.1f} × {size_y:.1f} × {size_z:.1f} Å³"
        return summary

if __name__ == "__main__":
    protein_pdb = project_root / "output" / "protein_preparation" / "1IEP.pdb"
    ligand_sdf = project_root / "output" / "ligand_preparation" / "ligand.pdbqt"
    output_dir = project_root / "output" / "p2rank"

    result = get_docking_box_from_p2rank(
        protein_pdb,
        output_dir,
        use_getbox=False,
    )

    result = dock(
        protein_file=protein_pdb,
        ligand_file=ligand_sdf
    )

    print(result)