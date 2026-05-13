import json
from typing import Any, Dict, Iterable, List, Optional

from tools.shared import ToolResult

from tools.dock import dock, get_docking_box_from_p2rank

from tools.protein import fetch_pdb,prepare_pure_protein,run_prepare_receptor4_py
from tools.ligand import prepare_ligand_amber_route
from tools.search import web_search
from tools.set_env import setup_environment
from tools.system_tools import read_error_report, read_text_file, run_shell_command, write_text_file


def _parse_list(value: str) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return [x.strip() for x in value.split(",") if x.strip()]


def _parse_dict(value: str) -> Dict[str, str]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass
    return {}


def _none_if_empty(value: str) -> Optional[str]:
    return value if value else None


def fetch_pdb_strict(pdb_id: str, output_dir: str) -> str:
    result = fetch_pdb(pdb_id=pdb_id, output_dir=output_dir)
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def prepare_pure_protein_strict(pdb_id: str, output_root: str) -> Dict[str, Any]:
    return prepare_pure_protein(pdb_id=pdb_id, output_root=output_root)


def prepare_ligand_amber_route_strict(
    input_smiles: str = "",
    input_pdb: str = "",
    input_file: str = "",
    input_format: str = "",
    output_dir: str = "./ligand_output",
    net_charge: int = 0,
    residue_name: str = "LIG",
    charge_method: str = "bcc",
    force_field: str = "gaff2",
    antechamber_extra_args_json: str = "",
    antechamber_kwargs_json: str = "",
    antechamber_intermediate_dir: str = "",
    robust_input: bool = True,
    fallback_charge_methods_json: str = "",
    generate_pdbqt: bool = True,
    generate_md_files: bool = True,
    generate_gmx_files: bool = False,
    protein_pdb: str = "",
) -> Dict[str, Any]:
    extra_args = _parse_list(antechamber_extra_args_json)
    raw_kwargs = _parse_dict(antechamber_kwargs_json)
    fallback_methods = _parse_list(fallback_charge_methods_json)

    return prepare_ligand_amber_route(
        input_smiles=_none_if_empty(input_smiles),
        input_pdb=_none_if_empty(input_pdb),
        input_file=_none_if_empty(input_file),
        input_format=_none_if_empty(input_format),
        output_dir=output_dir,
        net_charge=net_charge,
        residue_name=residue_name,
        charge_method=charge_method,
        force_field=force_field,
        antechamber_extra_args=extra_args or None,
        antechamber_kwargs=raw_kwargs or None,
        antechamber_intermediate_dir=_none_if_empty(antechamber_intermediate_dir),
        robust_input=robust_input,
        fallback_charge_methods=fallback_methods or None,
        generate_pdbqt=generate_pdbqt,
        generate_md_files=generate_md_files,
        generate_gmx_files=generate_gmx_files,
        protein_pdb=_none_if_empty(protein_pdb),
    )


def setup_environment_strict(packages_json: str, method: str, target_env: str) -> str:
    packages = _parse_list(packages_json)
    env = _none_if_empty(target_env)
    result = setup_environment(packages=packages or None, method=method, target_env=env)
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def web_search_strict(query: str, num_results: int) -> str:
    return web_search(query=query, num_results=num_results)


def get_docking_box_from_p2rank_strict(
    protein_pdb: str,
    output_dir: str,
    use_getbox: bool,
    extension: float,
    fallback_to_simple: bool,
) -> str:
    result = get_docking_box_from_p2rank(
        protein_pdb=protein_pdb,
        output_dir=output_dir,
        use_getbox=use_getbox,
        extension=extension,
        fallback_to_simple=fallback_to_simple,
    )
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def dock_strict(
    protein_file: str,
    ligand_file: str,
    output_dir: str,
    center_x: str,
    center_y: str,
    center_z: str,
    size_x: float,
    size_y: float,
    size_z: float,
    exhaustiveness: int,
    num_modes: int,
    energy_range: float,
) -> str:
    cx = None if center_x == "" else float(center_x)
    cy = None if center_y == "" else float(center_y)
    cz = None if center_z == "" else float(center_z)
    result = dock(
        protein_file=protein_file,
        ligand_file=ligand_file,
        output_dir=output_dir,
        center_x=cx,
        center_y=cy,
        center_z=cz,
        size_x=size_x,
        size_y=size_y,
        size_z=size_z,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        energy_range=energy_range,
    )
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def read_text_file_strict(path: str, max_chars: int, allow_outside_project: bool, encoding: str) -> str:
    result = read_text_file(
        path=path,
        max_chars=max_chars,
        allow_outside_project=allow_outside_project,
        encoding=encoding,
    )
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def write_text_file_strict(path: str, content: str, mode: str, allow_outside_project: bool, encoding: str) -> str:
    result = write_text_file(
        path=path,
        content=content,
        mode=mode,
        allow_outside_project=allow_outside_project,
        encoding=encoding,
    )
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def run_shell_command_strict(
    command: str,
    cwd: str,
    timeout_seconds: int,
    max_output_chars: int,
    env_json: str,
) -> str:
    env = _parse_dict(env_json)
    result = run_shell_command(
        command=command,
        cwd=_none_if_empty(cwd),
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
        env=env or None,
    )
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)


def read_error_report_strict(log_path: str, raw_error_text: str, max_chars: int, allow_outside_project: bool) -> str:
    result = read_error_report(
        log_path=_none_if_empty(log_path),
        raw_error_text=_none_if_empty(raw_error_text),
        max_chars=max_chars,
        allow_outside_project=allow_outside_project,
    )
    if isinstance(result, ToolResult):
        return result.format_for_agent()
    return str(result)

def run_prepare_receptor4_py_strict(
    input_pdb: str,
    output_pdbqt: str
) -> ToolResult:
    return run_prepare_receptor4_py(
        input_pdb=input_pdb,
        output_pdbqt=output_pdbqt
    )

STRICT_TOOL_MAP = {
    "fetch_pdb": fetch_pdb_strict,
    "prepare_pure_protein": prepare_pure_protein_strict,
    "prepare_ligand_amber_route": prepare_ligand_amber_route_strict,
    "setup_environment": setup_environment_strict,
    "web_search": web_search_strict,
    "get_docking_box_from_p2rank": get_docking_box_from_p2rank_strict,
    "dock": dock_strict,

    "read_text_file": read_text_file_strict,
    "write_text_file": write_text_file_strict,
    "run_shell_command": run_shell_command_strict,
    "read_error_report": read_error_report_strict,
    "run_prepare_receptor4_py": run_prepare_receptor4_py_strict
}
