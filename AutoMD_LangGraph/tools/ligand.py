#!/usr/bin/env python3
"""
Ligand parameterization tools (Antechamber route).

From SMILES or structure files, generate GAFF forcefield parameters,
AM1-BCC/RESP charges (mol2), PDBQT for docking, and AMBER topology/coordinates for MD.

Dependencies: AmberTools (antechamber, parmchk2, tleap), Open Babel, RDKit.
All public functions return ToolResult.
"""

import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from tools.protein import _detect_disulfide_bonds
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
    tool_debug,
    is_tool_available,
    run_in_conda_env,
)


def _ensure_temp_subdir(*parts: str) -> Path:
    path = TEMP_ROOT.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_residue_name(pdb_path: str, resname: str = "LIG") -> None:
    """🔧 替换 RDKit/obabel/PubChem 默认的 'UNL' 残基名为正确的 ligand 残基名 (默认 LIG).

    RDKit 的 MolToPDBFile、obabel --gen3d、PubChem SDF→PDB 都会把未知残基写成 'UNL'。
    'UNL' 残基 tleap 识别不了, 报 'Atom .R<UNL 497>.A<C3 16> does not have a type' FATAL。
    下游 antechamber 用 -rn LIG, 但 docked.pdb 是从 Vina→obabel 来的, 残基名沿用 'UNL' 没改。
    必须在 conformer PDB 写完后立刻把 UNL 替换掉, 让整条管线 (Vina→obabel→tleap) 残基名一致。
    """
    if not resname or not os.path.exists(pdb_path):
        return
    try:
        with open(pdb_path, "r") as f:
            content = f.read()
        # PDB 残基名在列 18-20 (3 字符宽), 用列对齐的 sed 替换更安全
        # 但 " UNL " (前后空格) 在标准 PDB ATOM/HETATM 行里总是匹配, 简单够用
        new_content = re.sub(r' UNL ', f' {resname:<3s} ', content)
        if new_content != content:
            with open(pdb_path, "w") as f:
                f.write(new_content)
    except Exception:
        pass


# ============================================================================
# 1. SMILES -> 3D structure (multi-tier degradation)
# ============================================================================

# ── Validation helpers ──────────────────────────────────────────────────────

def _count_heavy_atoms_from_smiles(smiles: str) -> int:
    """Count heavy (non-hydrogen) atoms from SMILES using RDKit. Returns -1 on failure."""
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return -1
        return mol.GetNumHeavyAtoms()
    except Exception:
        return -1


def _count_heavy_atoms_in_pdb(pdb_path: str) -> int:
    """Count non-hydrogen atoms in a PDB file. Returns -1 on failure."""
    heavy_atoms = {"C", "N", "O", "S", "P", "F", "CL", "BR", "I", "SE", "B", "SI"}
    count = 0
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith(("ATOM  ", "HETATM")):
                    elem = line[76:78].strip().upper()
                    if not elem:
                        aname = line[12:16].strip()
                        if aname:
                            c = aname[0].upper()
                            if c in heavy_atoms:
                                elem = c
                    if elem in heavy_atoms:
                        count += 1
    except Exception:
        return -1
    return count


def _validate_pdb_structure(pdb_path: str, smiles: str) -> tuple:
    """Validate a generated PDB against expected SMILES. Returns (is_valid, reason)."""
    if not os.path.exists(pdb_path):
        return False, "PDB file does not exist"

    fsize = os.path.getsize(pdb_path)
    if fsize == 0:
        return False, "PDB file is empty"
    if fsize < 50:
        return False, f"PDB file too small ({fsize} bytes)"

    expected_heavy = _count_heavy_atoms_from_smiles(smiles)
    actual_heavy = _count_heavy_atoms_in_pdb(pdb_path)

    if actual_heavy <= 0:
        return False, "No heavy atoms found in PDB"

    if expected_heavy > 0:
        if actual_heavy < expected_heavy * 0.7:
            return False, f"Too few heavy atoms: expected ~{expected_heavy}, got {actual_heavy}"
        if actual_heavy > expected_heavy * 1.5:
            return False, f"Too many heavy atoms: expected ~{expected_heavy}, got {actual_heavy}"

    # Check that coordinates are not all collapsed
    try:
        with open(pdb_path, "r") as f:
            xs, ys, zs = [], [], []
            for line in f:
                if line.startswith(("ATOM  ", "HETATM")):
                    try:
                        xs.append(float(line[30:38]))
                        ys.append(float(line[38:46]))
                        zs.append(float(line[46:54]))
                    except ValueError:
                        pass
            if not xs:
                return False, "No coordinate data in PDB"
            dx = max(xs) - min(xs)
            dy = max(ys) - min(ys)
            dz = max(zs) - min(zs)
            if dx < 0.001 and dy < 0.001 and dz < 0.001:
                return False, "All coordinates are identical (point)"
            if dx < 0.001 and dy < 0.001:
                return False, "Coordinates collapsed to a line in Z"
    except Exception:
        pass  # coordinate check is best-effort

    return True, f"Valid: {actual_heavy} heavy atoms, span {dx:.1f}x{dy:.1f}x{dz:.1f}"


# ── Tier 1: RDKit progressive ───────────────────────────────────────────────

def _generate_alt_smiles(smiles: str) -> list:
    """Generate alternative SMILES representations for problematic aromatics."""
    alternatives = [smiles]
    try:
        from rdkit import Chem
        alternatives.append(smiles.replace("[nH]", "[NH]"))
        alternatives.append(smiles.replace("[nH]", "N"))
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
        if mol is not None:
            try:
                Chem.SanitizeMol(mol, Chem.SANITIZE_ALL ^ Chem.SANITIZE_KEKULIZE)
                kek = Chem.MolToSmiles(mol, kekuleSmiles=True)
                if kek and kek != smiles:
                    alternatives.append(kek)
            except Exception:
                pass
    except ImportError:
        pass
    seen = set()
    return [s for s in alternatives if not (s in seen or seen.add(s))]


