"""
Docking tools: pocket detection via P2Rank + GetBox, and AutoDock Vina docking.

All public functions return ToolResult.
"""

import csv
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.shared import PROJECT_ROOT, success, degraded, failed, ToolResult, tool_debug, is_tool_available, run_in_conda_env

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
    if value is None or not isinstance(value, (int, float)):
        return True
    return value != value


def _is_implausible_getbox_result(box: Dict[str, float], pocket: Dict[str, Any]) -> bool:
    required = ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z")
    if any(_is_invalid_box_value(box.get(k)) for k in required):
        return True
    sx, sy, sz = float(box["size_x"]), float(box["size_y"]), float(box["size_z"])
    if sx <= 0 or sy <= 0 or sz <= 0:
        return True
    cx, cy, cz = float(box["center_x"]), float(box["center_y"]), float(box["center_z"])
    if abs(cx) < 1e-6 and abs(cy) < 1e-6 and abs(cz) < 1e-6 and max(sx, sy, sz) <= 20.0:
        return True
    px = float(pocket.get("center_x", 0.0) or 0.0)
    py = float(pocket.get("center_y", 0.0) or 0.0)
    pz = float(pocket.get("center_z", 0.0) or 0.0)
    if abs(cx - px) > 80 or abs(cy - py) > 80 or abs(cz - pz) > 80:
        return True
    return False


def _run_command(cmd: List[str], timeout: int, err_prefix: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{err_prefix}: timeout (>{timeout}s)")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{err_prefix}: {err}")
    return result


def parse_p2rank_predictions(csv_path: Path) -> List[Dict[str, Any]]:
    pockets = []
    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            row = _normalize_row(row)
            if not row.get("center_x") or not row.get("center_y") or not row.get("center_z"):
                continue
            residue_ids = row.get("residue_ids", "") or row.get("residues", "")
            num_residues = row.get("num_residues")
            if num_residues in (None, ""):
                num_residues = _count_residue_ids(residue_ids)
            pockets.append({
                "name": row.get("name", ""),
                "rank": _safe_int(row.get("rank", 0), 0),
                "score": _safe_float(row.get("score", 0.0), 0.0),
                "probability": _safe_float(row.get("probability", 0.0), 0.0),
                "center_x": _safe_float(row.get("center_x", 0.0), 0.0),
                "center_y": _safe_float(row.get("center_y", 0.0), 0.0),
                "center_z": _safe_float(row.get("center_z", 0.0), 0.0),
                "residues": residue_ids,
                "num_residues": _safe_int(num_residues or 0, 0),
            })
    pockets.sort(key=lambda x: (x["rank"] if x["rank"] > 0 else 10**9, -x["score"]))
    return pockets


def run_p2rank(protein_pdb: Path, output_dir: Path) -> Path:
    """Run P2Rank to predict binding pockets. Returns path to predictions CSV.

    Raises FileNotFoundError if P2Rank not installed (env_packages=["p2rank"]).
    """
    if not PRANK_EXEC.exists():
        raise FileNotFoundError(f"P2Rank executable not found: {PRANK_EXEC}")
    if sys.platform.startswith("win") and PRANK_EXEC.suffix.lower() != ".exe":
        raise RuntimeError("Windows shell detected calling Linux P2Rank script. Run in WSL/Linux.")

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(PRANK_EXEC), "predict", "-f", str(protein_pdb), "-o", str(output_dir)]
    _run_command(cmd, timeout=P2RANK_TIMEOUT_SECONDS, err_prefix="P2Rank prediction failed")

    csv_files = list(output_dir.glob("*_predictions.csv"))
    if not csv_files:
        raise FileNotFoundError(f"P2Rank output not found: {output_dir}")
    for preferred in [
        output_dir / f"{protein_pdb.name}_predictions.csv",
        output_dir / f"{protein_pdb.stem}_predictions.csv",
    ]:
        if preferred.exists():
            return preferred
    stem_prefixed = sorted(output_dir.glob(f"{protein_pdb.stem}*_predictions.csv"))
    if stem_prefixed:
        stem_prefixed.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return stem_prefixed[0]
    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return csv_files[0]


