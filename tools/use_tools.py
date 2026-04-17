import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from autogen_core.tools import FunctionTool

from tools.ligand import prepare_ligand_amber_route
from tools.set_env import setup_environment
from tools.search import web_search
from tools.dock import dock, get_docking_box_from_p2rank
from tools.system_tools import read_text_file, write_text_file, run_shell_command, read_error_report
from tools.strict_wrappers import STRICT_TOOL_MAP
from tools.protein import fetch_pdb,prepare_pure_protein,run_prepare_receptor4_py
from tools.protein_ensemble import get_protein_ensemble


TOOL_MAP = {
    "fetch_pdb": fetch_pdb,
    "prepare_pure_protein": prepare_pure_protein,
    "prepare_ligand_amber_route": prepare_ligand_amber_route,
    "setup_environment": setup_environment,
    "web_search": web_search,
    "get_docking_box_from_p2rank": get_docking_box_from_p2rank,
    "dock": dock,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
    "run_shell_command": run_shell_command,
    "read_error_report": read_error_report,
    "run_prepare_receptor4_py" : run_prepare_receptor4_py,
    "get_protein_ensemble": get_protein_ensemble,
}


def get_tool_map(*, strict_mode: bool = False) -> Dict[str, Any]:
    """Get tool map by mode. strict_mode=True uses strict-compatible wrappers."""
    return STRICT_TOOL_MAP if strict_mode else TOOL_MAP

def sanitize_text(text: str) -> str:
    """移除非法 surrogate 字符，避免请求序列化时 UTF-8 编码失败。"""
    return "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))


def sanitize_json_like(value: Any) -> Any:
    """递归清洗 JSON 结构中的字符串字段。"""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_json_like(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_json_like(v) for k, v in value.items()}
    return value


def load_tool_registry(file_path: Path) -> List[Dict[str, Any]]:
    """加载并校验工具元数据配置。"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data = sanitize_json_like(data)

    def _validate_tool_item(item: Dict[str, Any], source_label: str) -> Dict[str, Any]:
        if "name" not in item or "function" not in item or "description" not in item:
            raise ValueError(f"工具配置 {source_label} 缺少必填字段：name/function/description。")
        if "parameters" in item and not isinstance(item["parameters"], list):
            raise ValueError(f"工具配置 {source_label} 的 parameters 必须是数组。")
        return item

    def _load_node(node: Any, current_dir: Path, source_label: str) -> List[Dict[str, Any]]:
        node = sanitize_json_like(node)

        if isinstance(node, list):
            tools: List[Dict[str, Any]] = []
            for index, item in enumerate(node):
                if not isinstance(item, dict):
                    raise ValueError(f"工具配置 {source_label} 第 {index + 1} 项必须是对象。")
                tools.append(_validate_tool_item(item, f"{source_label} 第 {index + 1} 项"))
            return tools

        if not isinstance(node, dict):
            raise ValueError(f"工具配置 {source_label} 格式错误：根节点必须是数组或对象。")

        # 单工具文件：直接定义 name/function/description
        if {"name", "function", "description"}.issubset(node.keys()):
            return [_validate_tool_item(node, source_label)]

        # 模块化索引：支持 include/file/path 引入单工具文件
        include_ref = node.get("include") or node.get("file") or node.get("path")
        if include_ref:
            include_path = (current_dir / include_ref).resolve()
            if not include_path.exists():
                raise FileNotFoundError(f"工具配置引用文件不存在: {include_path}")
            return load_tool_registry(include_path)

        tools: List[Dict[str, Any]] = []

        # 兼容 categories 结构
        categories = node.get("categories")
        if isinstance(categories, list):
            for cat_index, category in enumerate(categories):
                tools.extend(_load_node(category, current_dir, f"{source_label}.categories[{cat_index + 1}]") )
            return tools

        # 兼容 tools 结构
        nested_tools = node.get("tools")
        if isinstance(nested_tools, list):
            for tool_index, item in enumerate(nested_tools):
                tools.extend(_load_node(item, current_dir, f"{source_label}.tools[{tool_index + 1}]") )
            return tools

        # 兼容 category / group 结构：category 本身带 tools
        if isinstance(node.get("tools"), list):
            for tool_index, item in enumerate(node["tools"]):
                tools.extend(_load_node(item, current_dir, f"{source_label}.tools[{tool_index + 1}]") )
            return tools

        raise ValueError(
            f"工具配置 {source_label} 格式无法识别：需要是工具列表、单工具对象，或包含 include/categories/tools 的索引对象。"
        )

    loaded = _load_node(data, file_path.parent, file_path.name)

    deduped: List[Dict[str, Any]] = []
    seen_functions = set()
    for item in loaded:
        function_key = item.get("function")
        if function_key in seen_functions:
            continue
        seen_functions.add(function_key)
        deduped.append(item)

    return deduped


def build_tool_description(tool_meta: Dict[str, Any]) -> str:
    """将结构化工具元数据拼接为工程化说明文本。"""
    lines = [
        f"工具名: {tool_meta['name']}",
        f"能力描述: {tool_meta['description']}",
    ]

    params = tool_meta.get("parameters", [])
    if not params:
        lines.append("参数定义: 无")
        return "\n".join(lines)

    lines.append("参数定义:")
    for param in params:
        param_name = param.get("name", "unknown")
        param_type = param.get("type", "any")
        required = "必填" if param.get("required", False) else "可选"
        default = param.get("default")
        default_text = "" if default is None else f", 默认值={default}"
        detail = param.get("description", "")
        lines.append(f"- {param_name} ({param_type}, {required}{default_text}): {detail}")

    return sanitize_text("\n".join(lines))


def build_tools_from_registry(
    file_path: Path,
    tool_map: Optional[Dict[str, Any]] = None,
    *,
    strict_mode: bool = False,
) -> List[FunctionTool]:
    """根据配置文件动态创建 FunctionTool 列表。"""
    registry = load_tool_registry(file_path)
    tools: List[FunctionTool] = []
    active_tool_map = tool_map or get_tool_map(strict_mode=strict_mode)

    for item in registry:
        function_key = item["function"]
        func = active_tool_map.get(function_key)
        if func is None:
            raise KeyError(f"工具配置中的 function 未在 TOOL_MAP 中注册: {function_key}")

        tools.append(
            FunctionTool(
                func,
                description=build_tool_description(item),
                strict=strict_mode,
            )
        )

    return tools