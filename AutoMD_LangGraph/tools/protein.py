#!/usr/bin/env python3
"""
Protein preparation tools: PDB fetch, pdb4amber clean, standard residue filter,
tleap system builder, and MGLTools PDBQT generation.

All public functions return ToolResult.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from tools.shared import (
    PROJECT_ROOT,
    MGLTOOLS_PCKGS_PATH,
    CONDA_MGLTOOLS_ENV,
    PREPARE_RECEPTOR4_SCRIPT,
    success,
    degraded,
    failed,
    ToolResult,
    tool_debug,
    is_tool_available,
    run_in_conda_env,
)

STANDARD_PROTEIN_RESIDUES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "CYX", "CYM", "GLN", "GLU", "GLH",
    "GLY", "HIS", "HID", "HIE", "HIP", "ILE", "LEU", "LYS", "LYN", "MET",
    "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "ASH",
}


def fetch_pdb(pdb_id: str, output_dir: str = "./data/pdb") -> ToolResult:
    """Download PDB file from RCSB."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    pdb_file = out_path / f"{pdb_id}.pdb"
    if pdb_file.exists():
        return success(data=str(pdb_file.resolve()))

    try:
        import requests
    except ImportError:
        return failed(
            errors=["requests 库未安装"],
            env_packages=["requests"],
        )

    try:
        url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        pdb_file.write_text(r.text)
        return success(data=str(pdb_file.resolve()))
    except Exception as exc:
        return failed(errors=[f"下载 PDB {pdb_id} 失败: {exc}"])


def run_pdb4amber(pdb_file: str, output_file: str, keep_hetatm: bool = False) -> ToolResult:
    """Clean PDB with pdb4amber: standardize residue names, add hydrogens."""
    if not is_tool_available("pdb4amber"):
        return failed(
            errors=["未找到 pdb4amber 命令 (main PATH and LangGraph conda env)"],
            env_packages=["ambertools"],
        )

    cmd = ["pdb4amber", "-i", pdb_file, "-o", output_file, "--reduce"]
    if keep_hetatm:
        cmd.append("--keep-hetatm")

    result = run_in_conda_env(cmd)
    if result.returncode != 0:
        return failed(errors=[result.stderr.strip() or result.stdout.strip()])

    out_path = Path(output_file)
    if not out_path.exists():
        default = Path(pdb_file).stem + ".pdb"
        if Path(default).exists():
            shutil.move(default, out_path)
        else:
            return failed(errors=[f"pdb4amber 未生成输出文件: {output_file}"])

    return success(data=str(out_path.resolve()))


