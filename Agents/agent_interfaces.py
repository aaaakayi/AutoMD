from .dock_agent import execute_dock_task
from .env_setup_agent import execute_env_setup_task
from .memory_agent import execute_memory_task
from .ligand_pre_agent import execute_ligand_pre_task
from .protein_pre_agent import execute_protein_pre_task
from .protein_evolution_agent import execute_protein_evolution_task


async def execute_agent_task(agent_name: str, task: str) -> str:
    """Unified async interface for main.py to call any agent and get text output."""
    normalized = (agent_name or "").strip().lower()

    if normalized in {"env", "env_setup", "env_setup_agent"}:
        return await execute_env_setup_task(task)
    if normalized in {"protein", "protein_pre", "protein_pre_agent"}:
        return await execute_protein_pre_task(task)
    if normalized in {"protein_evolution", "protein_review", "protein_evolution_agent"}:
        return await execute_protein_evolution_task(task)
    if normalized in {"ligand", "ligand_pre", "ligand_pre_agent"}:
        return await execute_ligand_pre_task(task)
    if normalized in {"dock", "dock_agent"}:
        return await execute_dock_task(task)
    if normalized in {"memory", "memory_agent", "progress", "progress_agent"}:
        return await execute_memory_task(task)

    supported = ["env_setup_agent", "protein_pre_agent", "protein_evolution_agent", "ligand_pre_agent", "dock_agent", "memory_agent"]
    raise ValueError(f"Unknown agent_name: {agent_name}. Supported values: {supported}")


