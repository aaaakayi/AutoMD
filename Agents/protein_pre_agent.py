import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from .common import create_model_client, create_executor_agent
    from .protein_evolution_agent import execute_protein_evolution_task
    from .dsml_bridge import run_agent_with_dsml_visualization
except ImportError:
    from common import create_model_client, create_executor_agent
    from protein_evolution_agent import execute_protein_evolution_task
    from dsml_bridge import run_agent_with_dsml_visualization

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYSTEM_MESSAGE_PATH = PROJECT_ROOT / "Prompts" / "protein_pre_agent_prompt.txt"

PROTEIN_ALLOWED_FUNCTIONS = (
    "fetch_pdb",
    "prepare_pure_protein",
    "setup_environment",
    "web_search",
    "read_text_file",
    "write_text_file",
    "run_shell_command",
    "read_error_report",
    "run_prepare_receptor4_py",
    "get_protein_ensemble"
)


def create_protein_pre_agent(model_client=None) -> AssistantAgent:
    return create_executor_agent(
        agent_name="protein_pre_agent",
        system_message_path=SYSTEM_MESSAGE_PATH,
        allowed_functions=PROTEIN_ALLOWED_FUNCTIONS,
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
    raise ValueError("protein_pre_agent 未返回可用文本结果")


def _is_evolution_converged(evolution_result: str) -> bool:
    """Parse evolution output and decide whether iterative optimization has converged."""
    if not evolution_result:
        return False

    marker_match = re.search(
        r"EVOLUTION_CONVERGED\s*[:：]\s*(YES|NO)",
        evolution_result,
        flags=re.IGNORECASE,
    )
    if marker_match:
        return marker_match.group(1).upper() == "YES"

    normalized = evolution_result.lower()
    fallback_negative_keywords = (
        "未收敛",
        "not converged",
        "need more iteration",
        "needs more iteration",
        "继续迭代",
    )
    if any(keyword in normalized for keyword in fallback_negative_keywords):
        return False

    fallback_positive_keywords = (
        "已收敛",
        "达到收敛",
        "无需继续迭代",
        "no further iteration",
        "converged",
    )
    return any(keyword in normalized for keyword in fallback_positive_keywords)


def _snapshot_mtime(paths: list[Path]) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for p in paths:
        try:
            if p.exists() and p.is_file():
                snapshot[str(p.resolve())] = p.stat().st_mtime
        except OSError:
            continue
    return snapshot


def _changed_files_since(before: dict[str, float], after: dict[str, float]) -> list[str]:
    changed: list[str] = []
    for path, ts in after.items():
        if path not in before or before[path] != ts:
            changed.append(path)
    return sorted(changed)


async def execute_protein_pre_task(
    task: str,
    enable_evolution: bool = False,
    max_rounds: int = 5,
    verbose: bool = True,
) -> str:
    if max_rounds <= 0:
        raise ValueError("max_rounds 必须大于 0")

    all_round_outputs: list[str] = []
    rolling_task = task
    last_evolution_result = ""
    evolution_targets = [
        PROJECT_ROOT / "Prompts" / "protein_pre_agent_prompt.txt",
        PROJECT_ROOT / "Prompts" / "tool_registry.json",
        PROJECT_ROOT / "Agents" / "protein_pre_agent.py",
    ]

    for round_index in range(1, max_rounds + 1):
        if verbose:
            print(f"\n[protein_pre_agent] 开始第 {round_index}/{max_rounds} 轮...", flush=True)

        model_client = create_model_client()
        try:
            agent = create_protein_pre_agent(model_client=model_client)
            protein_result = await run_agent_with_dsml_visualization(
                agent,
                rolling_task,
                bridge_prefix="[protein_pre_agent]",
                verbose=verbose,
                allowed_functions=PROTEIN_ALLOWED_FUNCTIONS,
            )
        finally:
            await model_client.close()

        round_report = [
            f"===== 第 {round_index} 轮 protein_pre_agent 输出 =====",
            protein_result,
        ]

        if not enable_evolution:
            all_round_outputs.append("\n".join(round_report))
            rolling_task = (
                f"{task}\n\n"
                f"【上一轮执行输出（第 {round_index} 轮）】\n{protein_result}"
            )
            continue

        review_task = (
            "请审查以下 protein_pre_agent 的本轮执行结果，并立即开始下一轮自进化。\n"
            "你已被明确授权：可修改项目文件。\n"
            "优先修改下一轮可立即生效的文件，例如：Prompts/protein_pre_agent_prompt.txt、Prompts/tool_registry/*.json、scripts/*.py。\n"
            "可以修改 Agents/protein_pre_agent.py，但这通常需要本次任务结束后重新启动进程才会完全生效。\n"
            "请优先给出最小可行改动，并说明改动验证方法。\n\n"
            "请在输出末尾单独增加一行机器可读标记：EVOLUTION_CONVERGED: YES 或 EVOLUTION_CONVERGED: NO。\n"
            "当你判断当前流程已稳定、无需继续迭代时输出 YES，否则输出 NO。\n\n"
            f"原始任务:\n{task}\n\n"
            f"当前轮次: 第 {round_index}/{max_rounds} 轮\n\n"
            f"protein_pre_agent 本轮执行输出:\n{protein_result}\n\n"
            f"上一轮 evolution 结果（如有）:\n{last_evolution_result or '无'}"
        )

        before_snapshot = _snapshot_mtime(evolution_targets)
        try:
            evolution_result = await execute_protein_evolution_task(
                review_task,
                verbose=verbose,
            )
        except Exception as exc:
            evolution_result = f"审查执行失败: {type(exc).__name__}: {exc}"
        after_snapshot = _snapshot_mtime(evolution_targets)
        changed_targets = _changed_files_since(before_snapshot, after_snapshot)

        if verbose:
            print(f"[protein_pre_agent] 第 {round_index} 轮 evolution 完成。", flush=True)

        last_evolution_result = evolution_result
        round_report.extend(
            [
                "",
                f"===== 第 {round_index} 轮 protein_evolution_agent 审查/进化结果 =====",
                evolution_result,
            ]
        )
        if changed_targets:
            round_report.extend(
                [
                    "",
                    f"===== 第 {round_index} 轮检测到已修改关键文件 =====",
                    "\n".join(changed_targets),
                ]
            )
        all_round_outputs.append("\n".join(round_report))

        if _is_evolution_converged(evolution_result):
            all_round_outputs.append(
                f"===== 第 {round_index} 轮触发提前停止：protein_evolution_agent 判定已收敛 ====="
            )
            break

        if round_index < max_rounds:
            changed_files_text = "\n".join(changed_targets) if changed_targets else "无"
            rolling_task = (
                f"{task}\n\n"
                f"【第 {round_index} 轮 protein_pre_agent 输出】\n{protein_result}\n\n"
                f"【第 {round_index} 轮 protein_evolution_agent 审查/进化建议】\n{evolution_result}\n\n"
                f"【第 {round_index} 轮已修改关键文件】\n{changed_files_text}\n\n"
                "请在下一轮执行中吸收上述演化建议。"
            )

    return "\n\n".join(all_round_outputs)


async def execute_protein_pre_simple(task: str, verbose: bool = True) -> str:
    """Run a single-turn protein_pre_agent task with DSML bridging and return clean text."""
    model_client = create_model_client()
    try:
        agent = create_protein_pre_agent(model_client=model_client)
        return await run_agent_with_dsml_visualization(
            agent,
            task,
            bridge_prefix="[protein_pre_agent]",
            verbose=verbose,
            allowed_functions=PROTEIN_ALLOWED_FUNCTIONS,
        )
    finally:
        await model_client.close()


async def _demo() -> None:
    result = await execute_protein_pre_simple(
        """## 蛋白质处理

##### 蛋白质处理

### 1. 所需文件
- **原始PDB文件**: `1AKE.pdb` （从RCSB PDB下载）
- **清洗后PDB文件**: `1AKE_clean.pdb` （经pdb4amber标准化、加氢、删除非蛋白分子，优先调用run_pdb4amber工具）
- **蛋白质PDBQT文件** (用于对接): `1AKE.pdbqt` （由MGLTools或Open Babel生成）
- **prmtop/inpcrd** (可选): 优先使用run_tleap工具生成

### 2. 文件获取/生成方式
- **原始PDB下载，清洗与加氢**: prepare_pure_protein工具可以全部实现，若失败则尝试自己写脚本生成。
- **蛋白质PDBQT**: 
  - 优先使用MGLTools的 `prepare_receptor4.py` 通过 `run_prepare_receptor4_py(input_pdb, output_pdbqt)` 生成。
  - 默认MGLTools可用，需要调用时通过run_prepare_receptor4_py工具调用，不要再尝试检查环境中是否有MGLTools，若run_prepare_receptor4_py失败则放 弃MGLTools。
  - 备选：Open Babel (`obabel -ipdb protein.pdb -opdbqt -O protein.pdbqt -xr`)。

### 3. 非标准残基处理
- prepare_pure_protein可以删除非标准残基。
- **最终结果**: 输出的蛋白文件应仅包含标准氨基酸残基（及允许的变体），无配体、水、离子。需要审查是否符合条件。""".strip()
    )
    print("\n===== protein_pre_agent 输出 =====")
    print(result)


if __name__ == "__main__":
    asyncio.run(_demo())
