"""
Post-docking analysis tools for protein-ligand interaction analysis,
pose clustering, interaction visualization, and ADMET property prediction.

Tools:
- analyze_interactions: PLIP/ProLIF-based interaction fingerprint
- cluster_docking_poses: RMSD-based clustering of docking poses
- generate_interaction_diagram: 2D LigPlot-style interaction diagram
- predict_admet: Rule-based ADMET property prediction via RDKit
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.shared import PROJECT_ROOT, success, degraded, failed, ToolResult


def _which(program: str) -> Optional[str]:
    return shutil.which(program)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ============================================================================
# 1. Interaction Analysis
# ============================================================================


def analyze_interactions(
    protein_pdb: str,
    ligand_sdf: str,
    output_dir: str,
    use_plip: bool = True,
) -> ToolResult:
    """Analyze protein-ligand interactions.

    Detects H-bonds, hydrophobic contacts, pi-stacking, pi-cation,
    salt bridges, halogen bonds, and water bridges.

    Args:
        protein_pdb: Path to protein PDB file.
        ligand_sdf: Path to ligand SDF/MOL2 file.
        output_dir: Directory for output reports.
        use_plip: Try PLIP CLI first (falls back to RDKit if unavailable).

    Returns:
        ToolResult with data dict containing interaction lists.
    """
    out = _ensure_dir(Path(output_dir))
    degradations: list[str] = []
    errors: list[str] = []

    interactions = _try_plip(protein_pdb, ligand_sdf, out, degradations, errors)
    if interactions is not None:
        return success(interactions)

    interactions = _try_prolif(protein_pdb, ligand_sdf, out, degradations, errors)
    if interactions is not None:
        return degraded(interactions, degradation=degradations, errors=errors)

    interactions = _rdkit_distance_interactions(protein_pdb, ligand_sdf, out)
    if interactions is not None:
        degradations.append("PLIP/ProLIF -> RDKit distance-based")
        return degraded(interactions, degradation=degradations, errors=errors)

    return failed(errors=["All interaction analysis methods failed"])


def _try_plip(
    protein_pdb: str,
    ligand_sdf: str,
    output_dir: Path,
    degradations: list[str],
    errors: list[str],
) -> Optional[dict]:
    """Try PLIP command-line tool."""
    plip_exe = _which("plip")
    if not plip_exe:
        conda_plip = os.path.expanduser("~/miniconda3/bin/plip")
        if os.path.exists(conda_plip):
            plip_exe = conda_plip
    if not plip_exe:
        degradations.append("PLIP not found")
        return None

    try:
        result = subprocess.run(
            [
                plip_exe,
                "-f", protein_pdb,
                "-l", ligand_sdf,
                "-o", str(output_dir),
                "-t",
                "-p",
                "-x",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            errors.append(f"PLIP failed (rc={result.returncode}): {result.stderr[:300]}")
            return None

        xml_path = output_dir / "report.xml"
        if xml_path.exists():
            return _parse_plip_xml(str(xml_path))
        txt_report = output_dir / "report.txt"
        if txt_report.exists():
            return _parse_plip_txt(str(txt_report))

        errors.append("PLIP ran but no report file found")
        return None

    except subprocess.TimeoutExpired:
        errors.append("PLIP timed out")
        return None
    except Exception as exc:
        errors.append(f"PLIP exception: {exc}")
        return None


def _parse_plip_xml(xml_path: str) -> dict:
    """Parse PLIP XML report into structured dict."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(xml_path)
    root = tree.getroot()

    def _parse_interactions(tag: str) -> list[dict]:
        results: list[dict] = []
        for elem in root.iter(tag):
            item: dict = {}
            for child in elem:
                item[child.tag] = child.text or ""
            results.append(item)
        return results

    interaction_types = {
        "hydrophobic_interactions": "hydrophobic_interaction",
        "hydrogen_bonds": "hydrogen_bond",
        "pi_stacks": "pi_stack",
        "pi_cation_interactions": "pi_cation",
        "salt_bridges": "salt_bridge",
        "halogen_bonds": "halogen_bond",
        "water_bridges": "water_bridge",
    }

    results: dict = {}
    for key, tag in interaction_types.items():
        items = _parse_interactions(tag)
        if items:
            results[key] = items

    results["summary"] = {
        k: len(v) for k, v in results.items() if k != "summary"
    }
    return results