def _build_pymol_residue_selection(residue_ids: List[str]) -> str:
    """Convert P2Rank residue_ids (e.g. A_10 B_221) to PyMOL selection."""
    chain_map: Dict[str, List[str]] = {}
    for token in residue_ids:
        token = token.strip()
        if not token:
            continue
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
    protein_pdb: str,
    output_dir: str,
    use_getbox: bool = True,
    extension: float = 8.0,
    fallback_to_simple: bool = True,
) -> ToolResult:
    """Extract top-1 pocket from P2Rank and compute docking box parameters.

    Prefers GetBox for precise box; falls back to simple size estimate.
    """
    protein_path = Path(protein_pdb).resolve()
    out_path = Path(output_dir).resolve()

    try:
        csv_path = run_p2rank(protein_path, out_path)
    except FileNotFoundError:
        return failed(
            errors=["P2Rank executable not found"],
            env_packages=["p2rank"],
        )
    except RuntimeError as exc:
        return failed(errors=[str(exc)])

    pockets = parse_p2rank_predictions(csv_path)
    if not pockets:
        return failed(errors=["No binding pockets predicted"])
    top = pockets[0]

    if use_getbox:
        residues_str = top.get("residues", "")
        if residues_str:
            residue_ids = residues_str.split()
            if residue_ids:
                r = get_docking_box_from_pymol_getbox(
                    protein_pdb=protein_path,
                    residue_ids=residue_ids,
                    extension=extension,
                )
                if r.ok and not _is_implausible_getbox_result(r.data, top):
                    return r
                if not fallback_to_simple:
                    return r

    num_res = int(top.get("num_residues", 0) or 0)
    size = max(20.0, num_res * 1.5)
    box_data = {
        "center_x": top["center_x"],
        "center_y": top["center_y"],
        "center_z": top["center_z"],
        "size_x": size,
        "size_y": size,
        "size_z": size,
    }
    tool_debug("[get_docking_box_from_p2rank] Degraded: simple size estimate")
    return degraded(data=box_data, degradation=["GetBox->simple size estimate"])