def _try_embed_and_write(mol, output_pdb: str, add_hydrogens: bool,
                         smiles: str, use_etkdg: bool = False) -> ToolResult:
    """Embed a molecule in 3D, optimize, write PDB, and validate."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.RWMol(mol)
    if add_hydrogens:
        mol = Chem.AddHs(mol, addCoords=True)

    # Embed
    embedded = False
    if use_etkdg:
        for params_cls in [AllChem.ETKDGv3, AllChem.ETKDGv2, AllChem.ETKDG]:
            try:
                if AllChem.EmbedMolecule(mol, params_cls()) == 0:
                    embedded = True
                    break
            except Exception:
                continue
    if not embedded:
        for seed in [42, 123, 456, 789]:
            try:
                if AllChem.EmbedMolecule(mol, randomSeed=seed) == 0:
                    embedded = True
                    break
            except Exception:
                continue
    if not embedded:
        return failed(errors=["All EmbedMolecule attempts failed"], degradation=["embed failed"])

    # Optimize: MMFF → UFF → raw coords
    try:
        if AllChem.MMFFOptimizeMolecule(mol) != 0:
            raise ValueError("MMFF returned non-zero")
    except Exception:
        try:
            AllChem.UFFOptimizeMolecule(mol)
        except Exception:
            pass

    Chem.MolToPDBFile(mol, output_pdb)

    valid, reason = _validate_pdb_structure(output_pdb, smiles)
    if not valid:
        return failed(errors=[reason], degradation=[reason])

    return success(data=str(Path(output_pdb).resolve()))


def _smiles_to_pdb_rdkit(smiles: str, output_pdb: str,
                         add_hydrogens: bool) -> ToolResult:
    """Tier 1: RDKit with progressive sanitization and force-field fallback."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        return failed(errors=["RDKit not installed"], env_packages=["rdkit"])

    # Suppress RDKit C++ stderr noise during degradation attempts
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    os.dup2(devnull, 2)
    try:
        degradations: list[str] = []

        # 1a: Standard full sanitization
        mol = Chem.MolFromSmiles(smiles, sanitize=True)
        if mol is not None and mol.GetNumAtoms() > 0:
            r = _try_embed_and_write(mol, output_pdb, add_hydrogens, smiles)
            if r.ok:
                tool_debug("[_smiles_to_pdb_rdkit] 1a: standard sanitize + embed")
                return r
            degradations.append(f"1a: standard parse + embed failed: {r.errors}")
        else:
            degradations.append("1a: MolFromSmiles(sanitize=True) returned None/invalid")

        # 1b: Skip kekulization
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
        if mol is not None and mol.GetNumAtoms() > 0:
            try:
                Chem.SanitizeMol(mol, Chem.SANITIZE_ALL ^ Chem.SANITIZE_KEKULIZE)
            except Exception as e:
                degradations.append(f"1b: partial sanitize warning: {e}")
            n_atoms = mol.GetNumAtoms()
            degradations.append(f"1b: parsed {n_atoms} atoms with sanitize=False")
            if n_atoms >= 3:
                r = _try_embed_and_write(mol, output_pdb, add_hydrogens, smiles, use_etkdg=True)
                if r.ok:
                    tool_debug("[_smiles_to_pdb_rdkit] 1b: skip-kekulize + ETKDG")
                    r.degradation = degradations + r.degradation
                    return r
                degradations.append(f"1b: embed/optimize failed: {r.errors}")
        else:
            degradations.append("1b: MolFromSmiles(sanitize=False) returned None")

        # 1c: Alternative SMILES representations
        alt_list = _generate_alt_smiles(smiles)
        for i, alt_smi in enumerate(alt_list):
            if alt_smi == smiles:
                continue
            mol = Chem.MolFromSmiles(alt_smi, sanitize=False)
            if mol is None or mol.GetNumAtoms() < 3:
                continue
            try:
                Chem.SanitizeMol(mol, Chem.SANITIZE_ALL ^ Chem.SANITIZE_KEKULIZE)
            except Exception:
                pass
            if mol.GetNumAtoms() < 3:
                continue
            r = _try_embed_and_write(mol, output_pdb, add_hydrogens, smiles, use_etkdg=True)
            if r.ok:
                tool_debug(f"[_smiles_to_pdb_rdkit] 1c: alt SMILES #{i+1}")
                r.degradation = degradations + [f"1c: alt SMILES #{i+1} '{alt_smi[:40]}...'"] + r.degradation
                return r
        degradations.append(f"1c: {len(alt_list)} alt SMILES tried, none succeeded")

        return failed(errors=degradations, degradation=degradations)
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)
        os.close(devnull)


# ── Tier 2: OpenBabel ────────────────────────────────────────────────────────

def _smiles_to_pdb_obabel(smiles: str, output_pdb: str) -> ToolResult:
    """Tier 2: OpenBabel SMILES -> 3D PDB (two routes)."""
    if not is_tool_available("obabel"):
        return failed(errors=["obabel not found on PATH or in LangGraph conda env"], env_packages=["openbabel"])

    degradations: list[str] = []

    # 2a: Direct SMILES -> PDB with --gen3d
    cmd = ["obabel", f"-:{smiles}", "-O", output_pdb, "--gen3d"]
    result = run_in_conda_env(cmd)
    if result.returncode == 0:
        valid, reason = _validate_pdb_structure(output_pdb, smiles)
        if valid:
            tool_debug("[_smiles_to_pdb_obabel] 2a: direct SMILES→PDB with --gen3d")
            return success(data=str(Path(output_pdb).resolve()))
        degradations.append(f"2a: obabel PDB validation failed: {reason}")
    else:
        degradations.append(f"2a: obabel failed: {result.stderr.strip()[:200]}")

    # 2b: Via SDF intermediate (sometimes avoids PDB writer issues)
    sdf_tmp = str(Path(output_pdb).with_suffix(".obabel.sdf"))
    cmd2 = ["obabel", f"-:{smiles}", "-O", sdf_tmp, "--gen3d"]
    r2 = run_in_conda_env(cmd2)
    if r2.returncode == 0 and os.path.exists(sdf_tmp) and os.path.getsize(sdf_tmp) > 50:
        cmd3 = ["obabel", "-isdf", sdf_tmp, "-opdb", "-O", output_pdb]
        r3 = run_in_conda_env(cmd3)
        try:
            os.unlink(sdf_tmp)
        except OSError:
            pass
        if r3.returncode == 0:
            valid, reason = _validate_pdb_structure(output_pdb, smiles)
            if valid:
                tool_debug("[_smiles_to_pdb_obabel] 2b: SMILES→SDF→PDB")
                return degraded(
                    data=str(Path(output_pdb).resolve()),
                    degradation=["2b: obabel SMILES->SDF->PDB"],
                    errors=degradations,
                )
            degradations.append(f"2b: SDF route validation failed: {reason}")
        else:
            degradations.append(f"2b: SDF->PDB failed: {r3.stderr.strip()[:200]}")
    else:
        degradations.append("2b: SDF generation empty/failed")

    return failed(errors=degradations, degradation=degradations)


# ── Tier 3: PubChem REST API ─────────────────────────────────────────────────

def _smiles_to_pdb_pubchem(smiles: str, output_pdb: str,
                           pdb_id: str = "") -> ToolResult:
    """Tier 3: Download 3D structure from PubChem by SMILES or PDB ID lookup."""
    if not is_tool_available("obabel"):
        return failed(errors=["PubChem route requires obabel for SDF->PDB (not found on PATH or in LangGraph conda env)"],
                      env_packages=["openbabel"])

    degradations: list[str] = []

    def _pc_get(url: str, timeout: int = 30) -> str | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AutoMD/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception as e:
            degradations.append(f"PubChem request failed: {e}")
            return None

    cid: str | None = None

    # 3a: Search by SMILES
    smiles_enc = urllib.parse.quote(smiles, safe="")
    cid_text = _pc_get(
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{smiles_enc}/cids/TXT"
    )
    if cid_text:
        cids = [line.strip() for line in cid_text.splitlines() if line.strip().isdigit()]
        if cids:
            cid = cids[0]
            degradations.append(f"3a: PubChem CID {cid} via SMILES search")

    # 3b: PDB ID fallback lookup
    if not cid and pdb_id:
        pdb_text = _pc_get(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastidentity/pdbid/{pdb_id}/cids/TXT"
        )
        if pdb_text:
            pdb_cids = [line.strip() for line in pdb_text.splitlines() if line.strip().isdigit()]
            if pdb_cids:
                cid = pdb_cids[0]
                degradations.append(f"3b: PubChem CID {cid} via PDB ID {pdb_id}")

    if not cid:
        return failed(errors=degradations + ["Could not find PubChem CID"],
                      degradation=degradations)

    # 3c: Download 3D SDF and convert to PDB
    sdf_text = _pc_get(
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/SDF?record_type=3d",
        timeout=60,
    )
    if not sdf_text or len(sdf_text) < 50:
        return failed(
            errors=degradations + [f"PubChem 3D SDF empty for CID {cid}"],
            degradation=degradations,
        )

    sdf_tmp = str(Path(output_pdb).with_suffix(".pubchem.sdf"))
    with open(sdf_tmp, "w") as f:
        f.write(sdf_text)

    result = subprocess.run(
        ["obabel", "-isdf", sdf_tmp, "-opdb", "-O", output_pdb],
        capture_output=True, text=True,
    )
    try:
        os.unlink(sdf_tmp)
    except OSError:
        pass

    if result.returncode != 0:
        return failed(
            errors=degradations + [f"obabel SDF->PDB failed: {result.stderr.strip()[:200]}"],
            degradation=degradations,
        )

    valid, reason = _validate_pdb_structure(output_pdb, smiles)
    if not valid:
        return failed(
            errors=degradations + [f"PubChem PDB validation failed: {reason}"],
            degradation=degradations,
        )

    return degraded(
        data=str(Path(output_pdb).resolve()),
        degradation=degradations + [f"3c: PubChem CID {cid} 3D SDF -> PDB"],
        errors=["RDKit and OpenBabel failed, used PubChem fallback"],
    )