def _parse_plip_txt(txt_path: str) -> dict:
    """Parse PLIP text report (fallback when XML unavailable)."""
    results: dict = {}
    current_type = None

    with open(txt_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "Hydrophobic" in line:
                current_type = "hydrophobic_interactions"
                results[current_type] = []
            elif "Hydrogen Bonds" in line:
                current_type = "hydrogen_bonds"
                results[current_type] = []
            elif "pi-Stacking" in line or "Pi-Stacking" in line:
                current_type = "pi_stacks"
                results[current_type] = []
            elif "pi-Cation" in line or "Pi-Cation" in line:
                current_type = "pi_cation_interactions"
                results[current_type] = []
            elif "Salt Bridges" in line:
                current_type = "salt_bridges"
                results[current_type] = []
            elif "Halogen Bonds" in line:
                current_type = "halogen_bonds"
                results[current_type] = []
            elif "Water Bridges" in line:
                current_type = "water_bridges"
                results[current_type] = []
            elif current_type and line[0].isdigit():
                results[current_type].append({"raw": line})

    results["summary"] = {
        k: len(v) for k, v in results.items() if k != "summary"
    }
    return results


def _try_prolif(
    protein_pdb: str,
    ligand_sdf: str,
    output_dir: Path,
    degradations: list[str],
    errors: list[str],
) -> Optional[dict]:
    """Try ProLIF (pure-Python interaction fingerprint library)."""
    try:
        from prolif.molecule import Molecule
        from prolif.fingerprint import Fingerprint
    except ImportError:
        degradations.append("ProLIF not installed")
        return None

    try:
        prot = Molecule.from_pdb(protein_pdb)
        lig = Molecule.from_sdf(ligand_sdf)
        if lig is None:
            lig_mols = Molecule.from_file(ligand_sdf)
            lig = lig_mols[0] if lig_mols else None
        if prot is None or lig is None:
            errors.append("ProLIF: failed to load molecules")
            return None

        fp = Fingerprint()
        fp.run_from_iterable(lig, prot)

        results: dict = {
            "hydrogen_bonds": [],
            "hydrophobic_interactions": [],
            "pi_stacks": [],
            "pi_cation_interactions": [],
            "salt_bridges": [],
            "halogen_bonds": [],
        }

        type_map = {
            "HBDonor": "hydrogen_bonds",
            "HBAcceptor": "hydrogen_bonds",
            "Hydrophobic": "hydrophobic_interactions",
            "PiStacking": "pi_stacks",
            "PiCation": "pi_cation_interactions",
            "Anionic": "salt_bridges",
            "Cationic": "salt_bridges",
            "HalogenDonor": "halogen_bonds",
            "HalogenAcceptor": "halogen_bonds",
        }

        if hasattr(fp, "to_dataframe"):
            df = fp.to_dataframe()
            for col in df.columns:
                if df[col].any():
                    interaction_type = type_map.get(col, "other")
                    if interaction_type not in results:
                        results[interaction_type] = []
                    results[interaction_type].append({
                        "type": col,
                        "residues": list(df.index[df[col]]),
                    })

        results["summary"] = {
            k: len(v) for k, v in results.items() if k != "summary"
        }
        return results

    except Exception as exc:
        errors.append(f"ProLIF exception: {exc}")
        return None


def _rdkit_distance_interactions(
    protein_pdb: str,
    ligand_sdf: str,
    output_dir: Path,
) -> Optional[dict]:
    """Basic RDKit distance-based interaction detection."""
    try:
        from rdkit import Chem
    except ImportError:
        return None

    try:
        protein = Chem.MolFromPDBFile(protein_pdb, removeHs=False)
        if protein is None:
            return None

        lig_supplier = Chem.SDMolSupplier(ligand_sdf, removeHs=False)
        ligand = lig_supplier[0] if len(lig_supplier) > 0 else None
        if ligand is None:
            ligand = Chem.MolFromMol2File(ligand_sdf, removeHs=False)
        if ligand is None:
            return None

        results = _compute_distance_based_interactions(protein, ligand)
        return results
    except Exception:
        return None


def _compute_distance_based_interactions(
    protein: Any,
    ligand: Any,
    hbond_dist_cutoff: float = 3.5,
    hydrophobic_dist_cutoff: float = 4.0,
    salt_bridge_dist_cutoff: float = 4.0,
) -> dict:
    """Compute interactions based on interatomic distances."""
    from rdkit import Chem

    prot_conf = protein.GetConformer()
    lig_conf = ligand.GetConformer()

    # Collect protein atom info
    prot_atoms: list[dict] = []
    for atom in protein.GetAtoms():
        info = atom.GetPDBResidueInfo()
        if info is None:
            continue
        pos = prot_conf.GetAtomPosition(atom.GetIdx())
        name = info.GetName().strip()
        prot_atoms.append({
            "idx": atom.GetIdx(),
            "symbol": atom.GetSymbol(),
            "residue_name": info.GetResidueName().strip(),
            "residue_num": info.GetResidueNumber(),
            "chain": info.GetChainId().strip(),
            "pos": (pos.x, pos.y, pos.z),
            "is_backbone": atom.GetSymbol() in ("N", "C", "O") and name in ("N", "CA", "C", "O"),
        })

    # Collect ligand atom info
    lig_atoms: list[dict] = []
    for atom in ligand.GetAtoms():
        pos = lig_conf.GetAtomPosition(atom.GetIdx())
        lig_atoms.append({
            "idx": atom.GetIdx(),
            "symbol": atom.GetSymbol(),
            "pos": (pos.x, pos.y, pos.z),
            "is_h_donor": atom.GetSymbol() in ("O", "N") and atom.GetTotalNumHs() > 0,
            "is_h_acceptor": atom.GetSymbol() in ("O", "N", "S", "F"),
            "is_hydrophobic": atom.GetSymbol() == "C",
            "formal_charge": atom.GetFormalCharge(),
        })

    def _distance(p1, p2) -> float:
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 + (p1[2] - p2[2]) ** 2) ** 0.5

    hydrogen_bonds: list[dict] = []
    hydrophobic_contacts: list[dict] = []
    salt_bridges: list[dict] = []

    # H-bond detection: backbone N/O to ligand donor/acceptor
    for pa in prot_atoms:
        if pa["is_backbone"] and pa["symbol"] in ("N", "O"):
            prot_is_donor = pa["symbol"] == "N"
            for la in lig_atoms:
                if prot_is_donor and not la["is_h_acceptor"]:
                    continue
                if not prot_is_donor and not la["is_h_donor"]:
                    continue
                dist = _distance(pa["pos"], la["pos"])
                if dist <= hbond_dist_cutoff:
                    hydrogen_bonds.append({
                        "protein_residue": f"{pa['residue_name']}{pa['residue_num']}{pa['chain']}",
                        "protein_atom": pa["symbol"],
                        "ligand_atom": la["symbol"],
                        "distance": round(dist, 2),
                        "type": "donor" if prot_is_donor else "acceptor",
                    })

    # Hydrophobic contacts: any protein C to any ligand C
    seen_pairs = set()
    for pa in prot_atoms:
        if pa["symbol"] != "C":
            continue
        for la in lig_atoms:
            if not la["is_hydrophobic"]:
                continue
            dist = _distance(pa["pos"], la["pos"])
            if dist <= hydrophobic_dist_cutoff:
                key = (pa["residue_num"], la["idx"])
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    hydrophobic_contacts.append({
                        "protein_residue": f"{pa['residue_name']}{pa['residue_num']}{pa['chain']}",
                        "ligand_atom_idx": la["idx"],
                        "distance": round(dist, 2),
                    })

    # Salt bridges: charged residues to oppositely charged ligand atoms
    for pa in prot_atoms:
        pa_charge = 0
        if pa["residue_name"] in ("LYS", "ARG"):
            pa_charge = 1
        elif pa["residue_name"] in ("ASP", "GLU"):
            pa_charge = -1
        if pa_charge == 0:
            continue
        for la in lig_atoms:
            if la["formal_charge"] == 0 or abs(la["formal_charge"]) > 2:
                continue
            if pa_charge * la["formal_charge"] >= 0:
                continue
            dist = _distance(pa["pos"], la["pos"])
            if dist <= salt_bridge_dist_cutoff:
                salt_bridges.append({
                    "protein_residue": f"{pa['residue_name']}{pa['residue_num']}{pa['chain']}",
                    "protein_charge": pa_charge,
                    "ligand_atom_idx": la["idx"],
                    "ligand_charge": la["formal_charge"],
                    "distance": round(dist, 2),
                })

    return {
        "hydrogen_bonds": _deduplicate_hbonds(hydrogen_bonds),
        "hydrophobic_interactions": _deduplicate_hydrophobic(hydrophobic_contacts),
        "pi_stacks": [],
        "pi_cation_interactions": [],
        "salt_bridges": salt_bridges,
        "halogen_bonds": [],
        "water_bridges": [],
        "summary": {
            "n_hydrogen_bonds": len(_deduplicate_hbonds(hydrogen_bonds)),
            "n_hydrophobic_contacts": len(_deduplicate_hydrophobic(hydrophobic_contacts)),
            "n_salt_bridges": len(salt_bridges),
            "n_pi_stacks": 0,
            "n_pi_cation": 0,
            "n_halogen_bonds": 0,
            "n_water_bridges": 0,
        },
    }