def get_docking_box_from_pymol_getbox(
    protein_pdb: Path,
    residue_ids: List[str],
    extension: float = 8.0,
    pymol_exec: str = "pymol",
    getbox_plugin_path: Optional[Path] = None,
    use_xvfb: bool = False,
) -> ToolResult:
    """Compute docking box via PyMOL + GetBox plugin using key residue list."""
    if getbox_plugin_path is None:
        getbox_plugin_path = (
            project_root / "dock_tools" / "GetBox" / "GetBox-PyMOL-Plugin" / "GetBox Plugin.py"
        )
    if not getbox_plugin_path.exists():
        return failed(errors=[f"GetBox plugin not found: {getbox_plugin_path}"])

    if not is_tool_available(pymol_exec):
        return failed(
            errors=[f"PyMOL executable '{pymol_exec}' not on PATH or in LangGraph conda env"],
            env_packages=["pymol"],
        )

    residue_sel = _build_pymol_residue_selection(residue_ids)
    if not residue_sel:
        return failed(errors=["residue_ids empty or invalid, cannot build PyMOL selector"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".pml", delete=False) as f:
        script_path = Path(f.name)
        f.write(
            f"load {protein_pdb}\n"
            f"run {getbox_plugin_path}\n"
            f"resibox {residue_sel}, {extension}\n"
            f"exit\n"
        )

    cmd = [pymol_exec, "-c", str(script_path)]
    if use_xvfb and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd

    try:
        result = run_in_conda_env(cmd, timeout=60)
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        return failed(errors=[
            f"PyMOL/GetBox failed (code={result.returncode}).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ])

    output = result.stdout
    num = r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"

    def _find_flag(flag: str) -> Optional[float]:
        m = re.search(rf"--{flag}\s+{num}", output)
        return float(m.group(1)) if m else None

    cx = _find_flag("center_x")
    cy = _find_flag("center_y")
    cz = _find_flag("center_z")
    sx = _find_flag("size_x")
    sy = _find_flag("size_y")
    sz = _find_flag("size_z")

    if None in (cx, cy, cz, sx, sy, sz):
        cm = re.search(
            rf"center_x\s*=\s*{num},\s*center_y\s*=\s*{num},\s*center_z\s*=\s*{num}", output
        )
        sm = re.search(
            rf"size_x\s*=\s*{num},\s*size_y\s*=\s*{num},\s*size_z\s*=\s*{num}", output
        )
        if cm and sm:
            cx, cy, cz = float(cm.group(1)), float(cm.group(2)), float(cm.group(3))
            sx, sy, sz = float(sm.group(1)), float(sm.group(2)), float(sm.group(3))

    if None in (cx, cy, cz, sx, sy, sz):
        return failed(errors=[f"Cannot parse box parameters from PyMOL output:\n{output}"])

    return success(data={
        "center_x": float(cx), "center_y": float(cy), "center_z": float(cz),
        "size_x": float(sx), "size_y": float(sy), "size_z": float(sz),
    })


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
    use_getbox: bool = True,
    exhaustiveness: int = 8,
    num_modes: int = 9,
    energy_range: float = 3.0,
) -> ToolResult:
    """Run AutoDock Vina docking (PDBQT input only)."""
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not is_tool_available("vina"):
        return failed(
            errors=["AutoDock Vina (vina) not found on PATH or in LangGraph conda env"],
            env_packages=["vina"],
        )

    protein_path = Path(protein_file).resolve()
    ligand_path = Path(ligand_file).resolve()

    if not protein_path.exists():
        return failed(errors=[f"Protein file not found: {protein_path}"])
    if not ligand_path.exists():
        return failed(errors=[f"Ligand file not found: {ligand_path}"])
    if protein_path.suffix.lower() != ".pdbqt":
        return failed(errors=[f"Protein file must be PDBQT format: {protein_path}"])
    if ligand_path.suffix.lower() != ".pdbqt":
        return failed(errors=[f"Ligand file must be PDBQT format: {ligand_path}"])

    if center_x is None or center_y is None or center_z is None:
        r = get_docking_box_from_p2rank(protein_path, out_dir)
        if not r.ok:
            return failed(errors=[f"Auto box center failed: {r.errors}"])
        center_x = r.data["center_x"]
        center_y = r.data["center_y"]
        center_z = r.data["center_z"]
        if use_getbox:
            size_x = r.data.get("size_x", size_x)
            size_y = r.data.get("size_y", size_y)
            size_z = r.data.get("size_z", size_z)

    with tempfile.TemporaryDirectory(prefix="vina_", dir=out_dir) as tmpdir:
        work_dir = Path(tmpdir)
        config_file = work_dir / "config.txt"
        out_pdbqt = out_dir / "docked.pdbqt"

        with open(config_file, "w") as f:
            f.write(
                f"receptor = {protein_path}\n"
                f"ligand = {ligand_path}\n"
                f"center_x = {center_x:.3f}\n"
                f"center_y = {center_y:.3f}\n"
                f"center_z = {center_z:.3f}\n"
                f"size_x = {size_x:.1f}\n"
                f"size_y = {size_y:.1f}\n"
                f"size_z = {size_z:.1f}\n"
                f"exhaustiveness = {exhaustiveness}\n"
                f"num_modes = {num_modes}\n"
                f"energy_range = {energy_range}\n"
                f"out = {out_pdbqt}\n"
            )

        try:
            result = run_in_conda_env(
                ["vina", "--config", str(config_file)],
                timeout=600,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                return failed(errors=[f"Vina docking failed (exit {result.returncode}): {err}"])
        except subprocess.TimeoutExpired:
            return failed(errors=["Vina docking timed out (>600s)"])

        best_energy = None
        for line in result.stdout.splitlines():
            if "REMARK VINA RESULT:" in line:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        best_energy = float(parts[3])
                    except ValueError:
                        pass
                break
        if best_energy is None:
            for line in reversed(result.stdout.splitlines()):
                if "kcal/mol" in line and line.strip().startswith("1"):
                    try:
                        best_energy = float(line.split()[1])
                    except ValueError:
                        pass

        out_pdb = out_dir / "docked.pdb"
        obabel_err = None
        if is_tool_available("obabel"):
            try:
                r = run_in_conda_env(
                    ["obabel", "-ipdbqt", str(out_pdbqt), "-opdb", "-O", str(out_pdb)],
                    timeout=30,
                )
                if r.returncode != 0:
                    obabel_err = r.stderr.strip() or "obabel returncode != 0"
            except subprocess.TimeoutExpired:
                obabel_err = "obabel timeout (>30s)"
            except Exception as e:
                obabel_err = f"obabel exception: {e}"
        else:
            obabel_err = "obabel not installed"

        if obabel_err or not out_pdb.exists() or out_pdb.stat().st_size < 50:
            tool_debug(f"[dock] obabel PDBQT→PDB failed: {obabel_err}; "
                       f"out_pdb exists={out_pdb.exists()}, "
                       f"size={out_pdb.stat().st_size if out_pdb.exists() else 0}")
            # Delete the bad file so callers see output_pdb=None
            if out_pdb.exists():
                try:
                    out_pdb.unlink()
                except OSError:
                    pass

        energy_str = f"{best_energy:.2f}" if best_energy is not None else "N/A"
        return success(data={
            "best_energy_kcal_per_mol": best_energy,
            "output_pdbqt": str(out_pdbqt.absolute()),
            "output_pdb": str(out_pdb.absolute()) if out_pdb.exists() else None,
            "center": (center_x, center_y, center_z),
            "size": (size_x, size_y, size_z),
            "summary": (
                f"Docking complete. Best binding energy: {energy_str} kcal/mol. "
                f"Result: {out_pdbqt.absolute()}. "
                f"Box center: ({center_x:.2f}, {center_y:.2f}, {center_z:.2f}). "
                f"Box size: {size_x:.1f} x {size_y:.1f} x {size_z:.1f} A^3"
            ),
        })


if __name__ == "__main__":
    r = get_docking_box_from_p2rank(
        protein_pdb=project_root / "output" / "protein_preparation" / "1IEP.pdb",
        output_dir=project_root / "output" / "p2rank",
        use_getbox=False,
    )
    print(r.format_for_agent())
