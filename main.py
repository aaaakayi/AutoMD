import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.messages import ToolCallExecutionEvent, ToolCallRequestEvent, ToolCallSummaryMessage
from autogen_agentchat.teams import SelectorGroupChat
from autogen_core.tools import FunctionTool

from Agents.common import create_model_client, load_system_message
from Agents.agent_interfaces import execute_agent_task
from Agents.dock_agent import create_dock_agent
from Agents.env_setup_agent import create_env_setup_agent
from Agents.ligand_pre_agent import create_ligand_pre_agent
from Agents.memory_agent import create_memory_agent
from Agents.protein_pre_agent import create_protein_pre_agent
from tools.search import web_search


PROJECT_ROOT = Path(__file__).resolve().parent
PROGRESS_REPORT_PATH = PROJECT_ROOT / "output" / "progress_report.md"
ORGANIZER_PROMPT_PATH = PROJECT_ROOT / "Prompts" / "Organizer.txt"


def parse_args() -> argparse.Namespace:
    """解析命令行参数，若无则使用默认测试任务。"""
    parser = argparse.ArgumentParser(description="AutoMD orchestrator")
    parser.add_argument(
        "task",
        nargs="*",
        help="原始工作描述，不传则使用默认任务。",
    )
    return parser.parse_args()


