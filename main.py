import argparse
import asyncio
import os
import sys
import openai
from pathlib import Path
from typing import Callable, Optional, Iterable

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
from Agents.md_agent import create_md_agent
from Agents.memory_agent import create_memory_agent
from Agents.postdock_agent import create_postdock_agent
from Agents.protein_pre_agent import create_protein_pre_agent
from orchestration import LongMemoryMaterializer, ProgressReportBuilder, StructuredEventStore, MemoryRetriever
from orchestration.dsml_utils import strip_dsml
from tools.long_memory import recall_user_memory, remember_user_memory
from tools.search import web_search
from tools.search_from_RAG import search_from_rag
from tools.system_tools import read_text_file

try:
    from memory.RAG import ChunkRAGPipeline
except Exception:
    ChunkRAGPipeline = None


PROJECT_ROOT = Path(__file__).resolve().parent
PROGRESS_REPORT_PATH = PROJECT_ROOT / "output" / "progress_report.md"
ORGANIZER_PROMPT_PATH = PROJECT_ROOT / "Prompts" / "Organizer.txt"
EVENT_LOG_DIR = PROJECT_ROOT / "memory" / "long_memory" / "events"
INDEX_PATH = PROJECT_ROOT / "memory" / "long_memory" / "index.json"
USER_PROFILE_PATH = PROJECT_ROOT / "memory" / "long_memory" / "user_profile.json"
RUNS_DIR = PROJECT_ROOT / "memory" / "long_memory" / "runs"
CHUNKS_PATH = PROJECT_ROOT / "memory" / "long_memory" / "chunks" / "chunks.jsonl"
VECTOR_DB_DIR = PROJECT_ROOT / "memory" / "RAG" / "vector_db"
VECTOR_COLLECTION = "automd_chunks"



