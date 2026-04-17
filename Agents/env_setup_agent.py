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

SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "set_env_agent_prompt.txt"

ENV_ALLOWED_FUNCTIONS = (
    "setup_environment",
    "read_text_file",
    "write_text_file",
    "run_shell_command",
    "read_error_report",
    "web_search",
)


def create_env_setup_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="env_setup_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=ENV_ALLOWED_FUNCTIONS,
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
    raise ValueError("env_setup_agent 未返回可用文本结果")


async def execute_env_setup_task(task: str) -> str:
    model_client = create_model_client()
    try:
        agent = create_env_setup_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[env_setup_agent]",
            verbose=True,
            allowed_functions=ENV_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()


if __name__ == "__main__":
    print(asyncio.run(execute_env_setup_task("请检查并安装依赖环境")))