def filter_standard_protein_residues(input_pdb: str, output_pdb: str) -> ToolResult:
    """Keep only standard protein residues to avoid tleap parameter issues.

    🔧 同时删除 N 端残基里 Reduce (via pdb4amber --reduce) 添加的多余 backbone H。
    这个 H 在 aminont12.lib 里没有 atom type 定义, 会导致 tleap 报:
        FATAL: Atom .R<NTYR 2>.A<H 24> does not have a type.
    任何走 pdb4amber --reduce 流程的蛋白都会有这个问题(N 端被定义的情况).
    """
    try:
        # 第一遍: 找到最小 resSeq 的标准氨基酸残基 = N 端
        n_terminal_resseq = None
        with open(input_pdb, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line[:6].strip() != "ATOM":
                    continue
                resname = line[17:20].strip().upper()
                if resname not in STANDARD_PROTEIN_RESIDUES:
                    continue
                try:
                    resseq = int(line[22:26].strip())
                except ValueError:
                    continue
                if n_terminal_resseq is None or resseq < n_terminal_resseq:
                    n_terminal_resseq = resseq

        # 第二遍: 写所有行, 但跳过 N 端残基里 Reduce 加的多余 H
        kept = 0
        with open(input_pdb, "r", encoding="utf-8", errors="ignore") as src, \
             open(output_pdb, "w", encoding="utf-8") as dst:
            for line in src:
                record = line[:6].strip()
                if record in {"ATOM", "HETATM"}:
                    resname = line[17:20].strip().upper()
                    if resname in STANDARD_PROTEIN_RESIDUES:
                        # 🔧 删 N 端 Reduce 加的额外 backbone H
                        #    Reduce 输出的特征: atom_name 是 "H" 或 "H1"
                        #    (普通氨基酸 H 的名字是 "HA"/"HB"/"HG"/... 有第 2 个字母)
                        #    不能用更激进的"删所有 H", 会误删正常蛋白 H
                        if n_terminal_resseq is not None:
                            try:
                                resseq = int(line[22:26].strip())
                                atom_name = line[12:16].strip()
                                if resseq == n_terminal_resseq and atom_name in ("H", "H1"):
                                    continue  # 跳过这行
                            except (ValueError, IndexError):
                                pass
                        dst.write(line)
                        kept += 1
                    continue
                if record in {"TER", "END", "ENDMDL"}:
                    dst.write(line)

        if kept == 0:
            return failed(errors=["过滤后没有保留任何标准蛋白原子"])
        return success(data=str(Path(output_pdb).resolve()))
    except Exception as exc:
        return failed(errors=[f"过滤标准蛋白残基失败: {exc}"])


def _detect_disulfide_bonds(pdb_path: str, max_dist: float = 2.5) -> list[tuple[int, int]]:
    """Find disulfide bonds from a PDB file.

    Two detection strategies are combined (union):
      1. SSBOND records (most authoritative — PDB-deposited experimental annotation)
      2. CYS-SG / CYX-SG distance within max_dist (fallback for PDBs without SSBOND
         or where pdb4amber left residues as CYS instead of CYX)

    Returns a de-duplicated list of (residue_number, residue_number) pairs.
    """
    from math import sqrt

    # 1) Read SSBOND records (PDB column spec, 1-indexed):
    #    SSBOND   1 CYS A    7    CYS A  137
    #    columns  1-  6 "SSBOND"
    #             8-10 serial
    #            12-14 resName1
    #            16   chain1
    #            18-21 resSeq1
    #            26-28 resName2
    #            30   chain2
    #            32-35 resSeq2
    # In 0-indexed Python slices: resSeq1 = [17:21], resSeq2 = [31:35]
    bonds: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    with open(pdb_path, "r") as f:
        for line in f:
            if not line.startswith("SSBOND"):
                continue
            try:
                r1 = int(line[17:21].strip())
                r2 = int(line[31:35].strip())
            except (ValueError, IndexError):
                continue
            pair = (min(r1, r2), max(r1, r2))
            if pair not in seen:
                seen.add(pair)
                bonds.append(pair)

    # 2) Distance-based detection for CYS / CYX SG pairs
    sg_atoms: dict[tuple[str, int], tuple[float, float, float]] = {}
    with open(pdb_path, "r") as f:
        for line in f:
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue
            resname = line[17:20].strip()
            atomname = line[12:16].strip()
            if atomname != "SG" or resname not in ("CYS", "CYX"):
                continue
            try:
                resid = int(line[22:26].strip())
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
            except (ValueError, IndexError):
                continue
            sg_atoms[(resname, resid)] = (x, y, z)

    # Group by residue number (regardless of CYS/CYX) — two CYS at same resnum
    # is the same atom physically, so we collapse the keys.
    by_resid: dict[int, tuple[float, float, float]] = {}
    for (_resn, resid), xyz in sg_atoms.items():
        # If a residue has BOTH a CYS and CYX entry (unusual), prefer CYX
        if resid not in by_resid or _resn == "CYX":
            by_resid[resid] = xyz

    residues = list(by_resid.keys())
    for i in range(len(residues)):
        for j in range(i + 1, len(residues)):
            ri, rj = residues[i], residues[j]
            xi, yi, zi = by_resid[ri]
            xj, yj, zj = by_resid[rj]
            d = sqrt((xi - xj) ** 2 + (yi - yj) ** 2 + (zi - zj) ** 2)
            if d < max_dist:
                pair = (min(ri, rj), max(ri, rj))
                if pair not in seen:
                    seen.add(pair)
                    bonds.append(pair)

    return bonds


def _fix_n_terminal_residue_names(pdb_path: str) -> str:
    """Replace N-terminal residue names (NSER, NGLY, NALA, ...) with their
    standard forms (SER, GLY, ALA, ...) in a PDB file.

    Background: tleap's `addPdbResMap` (default behavior) auto-renames
    chain-start residues to NXXX (aminont12.lib expects -NH3+ protonation).
    However, the H atoms already in the PDB (H1/H2/H3) have no atom type
    in tleap's N-terminal library, causing FATAL errors like:
        "FATAL: Atom .R<NSER 20>.A<H 14> does not have a type."

    Replacing NXXX with XXX makes tleap treat these residues as standard
    internal residues (which it handles correctly).

    Also handles C-terminal (CXXX) for symmetry.

    Returns path to the fixed PDB (creates "<name>.nterm_fixed.pdb" only
    if changes were made; otherwise returns the original path). Idempotent.
    """
    pdb_path = Path(pdb_path)
    fixed_path = pdb_path.with_name(pdb_path.stem + ".nterm_fixed.pdb")

    standard_aa = {
        "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
        "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
        "TYR", "VAL",
        # Histidine tautomers (in case PDB uses them)
        "HID", "HIE", "HIP",
    }

    # Match ATOM/HETATM line where residue name is NXXX (4 chars wide, at
    # position 17). This is tleap's N-terminal mapping leaking into the PDB.
    #
    # Capture group(2) is the 4-char NXXX form (e.g. "NSER"). We slice off
    # the prefix to get the 3-letter AA base.
    #
    # Note: we intentionally do NOT match N at position 16 (the altLoc column).
    # If altLoc is "N" with a standard 3-letter resname after it (e.g. "NALA"
    # where 'N' is altLoc and 'ALA' is resname), the line is fine as-is.
    n_pattern = re.compile(r"^(.{17})(N[A-Z]{3})( )")
    c_pattern = re.compile(r"^(.{17})(C[A-Z]{3})( )")

    def _fix(match: re.Match) -> str:
        prefix = match.group(1)        # first 17 chars
        n_c_xxx = match.group(2)      # e.g. "NSER" or "CGLY" (4 chars)
        trailing = match.group(3)     # the space after the 4-char form
        # group(2) is "N" + 3-letter AA base, e.g. "NSER" → base is "SER"
        base = n_c_xxx[1:]
        if base in standard_aa:
            # Replace "NSER" (4 chars) with " SER" (4 chars, leading space) to
            # keep PDB column alignment intact.
            return f"{prefix} {base}{trailing}"
        return match.group(0)  # leave unknown N/CXXX alone

    changed = False
    with open(pdb_path, "r") as fin, open(fixed_path, "w") as fout:
        for line in fin:
            new_line = line
            if line.startswith(("ATOM  ", "HETATM")):
                new_line = n_pattern.sub(_fix, new_line)
                new_line = c_pattern.sub(_fix, new_line)
            if new_line != line:
                changed = True
            fout.write(new_line)

    return str(fixed_path) if changed else str(pdb_path)


def _rename_n_terminal_h_atoms(pdb_path: str) -> int:
    """Rename generic 'H' atoms on N-terminal residues to 'H1' so they match
    tleap's aminont12.lib NXXX templates.

    pdb4amber's --reduce option adds H atoms but uses the generic name "H"
    rather than N-terminal-specific names (H1, H2, H3). When tleap's
    addPdbResMap then renames e.g. SER 20 to NSER 20, the aminont12.lib
    NSER template expects H1/H2/H3 specifically. The generic "H" in the PDB
    doesn't match the template, so tleap leaves the PDB's H AND creates a
    new H atom (still generic) — neither has a type, causing:
        FATAL: Atom .R<NSER 20>.A<H 14> does not have a type.

    The fix: rename the generic H to H1 in the PDB. Then tleap sees the H1
    matches the template, assigns the correct type, and adds H2/H3 itself.

    Important: a PDB may have multiple chain *segments* separated by TER
    records (e.g. when pdb4amber breaks a single chain at missing-residue
    gaps). Each segment is treated as its own chain by tleap and gets its
    own N-terminal. We detect segments by TER records AND by residue-number
    discontinuities (skipping non-ATOM lines and lines without a parseable
    resnum), not by chain ID alone — pdb4amber often uses the same chain
    ID for all segments.

    The PDB is rewritten in place. Returns the number of H atoms renamed.
    """
    # Step 1: walk the PDB and collect (chain, resname, resnum) for every
    # N-terminal residue. A "chain segment" starts:
    #   - at the first ATOM in the file
    #   - right after a TER record
    #   - right after a residue-number discontinuity (current resnum < prev)
    n_term_residues: set[tuple[str, str, int]] = set()  # (chain, resname, resnum)
    prev_resnum: int | None = None
    in_segment = False
    with open(pdb_path, "r") as f:
        for line in f:
            if line.startswith("TER") or line.startswith("END"):
                in_segment = False
                prev_resnum = None
                continue
            if not line.startswith("ATOM"):
                continue
            resname = line[17:20].strip()
            try:
                resnum = int(line[22:26].strip())
            except ValueError:
                continue
            chain = line[21:22]
            # Start of a new segment?
            if not in_segment:
                n_term_residues.add((chain, resname, resnum))
                in_segment = True
            elif prev_resnum is not None and resnum < prev_resnum:
                # Residue number went backwards (e.g. 19 -> 20 in 3PTB's
                # segment split). Treat as new segment.
                n_term_residues.add((chain, resname, resnum))
            prev_resnum = resnum

    # Step 2: rewrite the PDB, renaming generic "H" to "H1" on N-terminal
    # residues. PDB atom name field is columns 13-16 (1-indexed), i.e.
    # slice [12:16] in 0-indexed Python, 4 chars wide right-justified.
    rename_count = 0
    with open(pdb_path, "r") as f:
        lines = f.readlines()
    new_lines: list[str] = []
    for line in lines:
        if line.startswith("ATOM"):
            atomname = line[12:16].strip()
            if atomname == "H":
                try:
                    resnum = int(line[22:26].strip())
                except ValueError:
                    new_lines.append(line)
                    continue
                resname = line[17:20].strip()
                chain = line[21:22]
                if (chain, resname, resnum) in n_term_residues:
                    new_line = line[:12] + " H1 " + line[16:]
                    new_lines.append(new_line)
                    rename_count += 1
                    continue
        new_lines.append(line)

    if rename_count > 0:
        with open(pdb_path, "w") as f:
            f.writelines(new_lines)
        print(
            f"[protein] _rename_n_terminal_h_atoms: renamed {rename_count} generic 'H' "
            f"atoms to 'H1' on N-terminal residues in {pdb_path}"
        )
    return rename_count


def _parse_tleap_clashes(tleap_log_path: str) -> list[tuple[str, int, str]]:
    """Parse tleap.log for atomic clash / coordination-exceeded errors.

    Looks for patterns like:
        "Note. Bond: maximum coordination exceeded on .R<CYX 7>.A<HB2 6>"
        "Note. Bond: Maximum coordination exceeded on .R<LYS 136>.A<HD2 12>"
        "ATOMS NOT BONDED: .R<CYX 7>.A<HB2 6> .R<LYS 136>.A<HD2 12>"

    Returns a list of (resname, resnum, atomname) for the offending atoms.
    Currently limited to HB*/HD*/HG* side-chain hydrogens — the most common
    clash source in old PDB structures.

    If the log doesn't exist or is empty, returns an empty list.
    """
    p = Path(tleap_log_path)
    if not p.exists():
        return []

    pattern = re.compile(
        r"\b(?:coordination\s+exceeded|not\s+bonded).*?"
        r"\.(?:R|C)<([A-Z]{2,3})\s+(\d+)>\.A<([A-Z]+\d*)\s+\d+>",
        re.IGNORECASE,
    )

    clashes: list[tuple[str, int, str]] = []
    seen_keys: set[tuple[str, int, str]] = set()
    with open(p, "r", errors="ignore") as f:
        for line in f:
            for m in pattern.finditer(line):
                resname, resnum_s, atomname = m.group(1), m.group(2), m.group(3)
                try:
                    resnum = int(resnum_s)
                except ValueError:
                    continue
                # Only target side-chain hydrogens — these are the most
                # common clash source in old PDBs and least likely to
                # break downstream energetics. Heavier atoms (CB, CG, etc.)
                # would be more disruptive to remove.
                if re.match(r"^H[DBG]?\d?$", atomname, re.IGNORECASE):
                    key = (resname.upper(), resnum, atomname.upper())
                    if key not in seen_keys:
                        seen_keys.add(key)
                        clashes.append(key)
    return clashes


def _remove_atoms_from_pdb(
    pdb_path: str,
    atoms_to_remove: list[tuple[str, int, str]],
) -> str:
    """Remove specified atoms from a PDB file (in-place is unsafe; creates a
    new "<name>.clash_removed.pdb").

    atoms_to_remove: list of (resname, resnum, atomname) tuples — these
    will be matched case-insensitively against ATOM/HETATM records.

    Returns the path to the new file.
    """
    pdb_path = Path(pdb_path)
    out_path = pdb_path.with_name(pdb_path.stem + ".clash_removed.pdb")
    keys = {(r.upper(), int(n), a.upper()) for (r, n, a) in atoms_to_remove}

    with open(pdb_path, "r") as fin, open(out_path, "w") as fout:
        for line in fin:
            if line.startswith(("ATOM  ", "HETATM")):
                resname = line[17:20].strip().upper()
                atomname = line[12:16].strip().upper()
                try:
                    resnum = int(line[22:26].strip())
                except ValueError:
                    fout.write(line)
                    continue
                if (resname, resnum, atomname) in keys:
                    continue  # skip this atom
            fout.write(line)

    return str(out_path)


def run_tleap(
    clean_pdb: str,
    output_dir: Path,
    box_padding: float = 10.0,
    neutralize: bool = True,
    force_field: str = "ff19SB",
    water_model: str = "tip3p",
) -> ToolResult:
    """Build Amber system with tleap: load forcefield, solvate, neutralize.

    Returns ToolResult with data={"prmtop": str, "inpcrd": str}.
    """
    if not is_tool_available("tleap"):
        return failed(
            errors=["未找到 tleap 命令 (main PATH and LangGraph conda env)"],
            env_packages=["ambertools"],
        )

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_pdb_path = Path(clean_pdb).resolve()

    # A1: Fix N-terminal residue names (NSER -> SER etc.) to avoid tleap's
    # "Atom does not have a type" errors. Idempotent: returns original path
    # if no fixes are needed.
    fixed_pdb = _fix_n_terminal_residue_names(str(clean_pdb_path))
    tleap_input_pdb = Path(fixed_pdb)

    # A2: Detect disulfide bonds (SSBOND records + CYS/CYX distance)
    ss_bonds = _detect_disulfide_bonds(str(tleap_input_pdb))

    _water_box = f"{water_model.upper()}BOX" if water_model.lower() in ("tip3p", "tip4p", "tip4pew") else "TIP3PBOX"

    # Fix the cleaned PDB in place: rename generic N-terminal "H" atoms
    # to "H1" so tleap's aminont12.lib NXXX templates can match. See
    # _rename_n_terminal_h_atoms for the full rationale.
    _rename_n_terminal_h_atoms(str(tleap_input_pdb))

    leap_script = (
        f"source leaprc.protein.{force_field}\n"
        f"source leaprc.water.{water_model}\n"
        f"prot = loadpdb {tleap_input_pdb}\n"
    )
    if ss_bonds:
        for r1, r2 in ss_bonds:
            leap_script += f"bond prot.{r1}.SG prot.{r2}.SG\n"
    leap_script += f"solvateBox prot {_water_box} {box_padding}\n"
    if neutralize:
        leap_script += "addions prot Na+ 0\n"
    leap_script += "saveamberparm prot protein.prmtop protein.inpcrd\nquit\n"

    script_file = output_dir / "tleap.in"
    script_file.write_text(leap_script)

    result = run_in_conda_env(
        ["tleap", "-f", script_file.name],
        cwd=output_dir,
    )
    if result.returncode != 0:
        return failed(errors=[f"tleap 执行失败:\n{result.stderr}\n{result.stdout}"])

    prmtop = output_dir / "protein.prmtop"
    inpcrd = output_dir / "protein.inpcrd"
    if not prmtop.exists() or not inpcrd.exists():
        return failed(errors=["tleap 未能生成 prmtop/inpcrd 文件"])

    return success(data={"prmtop": str(prmtop), "inpcrd": str(inpcrd)})


def run_tleap_with_recovery(
    clean_pdb: str,
    output_dir: Path,
    box_padding: float = 10.0,
    neutralize: bool = True,
    force_field: str = "ff19SB",
    water_model: str = "tip3p",
    max_recovery_attempts: int = 1,
) -> ToolResult:
    """Run tleap with automatic clash-recovery fallback (B).

    Flow:
      1. Call run_tleap normally.
      2. If it fails AND tleap's leap.log shows atomic clashes
         (coordination exceeded / atoms not bonded), parse them and
         remove from the cleaned PDB.
      3. Retry run_tleap with the clash-removed PDB.
      4. Return final result (success or last failure).

    Only one retry by default — beyond that the problem is likely
    structural (true model defects) and further attempts just thrash.
    """
    r = run_tleap(
        clean_pdb=clean_pdb,
        output_dir=output_dir,
        box_padding=box_padding,
        neutralize=neutralize,
        force_field=force_field,
        water_model=water_model,
    )
    if r.ok or max_recovery_attempts <= 0:
        return r

    log_path = Path(output_dir) / "leap.log"
    clashes = _parse_tleap_clashes(str(log_path))
    if not clashes:
        return r  # no parseable clashes; nothing we can do automatically

    # Remove the offending side-chain hydrogens from the N-terminal-fixed PDB
    # (which is what run_tleap just used). We re-derive it from clean_pdb.
    fixed_pdb = _fix_n_terminal_residue_names(str(Path(clean_pdb).resolve()))
    clash_removed_pdb = _remove_atoms_from_pdb(fixed_pdb, clashes)

    recovery_r = run_tleap(
        clean_pdb=clash_removed_pdb,
        output_dir=output_dir,
        box_padding=box_padding,
        neutralize=neutralize,
        force_field=force_field,
        water_model=water_model,
    )
    if recovery_r.ok:
        return success(
            data=recovery_r.data,
            warnings=[f"自动恢复了 {len(clashes)} 个原子冲突: {clashes}"],
        )
    return failed(
        errors=[
            f"原始 tleap 失败:\n{r.errors}",
            f"自动恢复后仍失败:\n{recovery_r.errors}",
            f"尝试移除的冲突原子: {clashes}",
        ],
    )


def run_prepare_receptor4_py(input_pdb: str, output_pdbqt: str) -> ToolResult:
    """Generate receptor PDBQT via MGLTools prepare_receptor4.py.

    Degradation: L0 MGLTools → L1 OpenBabel → L2 fail.
    """
    input_abs = os.path.abspath(input_pdb)
    output_abs = os.path.abspath(output_pdbqt)
    output_dir = os.path.dirname(output_abs)

    if not os.path.exists(input_abs):
        return failed(errors=[f"输入文件不存在: {input_abs}"])

    os.makedirs(output_dir, exist_ok=True)

    conda_env = CONDA_MGLTOOLS_ENV
    script_path = PREPARE_RECEPTOR4_SCRIPT
    mgltools_pckgs_path = MGLTOOLS_PCKGS_PATH

    env = os.environ.copy()
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{str(mgltools_pckgs_path)}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = str(mgltools_pckgs_path)

    conda_executable = shutil.which("conda")
    use_conda_run = False

    if conda_executable:
        chk = subprocess.run(
            [conda_executable, "env", "list"],
            capture_output=True, text=True,
        )
        if conda_env in chk.stdout:
            use_conda_run = True

    if not use_conda_run:
        from tools.ligand import run_obabel_pdbqt
        ob = run_obabel_pdbqt(input_abs, output_abs)
        if ob.ok:
            tool_debug("[run_prepare_receptor4_py] L1: OpenBabel fallback (MGLTools unavailable)")
            return degraded(
                data=f"降级使用 OpenBabel 生成 PDBQT: {output_abs}",
                degradation=["MGLTools unavailable→OpenBabel"],
                warnings=["OpenBabel 使用 Gasteiger 电荷，精度低于 MGLTools"],
            )
        return failed(
            errors=[f"conda 环境 '{conda_env}' 不可用，OpenBabel 降级也失败"],
            env_packages=["mgltools", "openbabel"],
        )

    cmd = [
        conda_executable, "run", "-n", conda_env,
        "python", str(script_path),
        "-r", input_abs,
        "-o", output_abs,
        "-A", "hydrogens",
        "-U", "nphs",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=output_dir)

    if result.returncode == 0 and os.path.exists(output_abs):
        tool_debug("[run_prepare_receptor4_py] L0: MGLTools succeeded")
        return success(data=f"MGLTools 生成 PDBQT: {output_abs}")

    from tools.ligand import run_obabel_pdbqt
    ob = run_obabel_pdbqt(input_abs, output_abs)
    if ob.ok:
        tool_debug("[run_prepare_receptor4_py] L1: OpenBabel fallback (MGLTools failed)")
        return degraded(
            data=f"降级使用 OpenBabel 生成 PDBQT: {output_abs}",
            degradation=["MGLTools→OpenBabel"],
            errors=[f"MGLTools 失败: {result.stderr.strip()}"],
            warnings=["OpenBabel 使用 Gasteiger 电荷，精度低于 MGLTools"],
        )
    return failed(
        errors=["MGLTools 和 OpenBabel 均失败"],
        degradation=["MGLTools→OpenBabel→failed"],
        env_packages=["mgltools", "openbabel"],
    )


def prepare_pure_protein(pdb_id: str, output_root: str = "./output") -> dict:
    """Full protein preparation pipeline (legacy wrapper, returns dict for compatibility)."""
    output_root = Path(output_root)
    for d in [output_root / "pdb", output_root / "prepared", output_root / "md"]:
        d.mkdir(parents=True, exist_ok=True)

    r = fetch_pdb(pdb_id, str(output_root / "pdb"))
    if not r.ok:
        raise RuntimeError(f"下载 PDB 失败: {r.errors}")
    raw_pdb = r.data

    clean_pdb = output_root / "prepared" / f"{pdb_id}_clean.pdb"
    r = run_pdb4amber(raw_pdb, str(clean_pdb), keep_hetatm=False)
    if not r.ok:
        raise RuntimeError(f"pdb4amber 失败: {r.errors}")

    protein_only_pdb = output_root / "prepared" / f"{pdb_id}_protein_only.pdb"
    r = filter_standard_protein_residues(str(clean_pdb), str(protein_only_pdb))
    if not r.ok:
        raise RuntimeError(f"过滤失败: {r.errors}")

    r = run_tleap(str(protein_only_pdb), output_root / "md", box_padding=10.0, neutralize=True)
    if not r.ok:
        raise RuntimeError(f"tleap 失败: {r.errors}")

    return {
        "raw_pdb": raw_pdb,
        "clean_pdb": str(clean_pdb),
        "protein_only_pdb": str(protein_only_pdb),
        "prmtop": r.data["prmtop"],
        "inpcrd": r.data["inpcrd"],
    }


if __name__ == "__main__":
    r = run_prepare_receptor4_py(
        input_pdb="../Agents/output/prepared/1AKE_protein_clean.pdb",
        output_pdbqt="./output/prepared/1AKE_clean.pdbqt",
    )
    print(r.format_for_agent())
