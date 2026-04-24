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

SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "long_memory_agent_prompt.txt"

LONG_MEMORY_ALLOWED_FUNCTIONS = (
    "read_text_file",
    "write_text_file"
)

def create_long_memory_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="long_memory_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=LONG_MEMORY_ALLOWED_FUNCTIONS,
        model_client=model_client,
        strict_mode=False,
    )

async def execute_long_memory_task(task: str) -> str:
    model_client = create_model_client()
    try:
        agent = create_long_memory_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[long_memory_agent]",
            verbose=True,
            allowed_functions=LONG_MEMORY_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()

if __name__ == "__main__":
    pass