def parse_args() -> argparse.Namespace:
    """解析命令行参数，若无则使用默认测试任务。"""
    parser = argparse.ArgumentParser(description="AutoMD orchestrator")
    parser.add_argument(
        "task",
        nargs="*",
        help="原始工作描述，不传则使用默认任务。",
    )
    parser.add_argument(
        "--debug-tool-calls",
        action="store_true",
        help="开启后在终端输出工具调用详情（请求参数、返回摘要、调用汇总）。",
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
        debug_tool_calls: bool = False,
        show_system_messages: bool = False,
        show_user_messages: bool = False,
    ):
        self.log_callback = log_callback
        self.user_input_callback = user_input_callback
        self.should_stop_callback = should_stop_callback
        self.model_client = create_model_client()
        self.progress_report_path = PROGRESS_REPORT_PATH # 用于保存和更新执行过程中的进度报告
        self.debug_tool_calls = debug_tool_calls
        self.show_system_messages = show_system_messages
        self.show_user_messages = show_user_messages
        self.event_store = StructuredEventStore(EVENT_LOG_DIR) # 用于记录和管理整个执行过程中的事件数据
        self.report_builder = ProgressReportBuilder() # 用于构建和维护执行过程中的进度报告
        self.long_memory = LongMemoryMaterializer(PROJECT_ROOT) # 用于在流程结束时将事件数据转化为长期记忆索引
        self.memory_retriever = MemoryRetriever(INDEX_PATH, USER_PROFILE_PATH) # 用于在协调过程中检索相关长期记忆
        self.rag_pipeline = None
        self.rag_pipeline_error = None

        if ChunkRAGPipeline is not None:
            try:
                self.rag_pipeline = ChunkRAGPipeline(
                    chunks_path=CHUNKS_PATH,
                    db_dir=VECTOR_DB_DIR,
                    collection_name=VECTOR_COLLECTION,
                    rebuild_index=False,
                    auto_build_if_empty=True,
                )
            except Exception as exc:
                self.rag_pipeline_error = str(exc)
                self.rag_pipeline = None

        self.env_agent = create_env_setup_agent(model_client=self.model_client)
        self.protein_agent = create_protein_pre_agent(model_client=self.model_client)
        self.ligand_agent = create_ligand_pre_agent(model_client=self.model_client)
        self.dock_agent = create_dock_agent(model_client=self.model_client)
        self.postdock_agent = create_postdock_agent(model_client=self.model_client)
        self.md_agent = create_md_agent(model_client=self.model_client)
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

        def _search_from_rag_tool(query: str, *, top_k: int = 5, chunk_types: Optional[Iterable[str]] = None) -> str:
            # Wrapper used by FunctionTool so signature inspection succeeds.
            try:
                if self.rag_pipeline is not None:
                    results = self.rag_pipeline.retrieve(
                        query,
                        top_k=max(1, int(top_k)),
                        chunk_types=set(chunk_types) if chunk_types is not None else None,
                    )
                    return self.rag_pipeline.context_builder.format_results(results)
                # Fallback to module-level function which will instantiate its own pipeline
                return search_from_rag(query, top_k=top_k, chunk_types=chunk_types)
            except Exception as exc:  # pragma: no cover - defensive
                return f"RAG 检索失败: {exc}"

        self.coordinator = AssistantAgent(
            name="Coordinator",
            model_client=self.model_client,
            tools=[
                FunctionTool(web_search, description="搜索网络信息，返回摘要"),
                FunctionTool(recall_user_memory, description="读取已存储的用户长期记忆"),
                FunctionTool(remember_user_memory, description="将用户偏好/身份/长期有效信息写入长期记忆"),
                FunctionTool(_search_from_rag_tool, description="根据用户查询从本地 RAG 向量库检索相关上下文信息"),
                FunctionTool(read_text_file, description="读取文本文件内容,你只能检索你需要的内容")
            ],
            system_message=load_system_message(ORGANIZER_PROMPT_PATH).rstrip(),
            description="我是项目经理，负责在接到新任务时，规划出详细的执行步骤并协调各个专家 Agent。",
        )

        self.termination_condition = MaxMessageTermination(max_messages=50)

        self.group_chat = SelectorGroupChat(
            participants=[self.coordinator, self.user_agent, self.memory_agent, self.env_agent, self.protein_agent, self.ligand_agent, self.dock_agent, self.postdock_agent, self.md_agent],
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

    def _emit_rag_context(self, title: str, content: str, limit: int = 1800) -> None:
        text = self.report_builder.shorten_text(content or "", limit)
        self._emit(f"\n[RAG] {title}")
        if text:
            self._emit(text)
        else:
            self._emit("暂无可展示的检索内容。")

    async def run(self, raw_task: str) -> str:
        """启动群聊，实时输出中间过程并返回结构化最终报告。"""

        transcript: list[tuple[str, str]] = []
        if self.show_system_messages:
            self._emit("\n===== AutoMD 执行过程 =====")
        progress_report = self.report_builder.load_progress_report(self.progress_report_path)
        self.event_store.record_run_started(raw_task, self.debug_tool_calls)

        if self.rag_pipeline is not None:
            rag_results = self.rag_pipeline.retrieve(
                raw_task,
                top_k=5,
                chunk_types={"task_flow", "outcome_summary", "tool_param"},
            )
            recent_memory_summary = self.rag_pipeline.context_builder.format_results(rag_results)
            self._emit_rag_context(
                f"预检索完成（db={VECTOR_DB_DIR}, top_k=5）",
                recent_memory_summary,
            )
        else:
            if self.rag_pipeline_error:
                if self.show_system_messages:
                    self._emit(f"[memory] 向量库初始化失败，原因: {self.rag_pipeline_error}")
            retrieval = self.memory_retriever.retrieve(query=raw_task, top_k=3)
            recent_memory_summary = self.memory_retriever.format_for_prompt(retrieval)
            if self.show_system_messages:
                self._emit("[memory] 向量库未启用，已回退到关键词记忆检索。")

        enhanced_task = (
            raw_task.rstrip()
            + "\n\n当前维护的工作进度文档如下，请先阅读它再决定下一步工作分配：\n"
            + progress_report
            + "\n\n相关需要的长期记忆检索结果如下，请参考它来决定是否需要调整执行计划：\n"
            + recent_memory_summary
        )

        self.report_builder.save_progress_report(self.progress_report_path, raw_task, transcript)

        async for item in self.group_chat.run_stream(task=enhanced_task, output_task_messages=False):
            if self.should_stop_callback and self.should_stop_callback():
                if self.show_system_messages:
                    self._emit("\n[system] 收到停止请求，正在安全终止当前流程。")
                break

            source = getattr(item, "source", None)
            content = getattr(item, "content", None)

            source_label = source if isinstance(source, str) and source else "system"

            if isinstance(item, ToolCallRequestEvent):
                for line in self.event_store.record_tool_call_request(source_label, item):
                    if self.debug_tool_calls:
                        self._emit(line)
                continue

            if isinstance(item, ToolCallExecutionEvent):
                for line in self.event_store.record_tool_call_execution(source_label, item):
                    if self.debug_tool_calls:
                        self._emit(line)

                for result in getattr(item, "content", []):
                    tool_name = getattr(result, "name", "")
                    if tool_name == "search_from_rag":
                        status = "失败" if getattr(result, "is_error", False) else "成功"
                        rag_text = getattr(result, "content", "")
                        self._emit_rag_context(f"search_from_rag 返回（{status}）", str(rag_text))
                continue

            if isinstance(item, ToolCallSummaryMessage):
                for line in self.event_store.record_tool_call_summary(source_label, item):
                    if self.debug_tool_calls:
                        self._emit(line)
                continue

            if isinstance(source, str) and isinstance(content, str):
                clean_content = strip_dsml(content)
                transcript.append((source, clean_content))
                if source == "Coordinator":
                    self.event_store.record_coordinator_assignment(clean_content, raw_task)
                elif source in {"env_setup_agent", "protein_pre_agent", "ligand_pre_agent", "dock_agent", "memory_agent"}:
                    self.event_store.record_agent_message(source, clean_content, raw_task)
                self.report_builder.save_progress_report(self.progress_report_path, raw_task, transcript)
                if source != "user" or self.show_user_messages:
                    self._emit(f"\n[{source_label}] 发言:")
                    self._emit(clean_content)
            elif hasattr(item, "messages"):
                continue
            else:
                if self.show_system_messages:
                    raw_str = str(item)
                    clean_str = strip_dsml(raw_str)
                    self._emit(f"\n[{source_label}] {item.__class__.__name__}: {self.report_builder.shorten_text(clean_str, 300)}")

        self.event_store.record_run_finished(len(transcript))
        self.long_memory.materialize_and_index(
            event_store=self.event_store,
            raw_task=raw_task,
            transcript=transcript,
        )

        return self.report_builder.build_final_report(raw_task, transcript)

    async def close(self) -> None:
        """关闭模型客户端。"""
        await self.model_client.close()


async def run_pipeline(
    raw_task: str,
    log_callback: Optional[Callable[[str], None]] = None,
    user_input_callback: Optional[Callable[[str], str]] = None,
    should_stop_callback: Optional[Callable[[], bool]] = None,
    user_instruction: str = "",
    debug_tool_calls: bool = False,
    show_system_messages: bool = False,
    show_user_messages: bool = False,
) -> str:
    # Quick preflight: warn if OPENAI API key missing (common misconfiguration).
    if os.getenv("OPENAI_API_KEY") is None:
        print("[warning] OPENAI_API_KEY is not set in environment. OpenAI model calls will fail unless another model client is configured.")

    orchestrator = GroupChatOrchestrator(
        log_callback=log_callback,
        user_input_callback=user_input_callback,
        should_stop_callback=should_stop_callback,
        user_instruction=user_instruction,
        debug_tool_calls=debug_tool_calls,
        show_system_messages=show_system_messages,
        show_user_messages=show_user_messages,
    )
    try:
        try:
            final_report = await orchestrator.run(raw_task)
            return final_report
        except Exception as exc:
            # Provide a clearer error for OpenAI authentication failures
            try:
                from openai.error import AuthenticationError
                if isinstance(exc, AuthenticationError):
                    print("[error] OpenAI authentication failed. Check your OPENAI_API_KEY environment variable.")
                    print("[hint] Set it in PowerShell: $Env:OPENAI_API_KEY=\"sk-...\"  (or export in WSL: export OPENAI_API_KEY=\"sk-...\")")
                    raise RuntimeError("OpenAI API authentication failed. See printed hint for how to set OPENAI_API_KEY.") from exc
            except Exception:
                pass
            raise
    finally:
        await orchestrator.close()


async def run_single_agent(agent_name: str, task: str) -> str:
    """供 main.py 或外部模块调用：执行指定 agent 并返回文本结果。"""
    return await execute_agent_task(agent_name, task)


async def main() -> None:
    args = parse_args()
    raw_task = " ".join(args.task).strip() or (
        "请输入你的问题。"
    )
    await run_pipeline(
        raw_task,
        debug_tool_calls=args.debug_tool_calls,
        show_system_messages=args.debug_tool_calls,
        show_user_messages=False,
    )


if __name__ == "__main__":
    asyncio.run(main())