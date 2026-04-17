import asyncio
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from .common import create_model_client, create_executor_agent
    from .dsml_bridge import run_agent_with_dsml_visualization
except ImportError:
    from common import create_model_client, create_executor_agent
    from dsml_bridge import run_agent_with_dsml_visualization

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "protein_evolution_agent_prompt.txt"

PROTEIN_EVOLUTION_ALLOWED_FUNCTIONS = (
    "read_text_file",
    "read_error_report",
    "run_shell_command",
    "web_search",
    "write_text_file",
    "setup_environment",
)


def create_protein_evolution_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="protein_evolution_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=PROTEIN_EVOLUTION_ALLOWED_FUNCTIONS,
        model_client=model_client,
        strict_mode=False,
    )


def _extract_last_agent_text(run_result: Any, agent_name: str) -> str:
    for msg in reversed(getattr(run_result, "messages", [])):
        if getattr(msg, "source", "") != agent_name or not hasattr(msg, "content"):
            continue
        content = msg.content
        if isinstance(content, str):
            return content
        return str(content)
    raise ValueError("protein_evolution_agent 未返回可用文本结果")


async def execute_protein_evolution_task(task: str, verbose: bool = True) -> str:
    model_client = create_model_client()
    try:
        agent = create_protein_evolution_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[protein_evolution_agent]",
            verbose=verbose,
            allowed_functions=PROTEIN_EVOLUTION_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()


async def _demo() -> None:
    result = await execute_protein_evolution_task(
        "审查 protein_pre_agent 当前实现，找出可进化点，并给出最小可行改进方案。",
    )
    print("\n===== protein_evolution_agent 输出 =====")
    print(result)


if __name__ == "__main__":
    asyncio.run(_demo())