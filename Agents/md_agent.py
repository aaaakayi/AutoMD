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

SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "md_agent_prompt.txt"

MD_ALLOWED_FUNCTIONS = (
    "run_md_simulation",
    "read_text_file",
    "write_text_file",
    "run_shell_command",
    "read_error_report",
)


def create_md_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="md_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=MD_ALLOWED_FUNCTIONS,
        model_client=model_client,
        strict_mode=False,
    )


async def execute_md_task(task: str) -> str:
    model_client = create_model_client()
    try:
        agent = create_md_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[md_agent]",
            verbose=True,
            allowed_functions=MD_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()


if __name__ == "__main__":
    print(asyncio.run(execute_md_task(
        "请执行一次蛋白质-配体复合物的MD模拟，使用之前对接得到的最佳pose。"
    )))