class GroupChatOrchestrator:
    """使用 GroupChat 协调 MD 预处理与对接流程。"""

    def __init__(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
        user_input_callback: Optional[Callable[[str], str]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None,
        user_instruction: str = "",
    ):
        self.log_callback = log_callback
        self.user_input_callback = user_input_callback
        self.should_stop_callback = should_stop_callback
        self.model_client = create_model_client()
        self.progress_report_path = PROGRESS_REPORT_PATH

        self.env_agent = create_env_setup_agent(model_client=self.model_client)
        self.protein_agent = create_protein_pre_agent(model_client=self.model_client)
        self.ligand_agent = create_ligand_pre_agent(model_client=self.model_client)
        self.dock_agent = create_dock_agent(model_client=self.model_client)
        self.memory_agent = create_memory_agent(model_client=self.model_client)
        self.user_instruction = user_instruction.strip() or "无额外偏好，请优先保证可复现与稳健性。"

        def _user_input_provider(prompt: str) -> str:
            if self.should_stop_callback and self.should_stop_callback():
                return "用户请求停止，请 coordinator 立即结束流程。"

            if self.user_input_callback:
                try:
                    user_text = (self.user_input_callback(prompt) or "").strip()
                except Exception:
                    user_text = ""
                if user_text:
                    return user_text

            return self.user_instruction

        self.user_agent = UserProxyAgent(
            name="user",
            description="我是用户输入中继代理，不调用LLM，只转发真实用户输入给 coordinator。",
            input_func=_user_input_provider,
        )

        self.coordinator = AssistantAgent(
            name="Coordinator",
            model_client=self.model_client,
            tools=[FunctionTool(web_search, description="搜索网络信息，返回摘要")],
            system_message=(
                "你是一个分子动力学模拟的项目经理。"
                "你的职责是理解用户需求，并将任务拆解成清晰的、可执行的步骤列表。"
                "当需要执行具体操作时，请明确指出应该由哪个专家 Agent（env_setup_agent、protein_pre_agent、ligand_pre_agent、dock_agent）来处理。"
                "请不要直接执行操作，而是进行任务分配和协调。"
                "你必须要求每个专家 Agent 在最终回复中包含《系统性工作总结》小节。"
                "你在收尾时必须汇总四个专家 Agent 的系统性工作总结，并给出总评。"
                "当你给其它agent分配任务时，请你参考之前agent的工作总结，不要自己编造数据分配给agent。"
                "在每次关键分配前，优先让 memory_agent 更新并维护工作进度文档，再据此继续决策。"
                "在每个专家 agent 关键输出后，必须先向 user 请求下一步偏好，再继续分配下一步。"
                "\n\n"
                + load_system_message(ORGANIZER_PROMPT_PATH).rstrip()
            ),
            description="我是项目经理，负责在接到新任务时，规划出详细的执行步骤并协调各个专家 Agent。",
        )

        self.termination_condition = MaxMessageTermination(max_messages=50)

        self.group_chat = SelectorGroupChat(
            participants=[self.coordinator, self.user_agent, self.memory_agent, self.env_agent, self.protein_agent, self.ligand_agent, self.dock_agent],
            model_client=self.model_client,
            termination_condition=self.termination_condition,
            allow_repeated_speaker=False,
        )

    def _emit(self, text: str) -> None:
        print(text)
        if self.log_callback:
            try:
                self.log_callback(text)
            except Exception:
                pass

    @staticmethod
    def _shorten_text(text: str, limit: int = 240) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1] + "…"

    @staticmethod
    def _is_path_like(text: str) -> bool:
        if not isinstance(text, str):
            return False
        t = text.strip()
        if not t:
            return False
        if t.startswith("/") or t.startswith("./") or t.startswith("../"):
            return True
        if "\\" in t or "/" in t:
            return True
        if len(t) >= 3 and t[1:3] == ":\\":
            return True
        return False

    @staticmethod
    def _iter_path_candidates(value: Any, key_hint: str = "") -> Iterable[str]:
        file_keys = {
            "path",
            "file",
            "file_path",
            "filepath",
            "input_file",
            "output_file",
            "protein_file",
            "ligand_file",
            "protein_pdb",
            "output_dir",
            "work_dir",
            "directory",
            "dir",
        }

        if isinstance(value, dict):
            for k, v in value.items():
                k_lower = str(k).lower()
                if isinstance(v, str) and (k_lower in file_keys or GroupChatOrchestrator._is_path_like(v)):
                    yield v
                else:
                    yield from GroupChatOrchestrator._iter_path_candidates(v, k_lower)
            return

        if isinstance(value, list):
            for item in value:
                yield from GroupChatOrchestrator._iter_path_candidates(item, key_hint)
            return

        if isinstance(value, str) and (key_hint in file_keys or GroupChatOrchestrator._is_path_like(value)):
            yield value

    def _extract_paths_from_arguments(self, arguments: str) -> list[str]:
        if not isinstance(arguments, str) or not arguments.strip():
            return []
        try:
            parsed = json.loads(arguments)
        except Exception:
            return []

        seen = set()
        paths: list[str] = []
        for p in self._iter_path_candidates(parsed):
            pp = str(p).strip()
            if not pp or pp in seen:
                continue
            seen.add(pp)
            paths.append(pp)
        return paths

    def _render_tool_call_request(self, source: str, event: ToolCallRequestEvent) -> None:
        for call in event.content:
            tool_name = getattr(call, "name", "unknown_tool")
            args = getattr(call, "arguments", "")
            self._emit(f"\n[{source}] 调用工具: {tool_name}")
            if isinstance(args, str) and args.strip():
                self._emit(f"参数: {self._shorten_text(args, 300)}")
            paths = self._extract_paths_from_arguments(args)
            if paths:
                self._emit("访问文件:")
                for p in paths[:8]:
                    self._emit(f"- {p}")

    def _render_tool_call_execution(self, source: str, event: ToolCallExecutionEvent) -> None:
        for result in event.content:
            tool_name = getattr(result, "name", "unknown_tool")
            status = "失败" if getattr(result, "is_error", False) else "成功"
            content = getattr(result, "content", "")
            self._emit(f"\n[{source}] 工具返回: {tool_name} ({status})")
            if isinstance(content, str) and content.strip():
                self._emit(f"结果: {self._shorten_text(content, 800)}")

    def _render_tool_call_summary(self, source: str, event: ToolCallSummaryMessage) -> None:
        calls = getattr(event, "tool_calls", [])
        results = getattr(event, "results", [])
        self._emit(f"\n[{source}] 工具调用汇总: {len(calls)} 次")
        for idx, call in enumerate(calls):
            tool_name = getattr(call, "name", "unknown_tool")
            status = "成功"
            if idx < len(results) and getattr(results[idx], "is_error", False):
                status = "失败"
            self._emit(f"- {tool_name}: {status}")

    def _build_final_report(self, raw_task: str, transcript: list[tuple[str, str]]) -> str:
        lines = [
            "===== 最终执行报告 =====",
            "任务输入：",
            raw_task,
            "",
            f"已记录消息数：{len(transcript)}",
            "",
            "执行摘要：",
        ]

        if not transcript:
            lines.append("- 本次没有收到可显示的 agent 文本消息。")
        else:
            for index, (source, content) in enumerate(transcript, start=1):
                lines.append(f"{index}. {source}: {self._shorten_text(content)}")

        lines.append("")
        lines.append("说明：上面的终端输出保留了完整中间过程，这里只给出系统性摘要。")
        return "\n".join(lines)

    @staticmethod
    def _extract_final_block(text: str) -> str:
        if not isinstance(text, str):
            return ""
        matches = re.findall(r"\[star\][\s\S]*?\[TERMINATE\]", text)
        if matches:
            return matches[-1].strip()
        stripped = text.strip()
        return stripped

    @staticmethod
    def _agent_section_title(agent_name: str) -> str:
        mapping = {
            "env_setup_agent": "环境设置",
            "protein_pre_agent": "蛋白质处理",
            "ligand_pre_agent": "配体处理",
            "dock_agent": "分子对接",
            "memory_agent": "执行摘要",
        }
        return mapping.get(agent_name, agent_name)

    def _build_progress_report(self, raw_task: str, transcript: list[tuple[str, str]]) -> str:
        latest_by_agent: dict[str, str] = {}
        for source, content in transcript:
            if source in {"user", "system", "Coordinator"}:
                continue
            latest_by_agent[source] = content

        order = ["env_setup_agent", "protein_pre_agent", "ligand_pre_agent", "dock_agent", "memory_agent"]
        lines = ["[star]", "## 原始任务", raw_task, ""]

        if not latest_by_agent:
            lines.extend([
                "## 当前进度",
                "- 当前还没有收到可归档的 agent 结果。",
                "",
                "## 说明",
                "- 此文件由 main.py 在运行过程中持续更新，并作为后续 Coordinator 决策上下文。",
                "[TERMINATE]",
            ])
            return "\n".join(lines)

        for agent_name in order:
            content = latest_by_agent.get(agent_name)
            if not content:
                continue
            section_title = self._agent_section_title(agent_name)
            final_block = self._extract_final_block(content)
            lines.append(f"## {section_title}")
            lines.append(final_block)
            lines.append("")

        lines.extend([
            "## 说明",
            "- 此文件由 main.py 在运行过程中持续更新，并作为后续 Coordinator 决策上下文。",
            "[TERMINATE]",
        ])
        return "\n".join(lines)

    def _load_progress_report(self) -> str:
        try:
            if self.progress_report_path.exists():
                content = self.progress_report_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except OSError:
            pass

        return (
            "[star]\n"
            "## 工作进度\n"
            "- 当前还没有可用的历史进度记录。\n"
            "## 执行摘要\n"
            "- 尚未开始。\n"
            "[TERMINATE]"
        )

    def _save_progress_report(self, raw_task: str, transcript: list[tuple[str, str]]) -> None:
        try:
            self.progress_report_path.parent.mkdir(parents=True, exist_ok=True)
            self.progress_report_path.write_text(
                self._build_progress_report(raw_task, transcript),
                encoding="utf-8",
            )
        except OSError:
            pass

    async def run(self, raw_task: str) -> str:
        """启动群聊，实时输出中间过程并返回结构化最终报告。"""

        transcript: list[tuple[str, str]] = []
        self._emit("\n===== AutoMD 执行过程 =====")
        progress_report = self._load_progress_report()

        enhanced_task = (
            raw_task.rstrip()
            + "\n\n统一执行要求："
            + "\n1) 每个专家 Agent 的最终回复必须包含《系统性工作总结》小节。"
            + "\n2) 《系统性工作总结》必须按“任务目标 -> 实际执行 -> 关键结果 -> 问题与处理 -> 下一步建议”输出。"
            + "\n3) Coordinator 在最终收尾时必须汇总 env_setup_agent、protein_pre_agent、ligand_pre_agent、dock_agent 的系统性工作总结。"
            + "\n\n当前维护的工作进度文档如下，请先阅读它再决定下一步工作分配：\n"
            + progress_report
        )

        self._save_progress_report(raw_task, transcript)

        async for item in self.group_chat.run_stream(task=enhanced_task, output_task_messages=False):
            if self.should_stop_callback and self.should_stop_callback():
                self._emit("\n[system] 收到停止请求，正在安全终止当前流程。")
                break

            source = getattr(item, "source", None)
            content = getattr(item, "content", None)

            source_label = source if isinstance(source, str) and source else "system"

            if isinstance(item, ToolCallRequestEvent):
                self._render_tool_call_request(source_label, item)
                continue

            if isinstance(item, ToolCallExecutionEvent):
                self._render_tool_call_execution(source_label, item)
                continue

            if isinstance(item, ToolCallSummaryMessage):
                self._render_tool_call_summary(source_label, item)
                continue

            if isinstance(source, str) and isinstance(content, str):
                transcript.append((source, content))
                self._save_progress_report(raw_task, transcript)
                self._emit(f"\n[{source_label}] 发言:")
                self._emit(content)
            elif hasattr(item, "messages"):
                continue
            else:
                self._emit(f"\n[{source_label}] {item.__class__.__name__}: {self._shorten_text(str(item), 300)}")

        return self._build_final_report(raw_task, transcript)

    async def close(self) -> None:
        """关闭模型客户端。"""
        await self.model_client.close()