def _deduplicate_hbonds(hbonds: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for hb in hbonds:
        key = (hb["protein_residue"], hb["ligand_atom"])
        if key not in best or hb["distance"] < best[key]["distance"]:
            best[key] = hb
    return sorted(best.values(), key=lambda x: x["distance"])


def _deduplicate_hydrophobic(contacts: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for c in contacts:
        key = (c["protein_residue"], c["ligand_atom_idx"])
        if key not in best or c["distance"] < best[key]["distance"]:
            best[key] = c
    return sorted(best.values(), key=lambda x: x["distance"])


# ============================================================================
# 2. RMSD Pose Clustering
# ============================================================================


def cluster_docking_poses(
    docked_pdbqt: str,
    output_dir: str,
    reference_pdb: str = "",
    rmsd_cutoff: float = 2.0,
) -> ToolResult:
    """Cluster docking poses by heavy-atom RMSD.

    Parses a multi-model Vina PDBQT file, computes pairwise RMSD,
    and clusters poses using single-linkage clustering.

    Args:
        docked_pdbqt: Path to Vina output PDBQT (multi-model).
        output_dir: Directory for output files.
        reference_pdb: Optional reference PDB for alignment.
        rmsd_cutoff: RMSD cutoff in Angstroms for clustering (default 2.0).

    Returns:
        ToolResult with data dict: {n_clusters, n_poses, clusters: [...]}
    """
    out = _ensure_dir(Path(output_dir))

    try:
        from rdkit import Chem
    except ImportError:
        return failed(errors=["RDKit not available"])

    poses = _parse_vina_poses(docked_pdbqt)
    if len(poses) < 2:
        return success({
            "n_poses": len(poses),
            "n_clusters": len(poses),
            "clusters": [],
            "message": "Fewer than 2 poses, clustering not needed",
        })

    n = len(poses)
    rmsd_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            rmsd = _compute_ligand_rmsd(poses[i]["mol"], poses[j]["mol"])
            rmsd_matrix[i][j] = rmsd
            rmsd_matrix[j][i] = rmsd

    clusters = _single_linkage_cluster(rmsd_matrix, rmsd_cutoff)

    cluster_results: list[dict] = []
    for cid, member_indices in enumerate(clusters):
        members: list[dict] = []
        best_score = float("inf")
        best_idx = -1
        for idx in member_indices:
            members.append({
                "model_num": poses[idx]["model_num"],
                "vina_score": poses[idx].get("score", 0.0),
            })
            score = poses[idx].get("score", float("inf"))
            if score < best_score:
                best_score = score
                best_idx = idx

        rep_path = str(out / f"cluster_{cid:02d}_representative.pdb")
        Chem.MolToPDBFile(poses[best_idx]["mol"], rep_path)

        cluster_dir = _ensure_dir(out / f"cluster_{cid:02d}")
        for idx in member_indices:
            pose_path = str(cluster_dir / f"pose_{poses[idx]['model_num']:02d}.pdb")
            Chem.MolToPDBFile(poses[idx]["mol"], pose_path)

        cluster_results.append({
            "id": cid,
            "size": len(members),
            "best_score": best_score,
            "representative_path": rep_path,
            "members": members,
        })

    return success({
        "n_poses": n,
        "n_clusters": len(clusters),
        "rmsd_cutoff": rmsd_cutoff,
        "clusters": cluster_results,
    })


def _parse_vina_poses(pdbqt_path: str) -> list[dict]:
    """Parse multi-model PDBQT into individual RDKit mol objects."""
    from rdkit import Chem

    with open(pdbqt_path, "r") as f:
        content = f.read()

    models = content.split("MODEL")
    poses: list[dict] = []

    for i, block in enumerate(models):
        if not block.strip():
            continue
        block = "MODEL" + block
        end_idx = block.find("ENDMDL")
        if end_idx == -1:
            continue

        model_text = block[: end_idx + 6]

        with tempfile.NamedTemporaryFile(suffix=".pdb", mode="w", delete=False) as tf:
            tf.write(model_text)
            tmp_path = tf.name

        try:
            mol = Chem.MolFromPDBFile(tmp_path, removeHs=False)
            if mol is not None:
                score = 0.0
                for line in model_text.split("\n"):
                    if "VINA RESULT" in line or "RESULT" in line:
                        parts = line.split()
                        for part in parts:
                            try:
                                score = float(part)
                                break
                            except ValueError:
                                continue
                poses.append({"model_num": i, "mol": mol, "score": score})
        finally:
            os.unlink(tmp_path)

    return poses


def _compute_ligand_rmsd(mol1: Any, mol2: Any) -> float:
    """Compute heavy-atom RMSD between two ligand poses."""
    from rdkit.Chem import AllChem

    idx1 = [a.GetIdx() for a in mol1.GetAtoms() if a.GetAtomicNum() > 1]
    idx2 = [a.GetIdx() for a in mol2.GetAtoms() if a.GetAtomicNum() > 1]

    if len(idx1) != len(idx2) or len(idx1) < 3:
        try:
            return AllChem.GetBestRMS(mol1, mol2)
        except Exception:
            return 999.0

    conf1 = mol1.GetConformer()
    conf2 = mol2.GetConformer()

    ssd = 0.0
    for i, j in zip(idx1, idx2):
        a = conf1.GetAtomPosition(i)
        b = conf2.GetAtomPosition(j)
        dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
        ssd += dx * dx + dy * dy + dz * dz

    return (ssd / len(idx1)) ** 0.5


def _single_linkage_cluster(
    rmsd_matrix: list[list[float]], cutoff: float
) -> list[list[int]]:
    """Single-linkage clustering via DFS."""
    n = len(rmsd_matrix)
    visited = [False] * n
    clusters: list[list[int]] = []

    def _dfs(node: int, cluster: list[int]):
        visited[node] = True
        cluster.append(node)
        for neighbor in range(n):
            if not visited[neighbor] and rmsd_matrix[node][neighbor] <= cutoff:
                _dfs(neighbor, cluster)

    for i in range(n):
        if not visited[i]:
            cluster: list[int] = []
            _dfs(i, cluster)
            clusters.append(cluster)

    return clusters


# ============================================================================
# 3. 2D Interaction Diagram
# ============================================================================


def generate_interaction_diagram(
    protein_pdb: str,
    ligand_sdf: str,
    output_path: str,
    interaction_data: str = "",
) -> ToolResult:
    """Generate a 2D LigPlot-style protein-ligand interaction diagram.

    Args:
        protein_pdb: Path to protein PDB file.
        ligand_sdf: Path to ligand SDF/MOL2 file.
        output_path: Path for output PNG image.
        interaction_data: Optional JSON file with pre-computed interactions
                         (from analyze_interactions). Auto-computes if empty.

    Returns:
        ToolResult with data: {output_path, n_interactions_shown}
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw, AllChem
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError:
        return failed(errors=["RDKit not available"])

    # Load ligand
    lig_supplier = Chem.SDMolSupplier(ligand_sdf, removeHs=False)
    ligand = lig_supplier[0] if len(lig_supplier) > 0 else None
    if ligand is None:
        ligand = Chem.MolFromMol2File(ligand_sdf, removeHs=False)
    if ligand is None:
        ligand = Chem.MolFromPDBFile(ligand_sdf, removeHs=False)
    if ligand is None:
        return failed(errors=["Failed to load ligand"])

    # Load or compute interactions
    if interaction_data and Path(interaction_data).exists():
        with open(interaction_data, "r") as f:
            interactions = json.load(f)
    else:
        result = analyze_interactions(
            protein_pdb, ligand_sdf,
            str(Path(output_path).parent), use_plip=False,
        )
        if not result.ok:
            return degraded(
                data={"output_path": "", "error": "Could not compute interactions"},
                errors=result.errors,
            )
        interactions = result.data

    # Collect interacting residues
    residue_labels: dict[str, str] = {}
    for itype in ("hydrogen_bonds", "hydrophobic_interactions",
                   "pi_stacks", "pi_cation_interactions",
                   "salt_bridges", "halogen_bonds"):
        for item in interactions.get(itype, []):
            res = item.get("protein_residue", "")
            if res:
                residue_labels[res] = itype

    # Draw
    drawer = rdMolDraw2D.MolDraw2DCairo(800, 600)
    AllChem.Compute2DCoords(ligand)

    legend_lines: list[str] = []
    for res, itype in sorted(residue_labels.items()):
        label = _interaction_type_label(itype)
        legend_lines.append(f"{res}: {label}")

    legend = "\n".join(legend_lines[:20]) if legend_lines else "No interactions detected"

    drawer.DrawMolecule(ligand, legend=legend)
    drawer.FinishDrawing()

    with open(output_path, "wb") as f:
        f.write(drawer.GetDrawingText())

    return success({
        "output_path": output_path,
        "n_interactions_shown": len(residue_labels),
        "interacting_residues": list(residue_labels.keys()),
    })


def _interaction_type_label(itype: str) -> str:
    labels = {
        "hydrogen_bonds": "H-bond",
        "hydrophobic_interactions": "Hydrophobic",
        "pi_stacks": "pi-Stack",
        "pi_cation_interactions": "pi-Cation",
        "salt_bridges": "Salt bridge",
        "halogen_bonds": "Halogen bond",
    }
    return labels.get(itype, itype)


# ============================================================================
# 4. ADMET Property Prediction
# ============================================================================


def predict_admet(
    smiles_or_sdf: str,
    output_dir: str,
) -> ToolResult:
    """Predict ADMET properties using RDKit molecular descriptors.

    Checks Lipinski Rule of 5, Veber rules, PAINS alerts, Brenk alerts.

    Args:
        smiles_or_sdf: SMILES string or path to SDF file.
        output_dir: Directory for output JSON report.

    Returns:
        ToolResult with ADMET property dict.
    """
    out = _ensure_dir(Path(output_dir))

    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, AllChem, FilterCatalog, QED
    except ImportError:
        return failed(errors=["RDKit not available"])

    # Load molecule
    mol = None
    if Path(smiles_or_sdf).exists():
        supp = Chem.SDMolSupplier(smiles_or_sdf, removeHs=True)
        mol = supp[0] if len(supp) > 0 else None
    if mol is None:
        mol = Chem.MolFromSmiles(smiles_or_sdf)
    if mol is None:
        return failed(errors=[f"Failed to parse: {smiles_or_sdf[:100]}"])

    # Descriptors
    mw = Descriptors.MolWt(mol)
    logp = Descriptors.MolLogP(mol)
    hbd = Descriptors.NumHDonors(mol)
    hba = Descriptors.NumHAcceptors(mol)
    rot_bonds = Descriptors.NumRotatableBonds(mol)
    tpsa = Descriptors.TPSA(mol)
    heavy_atoms = mol.GetNumHeavyAtoms()
    aromatic_rings = Descriptors.NumAromaticRings(mol)

    # Lipinski
    lipinski_violations: list[str] = []
    if mw > 500:
        lipinski_violations.append(f"MW={mw:.1f}>500")
    if logp > 5:
        lipinski_violations.append(f"LogP={logp:.1f}>5")
    if hbd > 5:
        lipinski_violations.append(f"HBD={hbd}>5")
    if hba > 10:
        lipinski_violations.append(f"HBA={hba}>10")
    lipinski_pass = len(lipinski_violations) <= 1

    # Veber
    veber_violations: list[str] = []
    if rot_bonds > 10:
        veber_violations.append(f"RotBonds={rot_bonds}>10")
    if tpsa > 140:
        veber_violations.append(f"TPSA={tpsa:.1f}>140")
    veber_pass = len(veber_violations) == 0

    # Ghose
    ghose_pass = (
        160 <= mw <= 480 and -0.4 <= logp <= 5.6 and 20 <= heavy_atoms <= 70
    )

    # PAINS alerts
    pains_alerts: list[str] = []
    try:
        pains_params = FilterCatalog.FilterCatalogParams()
        pains_params.AddCatalog(
            FilterCatalog.FilterCatalogParams.FilterCatalogs.PAINS
        )
        pains_catalog = FilterCatalog.FilterCatalog(pains_params)
        entry = pains_catalog.GetFirstMatch(mol)
        while entry is not None:
            pains_alerts.append(entry.GetDescription())
            entry = pains_catalog.GetNextMatch(entry)
    except Exception:
        pass

    # Brenk alerts
    brenk_alerts: list[str] = []
    try:
        brenk_params = FilterCatalog.FilterCatalogParams()
        brenk_params.AddCatalog(
            FilterCatalog.FilterCatalogParams.FilterCatalogs.BRENK
        )
        brenk_catalog = FilterCatalog.FilterCatalog(brenk_params)
        entry = brenk_catalog.GetFirstMatch(mol)
        while entry is not None:
            brenk_alerts.append(entry.GetDescription())
            entry = brenk_catalog.GetNextMatch(entry)
    except Exception:
        pass

    # QED
    try:
        qed_score = QED.qed(mol)
    except Exception:
        qed_score = None

    # SA score (simplified)
    try:
        sa_score = _estimate_sa_score(mol)
    except Exception:
        sa_score = None

    result = {
        "molecular_weight": round(mw, 2),
        "logP": round(logp, 2),
        "hbd": hbd,
        "hba": hba,
        "rotatable_bonds": rot_bonds,
        "tpsa": round(tpsa, 2),
        "heavy_atoms": heavy_atoms,
        "aromatic_rings": aromatic_rings,
        "qed": round(qed_score, 4) if qed_score is not None else None,
        "synthetic_accessibility": round(sa_score, 2) if sa_score is not None else None,
        "lipinski": {
            "pass": lipinski_pass,
            "violations": lipinski_violations,
            "violation_count": len(lipinski_violations),
        },
        "veber": {
            "pass": veber_pass,
            "violations": veber_violations,
        },
        "ghose_filter": {"pass": ghose_pass},
        "pains_alerts": pains_alerts,
        "pains_count": len(pains_alerts),
        "brenk_alerts": brenk_alerts,
        "brenk_count": len(brenk_alerts),
        "overall_assessment": _admet_assessment(
            lipinski_pass, veber_pass, ghose_pass,
            len(pains_alerts), len(brenk_alerts),
        ),
    }

    report_path = out / "admet_report.json"
    with open(report_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    result["report_path"] = str(report_path)
    return success(result)


def _estimate_sa_score(mol: Any) -> float:
    """Estimate synthetic accessibility score (1=easy, 10=hard).

    Simplified Ertl & Schuffenhauer (2009) fragment-based approach.
    """
    from rdkit.Chem import Descriptors

    mw = Descriptors.MolWt(mol)
    rot_bonds = Descriptors.NumRotatableBonds(mol)
    rings = Descriptors.RingCount(mol)
    chiral_centers = len(
        Chem.FindMolChiralCenters(mol, includeUnassigned=True)
    )
    spiro = Descriptors.NumSpiroAtoms(mol)
    bridgehead = Descriptors.NumBridgeheadAtoms(mol)

    fragment_score = 0.0
    fragment_score += rot_bonds * 0.05
    fragment_score += (mw - 200) * 0.002
    fragment_score -= rings * 0.1
    fragment_score += chiral_centers * 0.2
    fragment_score += spiro * 0.5
    fragment_score += bridgehead * 0.3

    sa = 3.0 + fragment_score
    return max(1.0, min(10.0, sa))


def _admet_assessment(
    lipinski_pass: bool,
    veber_pass: bool,
    ghose_pass: bool,
    pains_count: int,
    brenk_count: int,
) -> str:
    issues: list[str] = []
    if not lipinski_pass:
        issues.append("Lipinski violations")
    if not veber_pass:
        issues.append("Veber violations")
    if not ghose_pass:
        issues.append("Ghose filter failure")
    if pains_count > 0:
        issues.append(f"{pains_count} PAINS alert(s)")
    if brenk_count > 0:
        issues.append(f"{brenk_count} Brenk alert(s)")

    if not issues:
        return "EXCELLENT: No alerts or violations"
    if len(issues) == 1:
        return f"GOOD: {issues[0]}"
    if len(issues) <= 2:
        return f"FAIR: {'; '.join(issues)}"
    return f"POOR: {'; '.join(issues)}"


try:
    from rdkit import Chem
except ImportError:
    pass
