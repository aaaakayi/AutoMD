import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent
from autogen_core.model_context import BufferedChatCompletionContext
from autogen_core.tools import FunctionTool
from autogen_ext.models.openai._model_info import ModelInfo

from Agents.dsml_safe_client import DSMLSafeOpenAIClient

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.use_tools import (
    build_tool_description,
    get_tool_map,
    load_tool_registry,
    sanitize_text,
)

TOOL_CONFIG_PATH = PROJECT_ROOT / "Prompts" / "tool_registry.json"

def create_model_client() -> DSMLSafeOpenAIClient:
    """创建模型客户端（带 DSML 防护）。"""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise ValueError("请设置 LLM_API_KEY 环境变量")
    model_name = os.getenv("LLM_MODEL_ID")
    base_url = os.getenv("LLM_BASE_URL")

    model_info = ModelInfo(
        max_tokens=4096,
        input_cost_per_token=0.0,
        output_cost_per_token=0.0,
        vision=False,
        function_calling=True,
        json_output=False,
        structured_output=False,
        # Use the generic family because the installed Autogen OpenAI client
        # cannot round-trip Deepseek thinking-mode / reasoning_content reliably.
        family="unknown"
    )

    client = DSMLSafeOpenAIClient(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        model_info=model_info,
        temperature=0.1,
        timeout=300,
        max_retries=3,
    )

    return client


def load_system_message(file_path: Path) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return sanitize_text(f.read())


def build_selected_tools(
    allowed_functions: Iterable[str],
    tool_map: Optional[Dict[str, Any]] = None,
    registry_path: Optional[Path] = None,
    strict_mode: bool = False,
) -> list[FunctionTool]:
    """Build selected FunctionTool list from registry and allowlist."""
    allowed = set(allowed_functions)
    active_tool_map = tool_map or get_tool_map(strict_mode=strict_mode)
    active_registry = registry_path or TOOL_CONFIG_PATH
    registry = load_tool_registry(active_registry)

    tools: list[FunctionTool] = []
    for item in registry:
        function_key = item.get("function")
        if function_key not in allowed:
            continue
        func = active_tool_map.get(function_key)
        if func is None:
            continue
        tools.append(
            FunctionTool(
                func,
                description=build_tool_description(item),
                strict=strict_mode,
            )
        )
    return tools


def create_executor_agent(
    *,
    agent_name: str,
    system_message_path: Path,
    allowed_functions: Iterable[str],
    model_client: Optional[DSMLSafeOpenAIClient] = None,
    tool_map: Optional[Dict[str, Any]] = None,
    strict_mode: bool = False,
    extra_system_rules: str = "",
    buffer_size: int = 30,
) -> AssistantAgent:
    """Create a reusable assistant with selected tools."""
    model_client = model_client or create_model_client()
    tools = build_selected_tools(
        allowed_functions,
        tool_map=tool_map,
        strict_mode=strict_mode,
    )

    system_prompt = load_system_message(system_message_path)
    if extra_system_rules:
        system_prompt = f"{system_prompt.rstrip()}\n\n{extra_system_rules.strip()}"

    agent_kwargs: Dict[str, Any] = {
        "name": agent_name,
        "model_client": model_client,
        "tools": tools,
        "system_message": system_prompt,
        "model_context": BufferedChatCompletionContext(buffer_size=buffer_size),
        "reflect_on_tool_use": True,
        "max_tool_iterations": 8,
    }

    return AssistantAgent(**agent_kwargs)
