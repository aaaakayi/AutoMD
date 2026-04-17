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

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "dock_agent_prompt.txt"

DOCK_ALLOWED_FUNCTIONS = (
    "get_docking_box_from_p2rank",
    "dock",
    "setup_environment",
    "web_search",
    "read_text_file",
    "write_text_file",
    "run_shell_command",
    "read_error_report",
)


def create_dock_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="dock_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=DOCK_ALLOWED_FUNCTIONS,
        model_client=model_client,
        strict_mode=False,
    )


async def execute_dock_task(task: str) -> str:
    model_client = create_model_client()
    try:
        agent = create_dock_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[dock_agent]",
            verbose=True,
            allowed_functions=DOCK_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()


if __name__ == "__main__":
    print(asyncio.run(execute_dock_task(
        "请执行一次示例分子对接任务，蛋白质pdbqt文件在./output/prepared/1AKE_protein_clean.pdbqt，配体pdbqt文件在./aspirin_output/asp.pdbqt，输出对接结果到./output/dock/目录下。"
        ))
    )