async def run_pipeline(
    raw_task: str,
    log_callback: Optional[Callable[[str], None]] = None,
    user_input_callback: Optional[Callable[[str], str]] = None,
    should_stop_callback: Optional[Callable[[], bool]] = None,
    user_instruction: str = "",
) -> str:
    orchestrator = GroupChatOrchestrator(
        log_callback=log_callback,
        user_input_callback=user_input_callback,
        should_stop_callback=should_stop_callback,
        user_instruction=user_instruction,
    )
    try:
        final_report = await orchestrator.run(raw_task)
        return final_report
    finally:
        await orchestrator.close()


async def run_single_agent(agent_name: str, task: str) -> str:
    """供 main.py 或外部模块调用：执行指定 agent 并返回文本结果。"""
    return await execute_agent_task(agent_name, task)


async def main() -> None:
    args = parse_args()
    raw_task = " ".join(args.task).strip() or (
        "对 1IEP 和配体：Cc1ccc(cc1Nc2nccc(n2)c3cccnc3)NC(=O)c4ccc(cc4)CN5CCN(CC5)C 进行MD的文件预处理和对接，生成后续可执行的MD所需文件。"
        "所有输出保存到 ./output，并在output下创建protein_preparation、ligand_preparation、docking_result等子目录。"
        "所有中间文件保存在项目根目录的 temp 文件夹，有用的输出文件必须保存在 output 文件夹。"
        "生成可复现的执行报告（含命令和结果摘要），每个专家都要给出系统性工作总结。"
    )
    final_report = await run_pipeline(raw_task)
    print(final_report)


if __name__ == "__main__":
    asyncio.run(main())