# ── Tier 4/5: PDB HETATM extraction ──────────────────────────────────────────

_NON_LIGAND_RESIDUES = {
    "HOH", "WAT", "TIP", "TIP3", "SOL",
    "NA", "CL", "K", "CA", "MG", "ZN", "FE", "MN", "CO", "CD", "HG",
    "NA+", "CL-", "K+", "CA2+", "MG2+", "ZN2+",
    "SO4", "PO4", "GOL", "EDO", "PEG", "ACT", "DMS", "MPD", "BME", "DTT", "TCEP",
    "TRS", "HEPES", "MES", "PIPES", "CHES", "CAPS", "TRIS", "CIT", "ACY",
    "EOH", "MOH", "IPA", "DMSO", "DMF", "UREA", "GAI", "BTB",
    "NO3", "NH4", "BR", "LI", "RB", "CS", "BA", "SR", "F", "I", "IOD",
}

_STANDARD_AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HID", "HIE", "HIP", "CYX", "ASH", "GLH", "LYN", "ACE", "NME",
}


def _extract_hetatm_from_pdb(pdb_path: str, output_pdb: str,
                             smiles: str = "",
                             ligand_resname: str = "") -> ToolResult:
    """Tier 4/5: Extract ligand HETATM records from a protein PDB file."""
    if not pdb_path or not os.path.exists(pdb_path):
        return failed(errors=[f"Protein PDB not found: {pdb_path}"])

    # Parse HETATM records grouped by residue name
    residues: dict[str, list[str]] = {}
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith("HETATM"):
                    rname = line[17:20].strip()
                    residues.setdefault(rname, []).append(line)
                elif line.startswith("ATOM  "):
                    rname = line[17:20].strip()
                    if rname not in _STANDARD_AA:
                        residues.setdefault(rname, []).append(line)
    except Exception as e:
        return failed(errors=[f"Failed to read PDB: {e}"])

    if not residues:
        return failed(errors=["No HETATM/non-standard records found in protein PDB"])

    # Filter out solvent/ions/buffer
    ligand_residues = {k: v for k, v in residues.items() if k not in _NON_LIGAND_RESIDUES}
    if not ligand_residues:
        ligand_residues = residues  # use all if everything filtered out

    # Select best candidate
    selected_name = ""
    selected_lines: list[str] = []

    if ligand_resname and ligand_resname in ligand_residues:
        selected_name = ligand_resname
        selected_lines = ligand_residues[ligand_resname]
    else:
        # Pick residue with most heavy atoms (most likely the ligand)
        best_count = 0
        for name, lines in ligand_residues.items():
            heavy = sum(
                1 for l in lines
                if l[76:78].strip().upper() in
                {"C", "N", "O", "S", "P", "F", "CL", "BR", "I", "SE", "B", "SI"}
            )
            if heavy > best_count:
                best_count = heavy
                selected_name = name
                selected_lines = lines

    if not selected_lines:
        return failed(errors=["Could not identify any ligand HETATM records"])

    with open(output_pdb, "w") as f:
        f.writelines(selected_lines)

    if smiles:
        valid, reason = _validate_pdb_structure(output_pdb, smiles)
        if not valid:
            return failed(
                errors=[f"HETATM extraction validation: {reason} "
                        f"(residue={selected_name}, {len(selected_lines)} lines)"],
                degradation=[f"Tier4: extracted '{selected_name}' but validation failed"],
            )

    return degraded(
        data=str(Path(output_pdb).resolve()),
        degradation=[f"Tier4: extracted HETATM '{selected_name}' "
                      f"({len(selected_lines)} atoms) from protein PDB"],
        errors=["RDKit + OpenBabel failed, used PDB co-crystal extraction"],
    )


# ── Main orchestration ───────────────────────────────────────────────────────

