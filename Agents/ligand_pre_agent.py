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

LIGAND_ALLOWED_FUNCTIONS = (
    "fetch_pdb",
    "prepare_ligand_amber_route",
    "setup_environment",
    "web_search",
    "read_text_file",
    "write_text_file",
    "run_shell_command",
    "read_error_report",
)
SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "ligand_pre_agent_prompt.txt"


def create_ligand_pre_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="ligand_pre_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=LIGAND_ALLOWED_FUNCTIONS,
        model_client=model_client,
        strict_mode=False,
    )


async def execute_ligand_pre_task(task: str) -> str:
    model_client = create_model_client()
    try:
        agent = create_ligand_pre_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[ligand_pre_agent]",
            verbose=True,
            allowed_functions=LIGAND_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()


if __name__ == "__main__":
    task_description = "配体分子的SMILES字符串是 CC(=O)Oc1ccccc1C(=O)O，请准备好配体文件"
    print(asyncio.run(execute_ligand_pre_task(task_description)))