def smiles_to_pdb(
    smiles: str,
    output_pdb: str,
    add_hydrogens: bool = True,
    protein_pdb_path: str | None = None,
    pdb_id: str = "",
    ligand_resname: str = "",
) -> ToolResult:
    """Convert SMILES to 3D PDB with 6-tier degradation strategy.

    Tiers:
      1. RDKit progressive (sanitize → skip-kekulize → alt SMILES → ETKDG → UFF)
      2. OpenBabel (direct --gen3d → via SDF intermediate)
      3. PubChem REST API (SMILES search → PDB ID search → 3D SDF → PDB)
      4. Local protein PDB HETATM extraction (co-crystal ligand)
      5. RCSB PDB download + HETATM extraction
      6. Return detailed failure diagnostics
    """
    out_dir = os.path.dirname(output_pdb)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    all_degradations: list[str] = []
    all_errors: list[str] = []

    # ── Tier 1: RDKit ──
    r = _smiles_to_pdb_rdkit(smiles, output_pdb, add_hydrogens)
    if r.ok:
        _normalize_residue_name(r.data, ligand_resname or "LIG")
        tool_debug(f"[smiles_to_pdb] Tier 1: RDKit {'(degraded) ' if r.degradation else ''}→ {output_pdb}")
        if r.degradation:
            return degraded(data=r.data, degradation=r.degradation, errors=r.errors)
        return r
    all_errors.extend(r.errors)
    all_degradations.extend(r.degradation)

    # ── Tier 2: OpenBabel ──
    r = _smiles_to_pdb_obabel(smiles, output_pdb)
    if r.ok:
        _normalize_residue_name(r.data, ligand_resname or "LIG")
        tool_debug(f"[smiles_to_pdb] Tier 2: OpenBabel → {output_pdb}")
        return degraded(
            data=r.data,
            degradation=all_degradations + r.degradation,
            errors=all_errors + r.errors,
        )
    all_errors.extend(r.errors)
    all_degradations.extend(r.degradation)

    # ── Tier 3: PubChem ──
    r = _smiles_to_pdb_pubchem(smiles, output_pdb, pdb_id)
    if r.ok:
        _normalize_residue_name(r.data, ligand_resname or "LIG")
        tool_debug(f"[smiles_to_pdb] Tier 3: PubChem → {output_pdb}")
        return degraded(
            data=r.data,
            degradation=all_degradations + r.degradation,
            errors=all_errors + r.errors,
        )
    all_errors.extend(r.errors)
    all_degradations.extend(r.degradation)

    # ── Tier 4: Local protein PDB HETATM ──
    if protein_pdb_path:
        r = _extract_hetatm_from_pdb(protein_pdb_path, output_pdb, smiles, ligand_resname)
        if r.ok:
            tool_debug(f"[smiles_to_pdb] Tier 4: HETATM extraction → {output_pdb}")
            return degraded(
                data=r.data,
                degradation=all_degradations + r.degradation,
                errors=all_errors + r.errors,
            )
        all_errors.extend(r.errors)
        all_degradations.extend(r.degradation)

    # ── Tier 5: RCSB download + HETATM ──
    if pdb_id:
        rcsb_url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
        rcsb_tmp = str(Path(output_pdb).with_suffix(".rcsb.pdb"))
        try:
            req = urllib.request.Request(rcsb_url, headers={"User-Agent": "AutoMD/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(rcsb_tmp, "wb") as f:
                    f.write(resp.read())
            if os.path.getsize(rcsb_tmp) > 1000:
                r = _extract_hetatm_from_pdb(rcsb_tmp, output_pdb, smiles, ligand_resname)
                try:
                    os.unlink(rcsb_tmp)
                except OSError:
                    pass
                if r.ok:
                    tool_debug(f"[smiles_to_pdb] Tier 5: RCSB {pdb_id}.pdb → {output_pdb}")
                    return degraded(
                        data=r.data,
                        degradation=all_degradations + [f"Tier5: downloaded {pdb_id}.pdb from RCSB"] + r.degradation,
                        errors=all_errors + r.errors,
                    )
                all_errors.extend(r.errors)
                all_degradations.append("Tier5: RCSB download OK but HETATM extraction failed")
            else:
                all_degradations.append("Tier5: RCSB download returned too-small file")
        except Exception as e:
            all_degradations.append(f"Tier5: RCSB download failed: {e}")

    # ── Tier 6: Complete failure ──
    return failed(
        errors=all_errors + [
            f"All {len(all_degradations)} degradation attempts exhausted.",
            f"SMILES: {smiles}",
            "Suggestions: provide a 3D structure file, check PubChem, "
            "or extract ligand from co-crystal PDB.",
        ],
        degradation=all_degradations,
    )


# ============================================================================
# 2. Antechamber
# ============================================================================

def run_antechamber(
    input_file: str,
    output_mol2: str,
    input_format: str = "pdb",
    charge_method: str = "bcc",
    net_charge: int = 0,
    residue_name: str = "LIG",
    force_field: str = "gaff2",
    extra_args: Optional[List[str]] = None,
    intermediate_dir: Optional[str] = None,
    **kwargs,
) -> ToolResult:
    """Run antechamber to generate MOL2 with GAFF atom types and charges."""
    if not is_tool_available("antechamber"):
        return failed(
            errors=["antechamber command not found on PATH or in LangGraph conda env"],
            env_packages=["ambertools"],
        )

    input_abs = os.path.abspath(input_file)
    output_abs = os.path.abspath(output_mol2)

    run_cwd = os.path.abspath(intermediate_dir) if intermediate_dir else str(
        _ensure_temp_subdir("antechamber", "default")
    )
    os.makedirs(run_cwd, exist_ok=True)

    cmd = [
        "antechamber",
        "-i", input_abs, "-fi", input_format,
        "-o", output_abs, "-fo", "mol2",
        "-c", charge_method, "-nc", str(net_charge),
        "-rn", residue_name, "-at", force_field,
    ]
    if extra_args:
        cmd.extend(extra_args)
    for key, value in kwargs.items():
        cmd.append(f"-{key}")
        if value is not None:
            cmd.append(str(value))

    result = run_in_conda_env(cmd, cwd=run_cwd)
    if result.returncode != 0:
        return failed(errors=[result.stderr or result.stdout])
    return success(data=str(Path(output_abs).resolve()))


# ============================================================================
# 3. Parmchk2
# ============================================================================

def run_parmchk2(
    mol2_file: str,
    output_frcmod: str,
    force_field: str = "gaff2",
    work_dir: Optional[str] = None,
) -> ToolResult:
    """Run parmchk2 to generate frcmod supplementing missing GAFF parameters."""
    if not is_tool_available("parmchk2"):
        return failed(
            errors=["parmchk2 command not found on PATH or in LangGraph conda env"],
            env_packages=["ambertools"],
        )

    cmd = [
        "parmchk2", "-i", mol2_file,
        "-f", "mol2", "-o", output_frcmod,
        "-s", force_field,
    ]
    run_cwd = os.path.abspath(work_dir) if work_dir else str(_ensure_temp_subdir("parmchk2"))
    os.makedirs(run_cwd, exist_ok=True)

    result = run_in_conda_env(cmd, cwd=run_cwd)
    if result.returncode != 0:
        return failed(errors=[result.stderr.strip()])

    with open(output_frcmod, "r") as f:
        content = f.read()
        if "ATTN, need revision" in content or "MISSING" in content:
            return degraded(
                data=str(Path(output_frcmod).resolve()),
                warnings=["frcmod contains missing parameters, may need manual revision"],
            )

    return success(data=str(Path(output_frcmod).resolve()))


# ============================================================================
# 4. MOL2 bond deduplication (internal)
# ============================================================================

def _deduplicate_mol2_bonds(input_mol2: str, output_mol2: str) -> bool:
    """Deduplicate MOL2 BOND records to avoid tleap crashes on duplicate bonds."""
    try:
        with open(input_mol2, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        bond_start = None
        counts_line = None

        for i, line in enumerate(lines):
            tag = line.strip().upper()
            if tag == "@<TRIPOS>MOLECULE":
                counts_line = i + 2
            elif tag == "@<TRIPOS>BOND":
                bond_start = i + 1
            elif bond_start is not None and line.startswith("@<TRIPOS>") and i > bond_start:
                bond_end = i
                break
        else:
            if bond_start is not None:
                bond_end = len(lines)

        if bond_start is None:
            shutil.copyfile(input_mol2, output_mol2)
            return True

        seen_pairs = set()
        kept_bonds = []
        for raw in lines[bond_start:bond_end]:
            parts = raw.split()
            if len(parts) < 4:
                continue
            a1, a2 = parts[1], parts[2]
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
            new_bond_lines.append(" ".join(parts) + "\n")

        if counts_line is not None and counts_line < len(lines):
            counts_parts = lines[counts_line].split()
            if len(counts_parts) >= 2:
                counts_parts[1] = str(len(new_bond_lines))
                lines[counts_line] = " ".join(counts_parts) + "\n"

        new_content = lines[:bond_start] + new_bond_lines + lines[bond_end:]
        with open(output_mol2, "w", encoding="utf-8") as f:
            f.writelines(new_content)
        return True
    except Exception as exc:
        print(f"MOL2 bond deduplication failed: {exc}")
        return False


# ============================================================================
# 5. Ligand tleap
# ============================================================================

def run_tleap(
    mol2_file: str,
    frcmod_file: Optional[str],
    output_prmtop: str,
    output_inpcrd: str,
    residue_name: str = "LIG",
    protein_pdb: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> ToolResult:
    """Generate AMBER topology/coordinates for ligand via tLEaP."""
    if not is_tool_available("tleap"):
        return failed(
            errors=["tleap command not found on PATH or in LangGraph conda env"],
            env_packages=["ambertools"],
        )

    mol2_abs = os.path.abspath(mol2_file)
    frcmod_abs = os.path.abspath(frcmod_file) if frcmod_file else None
    prmtop_abs = os.path.abspath(output_prmtop)
    inpcrd_abs = os.path.abspath(output_inpcrd)
    protein_abs = os.path.abspath(protein_pdb) if protein_pdb else None
    run_cwd = os.path.abspath(work_dir) if work_dir else str(_ensure_temp_subdir("tleap"))
    os.makedirs(run_cwd, exist_ok=True)

    leap_lines = []
    if protein_abs and os.path.exists(protein_abs):
        leap_lines.append("source leaprc.protein.ff19SB")
    leap_lines.append("source leaprc.gaff2")

    if frcmod_abs and os.path.exists(frcmod_abs):
        leap_lines.append(f"loadamberparams {frcmod_abs}")

    leap_lines.append(f"LIG = loadmol2 {mol2_abs}")

    if protein_abs and os.path.exists(protein_abs):
        leap_lines.append(f"PRO = loadpdb {protein_abs}")
        leap_lines.append("COM = combine { PRO LIG }")
        leap_lines.append(f"saveamberparm COM {prmtop_abs} {inpcrd_abs}")
    else:
        leap_lines.append(f"saveamberparm LIG {prmtop_abs} {inpcrd_abs}")

    leap_lines.append("quit")

    def _run(script_lines: List[str]) -> Tuple[bool, str, str]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".in", delete=False, dir=run_cwd) as f:
            f.write("\n".join(script_lines))
            script_file = f.name
        cmd = ["tleap", "-f", script_file, "-s"]
        result = run_in_conda_env(cmd, cwd=run_cwd)
        os.unlink(script_file)
        out = result.stdout or ""
        err = result.stderr or ""
        failed_flag = (
            result.returncode != 0
            or "fatal error" in out.lower()
            or "!fatal" in out.lower()
            or re.search(r"Exiting LEaP:\s+Errors\s*=\s*[1-9]\d*", out) is not None
        )
        return (not failed_flag), out, err

    ok, stdout_text, stderr_text = _run(leap_lines)
    if not ok:
        failure_text = f"{stdout_text}\n{stderr_text}".lower()
        if "cannot add bond" in failure_text or "duplicate bond" in failure_text:
            dedup_mol2 = str(Path(prmtop_abs).with_name(f"{Path(mol2_abs).stem}_dedup.mol2"))
            if _deduplicate_mol2_bonds(mol2_abs, dedup_mol2):
                retry_lines = [
                    ln if not ln.startswith("LIG = loadmol2 ") else f"LIG = loadmol2 {dedup_mol2}"
                    for ln in leap_lines
                ]
                ok, stdout_text, stderr_text = _run(retry_lines)
        if not ok:
            return failed(errors=[f"tLEaP failed:\n{stdout_text}\n{stderr_text}"])

    if not os.path.exists(output_prmtop) or not os.path.exists(output_inpcrd):
        return failed(errors=["tLEaP succeeded but target files not found"])

    return success(data={
        "prmtop": output_prmtop,
        "inpcrd": output_inpcrd,
    })


# ============================================================================
# 5b. Complex tleap (protein + docked ligand)
# ============================================================================

def run_complex_tleap(
    protein_pdb: str,
    docked_ligand_pdb: str,
    ligand_frcmod: str,
    output_dir: str,
    box_padding: float = 10.0,
    neutralize: bool = True,
    fallback_mol2: str = "",
    ligand_smiles: str = "",
    force_field: str = "ff19SB",
    water_model: str = "tip3p",
    ligand_ff: str = "gaff2",
) -> ToolResult:
    """Build solvated protein-ligand complex via tLEaP using docked ligand pose.

    Pipeline (rewritten 2026-06-04):
        1. Extract MODEL 1 from Vina's multi-model docked.pdb → docked_single.pdb
        2. 用 _rebuild_ligand_with_docked_pose (RDKit) 或 _update_mol2_coordinates
           把 docked 坐标 + mol2 GAFF 类型合并 → ligand_docked.mol2
        3. tleap: LIG = loadmol2 ligand_docked.mol2  (一次性拿到 type + docked 坐标)
                  PRO = loadpdb protein.pdb
                  combine + solvate + ions + saveamberparm

    设计原则: tleap 的 `LIG = loadpdb docked.pdb` 不会复用 `loadmol2` 的 type, 必然 FATAL.
    唯一可靠的方案是把 docked 坐标写进 mol2, 然后只 loadmol2.
    """
    # ── 顶层 try/except: 任何内部异常转成 failed ToolResult, 不让异常冒到 langgraph stream
    try:
        if not is_tool_available("tleap"):
            return failed(
                errors=["tleap command not found on PATH or in LangGraph conda env"],
                env_packages=["ambertools"],
            )

        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        protein_abs = os.path.abspath(protein_pdb)
        docked_abs = os.path.abspath(docked_ligand_pdb)

        # ── 1. Extract first MODEL if docked PDB is multi-model (Vina outputs 9 models)
        try:
            with open(docked_abs, "r") as f:
                first_1k = f.read(2048)
            if "MODEL" in first_1k:
                single = str(out / "docked_single.pdb")
                run_in_conda_env(
                    ["bash", "-lc", f"awk '/^MODEL *1$/,/^ENDMDL/' {docked_abs} | grep -v '^ENDMDL' > {single}"],
                    timeout=10,
                )
                if os.path.getsize(single) > 100:
                    # 🔧 补 TER/END 记录, 否则 tleap 解析残基边界失败
                    with open(single, "a") as f:
                        f.write("TER\nEND\n")
                    docked_abs = single
        except Exception as e:
            tool_debug(f"[run_complex_tleap] MODEL 提取异常, 沿用原 docked_abs: {e}")

        frcmod_abs = os.path.abspath(ligand_frcmod) if ligand_frcmod else ""
        prmtop_out = str(out / "complex.prmtop")
        inpcrd_out = str(out / "complex.inpcrd")
        docked_mol2 = str(out / "ligand_docked.mol2")

        # ── 2. 提前生成 ligand_docked.mol2 (docked 坐标 + GAFF type) ──
        # 这是 PRIMARY 能一次成功的关键: tleap 只 loadmol2 一次, 同时拿到 type 和 docked 坐标.
        # 不用 `LIG = loadpdb` 那条注定失败的路径.
        rebuilt = False
        if fallback_mol2 and os.path.exists(fallback_mol2):
            if ligand_smiles:
                rebuilt = _rebuild_ligand_with_docked_pose(
                    ligand_smiles, fallback_mol2, docked_abs, docked_mol2,
                )
                if rebuilt:
                    tool_debug("[run_complex_tleap] docked_mol2 用 RDKit rebuild 生成")
            if not rebuilt:
                # 降级: 旧 name-matching 路径 (RDKit 失败时)
                rebuilt = _update_mol2_coordinates(
                    fallback_mol2, docked_abs, docked_mol2,
                )
                if rebuilt:
                    tool_debug("[run_complex_tleap] docked_mol2 用 name-matching 生成 (RDKit 降级)")

            if not rebuilt:
                return failed(errors=[
                    "无法生成 ligand_docked.mol2: _rebuild_ligand_with_docked_pose 和 "
                    "_update_mol2_coordinates 都失败 (无法匹配 docked PDB 与 mol2 原子名)"
                ])

        def _build_leap_script(lig_mol2_path: str) -> str:
            lines = [
                f"source leaprc.protein.{force_field}",
                f"source leaprc.{ligand_ff}",
                f"source leaprc.water.{water_model}",
            ]
            if frcmod_abs and os.path.exists(frcmod_abs):
                lines.append(f"loadamberparams {frcmod_abs}")
            # 🔧 关键: LIG = loadmol2 一次性拿到 type + 坐标
            # 不要用 `LIG = loadpdb docked.pdb`, tleap 不会复用 loadmol2 的 type, 会建空 unit 然后 FATAL
            lines.append(f"LIG = loadmol2 {lig_mol2_path}")
            lines.append(f"PRO = loadpdb {protein_abs}")
            # Disulfide bonds from CYX SG pairs
            ss_bonds = _detect_disulfide_bonds(protein_abs)
            for r1, r2 in ss_bonds:
                lines.append(f"bond PRO.{r1}.SG PRO.{r2}.SG")
            lines.append("COM = combine { PRO LIG }")
            _wbox = f"{water_model.upper()}BOX" if water_model.lower() in ("tip3p", "tip4p", "tip4pew", "opc", "spce") else "TIP3PBOX"
            lines.append(f"solvatebox COM {_wbox} {box_padding}")
            if neutralize:
                lines.append("addions COM Na+ 0")
            lines.append(f"saveamberparm COM {prmtop_out} {inpcrd_out}")
            lines.append("quit")
            return "\n".join(lines)

        def _run(script: str) -> tuple:
            """用 Popen + 显式 close pipe FDs, 防止 pipe 在 generator pause 时不释放."""
            script_file = out / "tleap.in"
            script_file.write_text(script)
            proc = subprocess.Popen(
                ["tleap", "-f", str(script_file), "-s"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                cwd=str(out),
            )
            try:
                stdout, stderr = proc.communicate(timeout=600)  # 10 分钟上限
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                return False, "", "tleap 超时 (10 分钟)"
            finally:
                # 🔧 显式 close pipe FDs, 避免 generator 帧持有引用导致 pipe 不被 GC
                for fd in (proc.stdout, proc.stderr):
                    try:
                        fd.close()
                    except Exception:
                        pass
            stdout = stdout or ""
            stderr = stderr or ""
            ok = (
                proc.returncode == 0
                and "fatal error" not in stdout.lower()
                and "!fatal" not in stdout.lower()
                and re.search(r"Exiting LEaP:\s+Errors\s*=\s*[1-9]\d*", stdout) is None
            )
            return ok, stdout, stderr

        # ── 3. PRIMARY: loadmol2 一次性拿 type + docked 坐标 ──
        script = _build_leap_script(docked_mol2)
        ok, stdout, stderr = _run(script)
        if ok:
            tool_debug("[run_complex_tleap] Primary loadmol2 succeeded")

        if not ok:
            return failed(errors=[f"Complex tLEaP failed:\n{stdout}\n{stderr}"])

        if not os.path.exists(prmtop_out) or not os.path.exists(inpcrd_out):
            return failed(errors=["tLEaP succeeded but complex.prmtop/inpcrd not found"])

        return success(data={
            "prmtop": prmtop_out,
            "inpcrd": inpcrd_out,
        })
    except Exception as e:
        # 🔧 兜底: 把任何未预期异常转成 failed ToolResult, 不让异常冒到 langgraph stream
        import traceback as _tb
        return failed(
            errors=[f"run_complex_tleap 内部异常 ({type(e).__name__}): {e}\n{_tb.format_exc()}"],
            degradation=["internal_exception_in_run_complex_tleap"],
        )


def _update_mol2_coordinates(mol2_path: str, pdb_path: str, output_path: str) -> bool:
    """Replace mol2 atom coordinates with those from a PDB file.

    Atoms are matched by name. Returns True on success.
    """
    try:
        pdb_coords: dict[str, tuple] = {}
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith(("ATOM", "HETATM")):
                    name = line[12:16].strip()
                    try:
                        x = float(line[30:38])
                        y = float(line[38:46])
                        z = float(line[46:54])
                    except ValueError:
                        continue
                    pdb_coords[name] = (x, y, z)

        if not pdb_coords:
            return False

        with open(mol2_path, "r") as f:
            mol2_lines = f.readlines()

        in_atom = False
        updated = 0
        with open(output_path, "w") as f:
            for line in mol2_lines:
                stripped = line.strip()
                if stripped == "@<TRIPOS>ATOM":
                    in_atom = True
                    f.write(line)
                    continue
                if in_atom and stripped.startswith("@<TRIPOS>"):
                    in_atom = False
                if in_atom and len(line.split()) >= 6:
                    parts = line.split()
                    atom_name = parts[1]
                    if atom_name in pdb_coords:
                        x, y, z = pdb_coords[atom_name]
                        parts[2] = f"{x:.4f}"
                        parts[3] = f"{y:.4f}"
                        parts[4] = f"{z:.4f}"
                        line = " ".join(parts) + "\n"
                        updated += 1
                f.write(line)

        return updated > 0
    except Exception:
        return False


def _atom_fingerprint(mol, idx: int) -> tuple:
    """Return a hashable fingerprint for atom *idx* in RDKit *mol*.

    Used to match atoms between two representations of the same molecule
    (e.g. mol2 from antechamber vs RDKit canonical-SMILES ordering).

    🔧 字段顺序必须与 _mol2_fp 严格一致。简化到 5 个无歧义字段:
      (符号, 原子序数, 度, 总键级=sum(键级), 邻居集排序)
    不再包含 GetNumExplicitHs/GetNumImplicitHs, 因为这两个字段对 RDKit 和 mol2
    的语义不一致(已实测: RDKit 对 sp3 C 全部返回 0, 但 mol2 端按 H 邻居数计) —
    会导致 heavy atom 永远匹配不上, Kabsch 对齐永远跑不起来.
    """
    a = mol.GetAtomWithIdx(idx)
    neighbors = []
    total_bond_order = 0
    for nb in a.GetNeighbors():
        nb_a = mol.GetAtomWithIdx(nb.GetIdx())
        neighbors.append((nb_a.GetSymbol(), nb_a.GetAtomicNum()))
        # 🔧 GetBondBetweenAtoms 是 Mol 的方法, 不是 Atom 的; 之前写成 a.GetBondBetweenAtoms
        # 在 RDKit 上会抛 AttributeError, 让 _rebuild_ligand_with_docked_pose 整个挂掉
        total_bond_order += mol.GetBondBetweenAtoms(idx, nb.GetIdx()).GetBondTypeAsDouble()
    neighbors.sort()
    return (a.GetSymbol(), a.GetAtomicNum(), a.GetDegree(),
            total_bond_order, tuple(neighbors))


def _rebuild_ligand_with_docked_pose(
    smiles: str,
    original_mol2: str,
    docked_pdb: str,
    output_mol2: str,
) -> bool:
    """Rebuild ligand mol2 with docked heavy-atom coords + RDKit-generated H coords.

    Solves the ``_update_mol2_coordinates`` blind-spot: the docked PDB from Vina
    only contains polar hydrogens, so non-polar H atoms keep their vacuum
    coordinates when doing naive name-matching.  This function uses RDKit to
    generate chemically-correct hydrogen positions after heavy atoms are
    constrained to the docked pose.

    Returns True on success.
    """
    import numpy as np
    from rdkit import Chem
    from rdkit.Chem import AllChem

    # ── 1. Parse original mol2 structure ────────────────────────────
    mol2_atoms: list[dict] = []
    mol2_bonds: list[tuple[int, int, int]] = []
    section_name = None
    with open(original_mol2) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("@<TRIPOS>"):
                section_name = stripped
                continue
            if section_name == "@<TRIPOS>ATOM":
                parts = line.split()
                if len(parts) >= 6:
                    mol2_atoms.append({
                        "name": parts[1],
                        "type": parts[5],
                        "x": float(parts[2]),
                        "y": float(parts[3]),
                        "z": float(parts[4]),
                        "charge": float(parts[-1]) if len(parts) >= 9 else 0.0,
                    })
            elif section_name == "@<TRIPOS>BOND":
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        order = int(parts[3])
                    except ValueError:
                        order = 5 if parts[3].strip().lower() == "ar" else 1
                    mol2_bonds.append((int(parts[1]) - 1, int(parts[2]) - 1, order))

    if not mol2_atoms:
        return False

    # ── 2. Read docked PDB target coordinates ───────────────────────
    pdb_coords: dict[str, tuple[float, float, float]] = {}
    with open(docked_pdb) as f:
        for line in f:
            if line.startswith(("ATOM", "HETATM")):
                name = line[12:16].strip()
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                except ValueError:
                    continue
                pdb_coords[name] = (x, y, z)

    if not pdb_coords:
        return False

    # ── 3. Build RDKit molecule from SMILES ─────────────────────────
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return False
    mol = Chem.AddHs(mol)

    params = AllChem.ETKDGv3()
    params.randomSeed = 42
    try:
        if AllChem.EmbedMolecule(mol, params) != 0:
            return False
    except Exception:
        return False
    try:
        AllChem.MMFFOptimizeMolecule(mol)
    except Exception:
        pass
    conf = mol.GetConformer()

    # ── 4. Match mol2 atoms ↔ RDKit atoms ───────────────────────────
    rd_fps: dict[int, tuple] = {}
    for i in range(mol.GetNumAtoms()):
        rd_fps[i] = _atom_fingerprint(mol, i)

    mol2_nbrs: list[list[int]] = [[] for _ in mol2_atoms]
    mol2_nbr_orders: list[list[int]] = [[] for _ in mol2_atoms]
    for i, j, order in mol2_bonds:
        mol2_nbrs[i].append(j)
        mol2_nbrs[j].append(i)
        mol2_nbr_orders[i].append(order)
        mol2_nbr_orders[j].append(order)

    def _mol2_fp(idx: int) -> tuple:
        """mol2 端 fingerprint, 字段顺序与 _atom_fingerprint (RDKit) 严格对齐:
            (符号, 原子序数, 度, 总键级=sum(键级), 邻居集排序)
        5 个无歧义字段, 避开 RDKit 那边有歧义的 explicit/implicit H 计数.
        """
        a = mol2_atoms[idx]
        sym = a["name"].rstrip("0123456789") or a["name"]
        elem_map = {"C": 6, "N": 7, "O": 8, "S": 16, "P": 15, "H": 1,
                    "F": 9, "Cl": 17, "Br": 35, "I": 53}
        anum = elem_map.get(sym, 0)

        nbrs = []
        for ni in mol2_nbrs[idx]:
            nsym = mol2_atoms[ni]["name"].rstrip("0123456789") or mol2_atoms[ni]["name"]
            nbrs.append((nsym, elem_map.get(nsym, 0)))
        nbrs.sort()

        total_bond_order = sum(mol2_nbr_orders[idx])

        return (sym, anum, len(mol2_nbrs[idx]),
                total_bond_order, tuple(nbrs))

    mol2_to_rdkit: dict[int, int] = {}
    used_rd: set[int] = set()
    for mi in range(len(mol2_atoms)):
        fp = _mol2_fp(mi)
        best = -1
        for ri, rfp in rd_fps.items():
            if ri in used_rd:
                continue
            if fp == rfp:
                best = ri
                break
        if best >= 0:
            mol2_to_rdkit[mi] = best
            used_rd.add(best)

    # ── 5. Kabsch alignment ────────────────────────────────────────
    src_pts: list[list[float]] = []
    dst_pts: list[list[float]] = []
    for mi, atom in enumerate(mol2_atoms):
        name = atom["name"]
        if name not in pdb_coords:
            continue
        rd_idx = mol2_to_rdkit.get(mi)
        if rd_idx is None:
            continue
        p = conf.GetAtomPosition(rd_idx)
        src_pts.append([p.x, p.y, p.z])
        dst_pts.append(list(pdb_coords[name]))

    if len(src_pts) < 3:
        return False

    src = np.array(src_pts)
    dst = np.array(dst_pts)
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    H = (src - src_c).T @ (dst - dst_c)
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T
    t = dst_c - R @ src_c

    # ── 6. Write updated mol2 ──────────────────────────────────────
    with open(original_mol2) as fin, open(output_mol2, "w") as fout:
        in_atom = False
        atom_idx = 0
        for line in fin:
            stripped = line.strip()
            if stripped == "@<TRIPOS>ATOM":
                in_atom = True
                fout.write(line)
                continue
            if in_atom and stripped.startswith("@<TRIPOS>"):
                in_atom = False

            if in_atom and len(line.split()) >= 6:
                parts = line.split()
                name = parts[1]
                if name in pdb_coords:
                    x, y, z = pdb_coords[name]
                else:
                    rd_idx = mol2_to_rdkit.get(atom_idx)
                    if rd_idx is not None:
                        p = conf.GetAtomPosition(rd_idx)
                        v = np.array([p.x, p.y, p.z])
                        v = R @ v + t
                        x, y, z = float(v[0]), float(v[1]), float(v[2])
                    else:
                        x, y, z = float(parts[2]), float(parts[3]), float(parts[4])
                parts[2] = f"{x:.4f}"
                parts[3] = f"{y:.4f}"
                parts[4] = f"{z:.4f}"
                line = " ".join(parts) + "\n"
                atom_idx += 1

            fout.write(line)

    return os.path.exists(output_mol2) and os.path.getsize(output_mol2) > 100


# ============================================================================
# 6. PDBQT generation (for docking)
# ============================================================================

def _pdbqt_fallback_obabel(
    input_abs: str,
    output_abs: str,
    *,
    degradation: list,
    errors: list,
) -> ToolResult:
    """L1 fallback: use OpenBabel to generate PDBQT."""
    ob = run_obabel_pdbqt(input_abs, output_abs)
    if ob.ok:
        return degraded(
            data=f"Fallback: OpenBabel generated PDBQT: {output_abs}",
            degradation=degradation,
            errors=errors,
            warnings=["OpenBabel uses Gasteiger charges, lower accuracy than MGLTools"],
        )
    return failed(
        errors=errors + ["OpenBabel PDBQT conversion also failed"],
        degradation=degradation + ["OpenBabel->failed"],
        env_packages=["mgltools", "openbabel"],
    )


def run_prepare_ligand4_py(input_file: str, output_pdbqt: str) -> ToolResult:
    """Generate ligand PDBQT via MGLTools prepare_ligand4.py.

    Degradation: L0 MGLTools -> L1 OpenBabel -> L2 fail.
    """
    input_abs = os.path.abspath(input_file)
    output_abs = os.path.abspath(output_pdbqt)
    output_dir = os.path.dirname(output_abs)

    if not os.path.exists(input_abs):
        return failed(errors=[f"Input file not found: {input_abs}"])

    script_path = PREPARE_LIGAND4_SCRIPT
    mgltools_pckgs_path = MGLTOOLS_PCKGS_PATH
    conda_env = CONDA_MGLTOOLS_ENV

    env = os.environ.copy()
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{str(mgltools_pckgs_path)}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = str(mgltools_pckgs_path)

    conda_executable = shutil.which("conda")
    if not conda_executable:
        return _pdbqt_fallback_obabel(
            input_abs, output_abs,
            degradation=["conda unavailable->OpenBabel"],
            errors=["conda command not found"],
        )

    chk = subprocess.run([conda_executable, "env", "list"], capture_output=True, text=True)
    if conda_env not in chk.stdout:
        return _pdbqt_fallback_obabel(
            input_abs, output_abs,
            degradation=["conda mgltools env missing->OpenBabel"],
            errors=[f"conda env '{conda_env}' not found"],
        )

    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        conda_executable, "run", "-n", conda_env,
        "python", str(script_path),
        "-l", input_abs, "-o", output_abs, "-v",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=output_dir)

    if result.returncode != 0:
        return _pdbqt_fallback_obabel(
            input_abs, output_abs,
            degradation=["MGLTools->OpenBabel"],
            errors=[f"prepare_ligand4.py failed: {result.stderr.strip()}"],
        )

    tool_debug("[run_prepare_ligand4_py] MGLTools succeeded")
    return success(data=f"MGLTools generated PDBQT: {output_abs}")


def run_obabel_pdbqt(input_file: str, output_pdbqt: str) -> ToolResult:
    """Convert MOL2/PDB to PDBQT using Open Babel."""
    if not is_tool_available("obabel"):
        return failed(
            errors=["obabel command not found on PATH or in LangGraph conda env"],
            env_packages=["openbabel"],
        )

    ext = Path(input_file).suffix.lower()
    in_fmt = {"mol2": "mol2", "pdb": "pdb"}.get(ext, "pdb")

    cmd = ["obabel", f"-i{in_fmt}", input_file, "-opdbqt", "-O", output_pdbqt]
    result = run_in_conda_env(cmd)
    if result.returncode != 0:
        return failed(errors=[f"Open Babel conversion failed: {result.stderr.strip()}"])
    return success(data=str(Path(output_pdbqt).resolve()))


# ============================================================================
# 7. Obabel general format conversion
# ============================================================================

def run_obabel(
    input_file: str,
    output_file: str,
    input_format: str = "pdb",
    output_format: str = "mol2",
) -> ToolResult:
    """General format conversion via Open Babel."""
    if not is_tool_available("obabel"):
        return failed(
            errors=["obabel command not found on PATH or in LangGraph conda env"],
            env_packages=["openbabel"],
        )

    cmd = ["obabel", f"-i{input_format}", input_file, f"-o{output_format}", "-O", output_file]
    result = run_in_conda_env(cmd)
    if result.returncode != 0:
        return failed(errors=[f"Open Babel conversion failed: {input_file} -> {output_file}"])
    return success(data=str(Path(output_file).resolve()))


# ============================================================================
# 8. ACPYPE: GROMACS topology
# ============================================================================

def run_acpype(
    input_file: str,
    output_dir: str,
    net_charge: Optional[int] = None,
    output_format: str = "gmx",
) -> ToolResult:
    """Generate GROMACS topology via ACPYPE."""
    if not is_tool_available("acpype"):
        return failed(
            errors=["acpype command not found on PATH or in LangGraph conda env, skipping GROMACS topology"],
            env_packages=["acpype"],
        )

    cmd = ["acpype", "-i", input_file, "-o", output_format, "-d"]
    if net_charge is not None:
        cmd.extend(["-n", str(net_charge)])

    original_cwd = os.getcwd()
    os.chdir(output_dir)
    result = run_in_conda_env(cmd)
    os.chdir(original_cwd)

    if result.returncode != 0:
        return failed(errors=[result.stderr.strip()])
    return success(data=f"ACPYPE completed: {output_dir}")


# ============================================================================
# 9. End-to-end ligand parameterization
# ============================================================================

def prepare_ligand_amber_route(
    input_smiles: Optional[str] = None,
    input_pdb: Optional[str] = None,
    input_file: Optional[str] = None,
    input_format: Optional[str] = None,
    output_dir: str = "./ligand_output",
    net_charge: Optional[int] = None,
    residue_name: str = "LIG",
    charge_method: str = "bcc",
    force_field: str = "gaff2",
    antechamber_extra_args: Optional[List[str]] = None,
    antechamber_kwargs: Optional[Dict[str, object]] = None,
    antechamber_intermediate_dir: Optional[str] = None,
    robust_input: bool = True,
    fallback_charge_methods: Optional[List[str]] = None,
    generate_pdbqt: bool = True,
    generate_md_files: bool = True,
    generate_gmx_files: bool = False,
    protein_pdb: Optional[str] = None,
) -> ToolResult:
    """Full ligand parameterization pipeline.

    Input: SMILES or structure file (PDB/MOL2/SDF)
    Output: GAFF parameters, charges, PDBQT (docking), PRMTOP/INPCRD (AMBER MD)
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    degradations: list[str] = []
    errors_accum: list[str] = []

    # Step 1: Resolve input
    ante_input_file = None
    ante_input_format = None
    temp_files: list[str] = []

    if input_smiles:
        ante_input_file = str(output_path / "input_from_smiles.pdb")
        r = smiles_to_pdb(input_smiles, ante_input_file)
        if not r.ok:
            return failed(errors=[f"SMILES to PDB conversion failed: {r.errors}"])
        ante_input_format = "pdb"
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
                ext_map = {
                    ".pdb": "pdb", ".mol2": "mol2", ".sdf": "sdf",
                    ".mdl": "mdl", ".ac": "ac", ".mol": "mdl",
                }
                ante_input_format = ext_map.get(Path(candidate_file).suffix.lower(), "pdb")
        else:
            return failed(errors=["Must provide input_smiles or valid input_pdb/input_file"])

    if net_charge is None:
        net_charge = 0

    base_kwargs = dict(antechamber_kwargs or {})
    if ante_input_format == "mol2" and "j" not in base_kwargs:
        base_kwargs["j"] = 4

    intermediate_root: Optional[Path] = None
    if antechamber_intermediate_dir:
        inter_path = Path(antechamber_intermediate_dir)
        if not inter_path.is_absolute():
            inter_path = output_path / inter_path
        inter_path.mkdir(parents=True, exist_ok=True)
        intermediate_root = inter_path
    else:
        intermediate_root = _ensure_temp_subdir("antechamber", output_path.name or residue_name.lower())

    input_candidates: list[Tuple[str, str, str]] = [(ante_input_file, ante_input_format, "original")]
    if robust_input and is_tool_available("obabel"):
        if ante_input_format != "pdb":
            fallback_pdb = str(output_path / "input_fallback.pdb")
            if run_obabel(ante_input_file, fallback_pdb, input_format=ante_input_format, output_format="pdb").ok:
                input_candidates.append((fallback_pdb, "pdb", "obabel->pdb"))
                temp_files.append(fallback_pdb)
        if ante_input_format != "mol2":
            fallback_mol2 = str(output_path / "input_fallback.mol2")
            if run_obabel(ante_input_file, fallback_mol2, input_format=ante_input_format, output_format="mol2").ok:
                input_candidates.append((fallback_mol2, "mol2", "obabel->mol2"))
                temp_files.append(fallback_mol2)

    charge_methods: list[str] = [charge_method]
    if fallback_charge_methods:
        for m in fallback_charge_methods:
            ml = str(m).lower()
            if ml and ml not in charge_methods:
                charge_methods.append(ml)
    if robust_input and "gas" not in charge_methods:
        charge_methods.append("gas")

    # Step 2: Antechamber
    mol2_file = str(output_path / f"{residue_name.lower()}.mol2")
    antechamber_ok = False
    attempt_logs: list[str] = []
    attempt_index = 0

    for candidate_file, candidate_fmt, candidate_desc in input_candidates:
        current_kwargs = dict(base_kwargs)
        if candidate_fmt == "mol2" and "j" not in current_kwargs:
            current_kwargs["j"] = 4
        for method in charge_methods:
            attempt_index += 1
            attempt_intermediate_dir = None
            if intermediate_root is not None:
                safe_desc = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_desc)
                attempt_dir = intermediate_root / f"attempt_{attempt_index:02d}_{safe_desc}_{candidate_fmt}_{method}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                attempt_intermediate_dir = str(attempt_dir)

            r = run_antechamber(
                candidate_file, mol2_file,
                input_format=candidate_fmt, charge_method=method,
                net_charge=net_charge, residue_name=residue_name,
                force_field=force_field,
                extra_args=antechamber_extra_args,
                intermediate_dir=attempt_intermediate_dir,
                **current_kwargs,
            )
            if r.ok:
                antechamber_ok = True
                break
            attempt_logs.append(
                f"[{candidate_desc}|fi={candidate_fmt}|charge={method}] {r.errors}"
            )
        if antechamber_ok:
            break

    for tmp in temp_files:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    if not antechamber_ok:
        return failed(
            errors=[f"Antechamber failed: {'; '.join(attempt_logs)}"],
            env_packages=["ambertools"],
        )

    result_data: dict[str, Optional[str]] = {
        "mol2": mol2_file,
        "frcmod": None, "prmtop": None, "inpcrd": None,
        "pdbqt": None, "gmx_top": None, "gmx_itp": None, "gmx_gro": None,
    }

    # Step 3: Parmchk2
    frcmod_file = str(output_path / f"{residue_name.lower()}.frcmod")
    r = run_parmchk2(mol2_file, frcmod_file, force_field=force_field,
                     work_dir=str(_ensure_temp_subdir("parmchk2", output_path.name or residue_name.lower())))
    if r.ok:
        result_data["frcmod"] = r.data
    else:
        degradations.extend(r.degradation)
        errors_accum.extend(r.errors)

    # Step 4: PDBQT
    if generate_pdbqt:
        pdbqt_file = str(output_path / f"{residue_name.lower()}.pdbqt")
        r = run_prepare_ligand4_py(mol2_file, pdbqt_file)
        if r.ok:
            result_data["pdbqt"] = pdbqt_file
        degradations.extend(r.degradation)
        errors_accum.extend(r.errors)

    # Step 5: AMBER topology
    if generate_md_files:
        prmtop_file = str(output_path / f"{residue_name.lower()}.prmtop")
        inpcrd_file = str(output_path / f"{residue_name.lower()}.inpcrd")
        r = run_tleap(
            mol2_file, frcmod_file, prmtop_file, inpcrd_file,
            residue_name, protein_pdb,
            work_dir=str(_ensure_temp_subdir("tleap", output_path.name or residue_name.lower())),
        )
        if r.ok:
            result_data["prmtop"] = prmtop_file
            result_data["inpcrd"] = inpcrd_file
        degradations.extend(r.degradation)
        errors_accum.extend(r.errors)

    # Step 6: GROMACS (optional)
    if generate_gmx_files:
        r = run_acpype(mol2_file, str(output_path), net_charge, "gmx")
        if r.ok:
            prefix = Path(mol2_file).stem
            for ext in ["_GMX.top", "_GMX.itp", "_GMX.gro"]:
                src = output_path / f"{prefix}{ext}"
                dst = output_path / f"{residue_name.lower()}{ext}"
                if src.exists():
                    shutil.move(str(src), str(dst))
                    if ext == "_GMX.top":
                        result_data["gmx_top"] = str(dst)
                    elif ext == "_GMX.itp":
                        result_data["gmx_itp"] = str(dst)
                    elif ext == "_GMX.gro":
                        result_data["gmx_gro"] = str(dst)

    if errors_accum:
        return degraded(data=result_data, degradation=degradations, errors=errors_accum)
    return success(data=result_data, warnings=degradations)


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    result = prepare_ligand_amber_route(
        input_smiles="O=C(O)[C@@H](N)CC[S+](C)C[C@H]1O[C@H]([C@H](O)[C@@H]1O)n2cnc3c(N)ncnc23",
        output_dir="./ligand_output",
        net_charge=0,
        residue_name="LIG",
        charge_method="gas",
        antechamber_kwargs={"m": 1, "j": 4},
        generate_pdbqt=True,
        generate_md_files=True,
        generate_gmx_files=False,
    )
    print(result.format_for_agent())
