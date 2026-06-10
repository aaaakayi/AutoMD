#!/usr/bin/env python3
"""
AutoMD Chat — conversational LLM frontend for the AutoMD workflow.

Usage:
    python chat.py                      # session = "main"
    python chat.py --session kinase     # session = "kinase"

The chat LLM helps the user formulate a raw_task, launches the workflow on
confirmation, and can inspect output files after completion.
"""

from __future__ import annotations

import os
import sys
import argparse
import re
import ast
import uuid
from pathlib import Path

# Ensure the LangGraph package is importable
project_dir = os.path.dirname(os.path.abspath(__file__))
langgraph_dir = os.path.join(project_dir, "AutoMD_LangGraph")
if langgraph_dir not in sys.path:
    sys.path.insert(0, langgraph_dir)
# Ensure the prompt/ package is importable (chat prompts live in prompt/chat.md)
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

from prompt import load as _load_prompt  # chat LLM prompts live in prompt/chat.md

try:
    from nodes.visual_docking import _start_pymol, _try_connect, _ensure_pymol_rpc, _wait_for_rpc, _to_win_path
except Exception:
    # Provide lightweight stubs for environments where nodes/visual_docking or its
    # dependencies are unavailable. These stubs allow `--test-embed-pymol` to run
    # without importing the full PyMOL RPC stack.
    def _ensure_pymol_rpc():
        class _StubServer:
            def do(self, cmd):
                return None
            def get_session_info(self):
                return {"objects": [], "names": [], "view": []}
            def get_object_list(self):
                return []
        return _StubServer()

    def _start_pymol(*args, **kwargs):
        raise FileNotFoundError("PyMOL stub: not available")

    def _try_connect(*args, **kwargs):
        raise FileNotFoundError("PyMOL stub: not available")

    def _wait_for_rpc(*args, **kwargs):
        raise FileNotFoundError("PyMOL stub: not available")

    def _to_win_path(p):
        return p
from tools.system_tools import run_shell_command as _run_shell_command

# LLM\retrieval_llm.py 的导入与 stub — 让 chat.py 在 retrieval_llm 不可用时也能跑
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from LLM.retrieval_llm import query_pymol_knowledge as _query_pymol_knowledge_impl
except Exception:
    def _query_pymol_knowledge_impl(query: str) -> str:
        return f"[PyMOL 知识库不可用] 模块 LLM.retrieval_llm 加载失败, 请检查 LLM\\retrieval_llm.py。query={query}"

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return None
load_dotenv()

import json

from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from orchestrate.logging.context_logger import log_llm_context
from orchestrate.stream.dsml_filter import DSMLStreamFilter
from orchestrate.summary.tool_summary_formatter import format_tool_summary
from orchestrate.text.sanitizers import (
    sanitize_summary_output,
    sanitize_terminal_text,
    strip_dsml_text,
)

MAX_ROUNDS = 15  # 最大工具调用轮数，避免无限循环


def _build_prompt(session_id: str) -> str:
    """Load tool_llm system prompt from prompt/chat.md, inject session-specific paths."""
    abs_output = (Path(langgraph_dir) / "output" / session_id).resolve()
    output_path = str(abs_output)
    # Note: prompt has {session_id} {output_path} {task_id} placeholders; we
    # fill session_id and output_path here. {task_id} is left as-is for the
    # LLM to read as documentation, since the chat LLM doesn't actually need
    # to know the task_id (workflow generates it).
    return _load_prompt("chat", "TOOL_LLM_SYSTEM").format(
        session_id=session_id,
        output_path=output_path,
    )


load_dotenv()

def _build_llm(temperature = 0.2, disable_thinking: bool = False):
    api_key = os.getenv("LLM_API_KEY")
    model_name = 'deepseek-v4-flash'
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    
    if disable_thinking:
        # 直接将 extra_body 作为顶层参数传递
        return ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            max_tokens=4096,
            api_key=api_key,
            base_url=base_url,
            extra_body={"thinking": {"type": "disabled"}},
        )
    else:
        return ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            max_tokens=4096,
            api_key=api_key,
            base_url=base_url,
        )

def embed_pymol(CLI_LIST):
    # ── 1. Start PyMOL (connects to existing on interrupt re-execution) ──
    try:
        server = _ensure_pymol_rpc()
    except (FileNotFoundError, TimeoutError) as e:
        raise RuntimeError(f"PyMOL 不可用: {e},请设置正确的路径并确保 PyMOL 已安装") from e
    if not CLI_LIST:
        return "没有提供任何命令。"

    # 🔧 输出形态: 逐条 CLI 一行, 让工具调用 LLM 看到具体哪条成功/失败/为什么失败
    # 这一份"富输出"会作为 ToolMessage 进入 self.messages 上下文
    # 总结 LLM 看到的是 _clean_tool_result 清洗后的"贫瘠摘要"(另一个分支)
    lines: list[str] = []
    for idx, cmd in enumerate(CLI_LIST, start=1):
        try:
            resp = server.do(cmd)
            if resp is None:
                lines.append(f"{idx}. {cmd} → OK (无返回)")
            elif isinstance(resp, str) and resp.lower().startswith("error"):
                lines.append(f"{idx}. {cmd} → ERROR: {resp[:500]}")
            else:
                lines.append(f"{idx}. {cmd} → OK: {str(resp)[:300]}")
        except Exception as e:
            lines.append(f"{idx}. {cmd} → EXCEPTION: {str(e)[:500]}")

    return "\n".join(lines)


@tool
def chat_reply() -> str:
    """向用户回复。当本轮不需要执行操作、只需用自然语言回复用户时调用。仅调此一个工具。"""
    return "ok"


@tool
def permit_shell() -> str:
    """用户明确同意执行shell命令后调用。单次有效，仅授权下一轮一个run_shell_command。"""
    return "ok"


@tool
def run_workflow(raw_task: str) -> str:
    """启动 AutoMD 全自动流程（对接→MD→分析→报告）。
    何时用: 用户已明确确认参数、说"执行"或"开始"。
    何时不用: 参数未确认、用户还在讨论阶段。
    参数 raw_task: 完整任务描述，含 PDB ID、配体名称/SMILES、对接模式、MD 配置等。
    调用后独占本轮，不与其它工具并发。"""
    _ = raw_task
    return "已请求执行工作流，请在下方工作流面板查看进度。"


def _resolve_path(path: str, session_id: str | None = None) -> Path:
    """Resolve a read path under the current session output."""
    p = Path(path)
    if session_id:
        return Path(langgraph_dir) / "output" / session_id / p
    return Path(langgraph_dir) / "output" / p


@tool
def read_output_file(path: str) -> str:
    """读取文件内容。只允许读取当前会话产物。
    何时用: 需要查看当前会话产物文件内容。
    何时不用: 浏览文件列表用list_outputs；不可读取手册、其他会话文件或系统文件。"""
    full = _resolve_path(path)
    if not full.exists():
        return f"文件不存在: {full}"
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
        return content[:4000] if len(content) > 4000 else content
    except Exception as e:
        return f"读取失败: {e}"


@tool
def list_outputs() -> str:
    """列出 output/ 下所有产物文件。无参数。
    何时用: 用户问"有什么文件"、需要浏览产物目录结构。
    何时不用: 读具体文件内容用 read_output_file。但是你得先确认文件确实存在，不然先用list_outputs确认文件路径"""
    output_root = Path(langgraph_dir) / "output"
    files = []
    for root, _dirs, filenames in os.walk(str(output_root)):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(root, fn), str(output_root))
            files.append(rel)
    files.sort()
    return "\n".join(files[:80]) if files else "(output/ 目录为空)"


@tool
def pymol_start() -> str:
    """启动 PyMOL 及其 XML-RPC 服务。已运行则返回状态。无参数。
    何时用: 用户要求可视化、查看结构、加载分子到 PyMOL 时首先调用。
    何时不用: 仅查询状态用 pymol_status，关闭 PyMOL 用 pymol_quit。"""
    server = _try_connect()
    if server is not None:
        try:
            objs = server.get_object_list()
            return f"PyMOL 已在运行。当前加载的对象: {objs if objs else '(空)'}"
        except Exception:
            return "PyMOL 已在运行（无法查询详情）。"
    try:
        server = _ensure_pymol_rpc()
        objs = server.get_object_list()
        return f"PyMOL 已启动并就绪。当前加载的对象: {objs if objs else '(空)'}"
    except Exception as e:
        return f"PyMOL 启动失败: {e}"


@tool
def pymol_quit() -> str:
    """关闭 PyMOL 窗口。无参数。
    何时用: 用户要求关闭 PyMOL、结束可视化会话。"""
    server = _try_connect()
    if server is None:
        return "PyMOL 未在运行，无需关闭。"
    try:
        server.quit_pymol()
        return "PyMOL 已关闭。"
    except Exception:
        return "PyMOL 已关闭（连接断开）。"


@tool
def pymol_status() -> str:
    """查询 PyMOL 运行状态及已加载对象列表。无参数。
    何时用: 用户询问 PyMOL 状态、想知道加载了哪些分子。
    何时不用: 启动 PyMOL 用 pymol_start，执行 PyMOL 命令用 pymol_execute。"""
    server = _try_connect()
    if server is None:
        return "PyMOL 未运行。使用 pymol_start 启动。"
    try:
        info = server.get_session_info()
        objs = info.get("objects", [])
        names = info.get("names", [])
        lines = ["PyMOL 运行中。"]
        lines.append(f"已加载对象: {objs if objs else '(空)'}")
        if names:
            lines.append(f"命名选择: {names}")
        return "\n".join(lines)
    except Exception as e:
        return f"查询 PyMOL 状态失败: {e}"


@tool
def pymol_execute(cli_list: list[str]) -> str:
    """执行 PyMOL CLI 命令列表。
    参数 cli_list: PyMOL 命令字符串列表，如 ["load protein.pdb", "color red, protein"]。
    何时用: 加载分子文件、修改显示样式、截图、改变视角等 PyMOL 操作。
    何时不用: 启动 PyMOL 用 pymol_start，查询状态用 pymol_status。
    使用前确保已调用 pymol_start。"""
    return embed_pymol(cli_list)


@tool
def query_pymol_knowledge(query: str) -> str:
    """根据自然语言问题从 PyMOL 知识库检索可执行的 PyMOL CLI 答案。

    参数 query: 自然语言问题, 例如 "怎么找配体周围 5Å 的所有蛋白残基?" 或 "PyMOL 报路径错怎么办?"

    何时用: 调 pymol_execute 之前, 想确认某条 PyMOL CLI 的正确写法、避免凭印象写错时。
          返回值会作为你的下一步输入, 据此再生成 pymol_execute 的 cli_list。
    何时不用: 已经有明确 CLI 不需要查; 查产物文件用 read_output_file;
          查 PyMOL 运行状态用 pymol_status; 启动 PyMOL 用 pymol_start。

    注意: 检索需要 5-15 秒, 一次问一个问题, 不要并发调多次。

    ⚠️ 重要: 返回的 PyMOL CLI 示例是 **模板**, 里面的路径都是 **占位符** (如 `D:\\path\\protein.pdbqt`、
       `D:\\path\\ligand.pdbqt`、`D:\\path\\to\\output.png`)。
       **绝不能原样照抄给 pymol_execute** —— 那样会 "Could not open file" 报错, 还得返工。

       你必须替换占位符才能用:
       1. **加载路径**: 用当前会话的真实文件路径替换。先用 list_outputs / read_output_file
          拿到 `output/<session_id>/` 下的实际产物, 例
          `D:\\AutoMD\\AutoMD_LangGraph\\output\\<session_id>\\protein\\<task_id>\\receptor\\<name>.pdbqt`
          或 `...\\docking\\<task_id>\\vina\\docked.pdbqt`。
       2. **输出路径**: 截图类必须写到 `D:\\AutoMD\\AutoMD_LangGraph\\output\\<session_id>\\pymol\\*.png`,
          **绝不写到**知识库示例里的 `D:\\path\\to\\` 这种虚拟目录。
       3. **对象名保留**: 模板里的 `receptor` / `ligand` / `binding_site` 是 AutoMD 项目约定, 直接保留, 别改。
    """
    return _query_pymol_knowledge_impl(query)


@tool
def run_shell_command(
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 20000,
    env: dict[str, str] | None = None,
) -> str:
    """在项目目录内执行shell命令。需要用户明确许可!
    调用前提: 先用chat_reply向用户说明命令并获同意，再调permit_shell授权，最后调本工具。
    参数: command(必填), cwd(可选), timeout_seconds(默认120), env(可选)。
    何时用: 用户明确要求运行脚本、编译软件、安装依赖时。单次有效，每次需重新授权。
    何时不用: 查看文件用read_output_file，浏览产物用list_outputs，PyMOL用pymol_*系列。
             不要用此工具探索文件系统或查看目录。"""
    result = _run_shell_command(
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
        env=env,
    )
    return result.format_for_agent()


TOOLS = [chat_reply, permit_shell, run_workflow, read_output_file, list_outputs, pymol_start, pymol_quit, pymol_status, pymol_execute, query_pymol_knowledge, run_shell_command]

# ── LLM prompts ──────────────────────────────────────────────────────────
# All chat.py LLM prompt constants are loaded from prompt/chat.md.
# Edit there to change LLM behavior. See prompt/README.md.
SUMMARY_SYSTEM = _load_prompt("chat", "SUMMARY_SYSTEM")

# Summary requirements (HTML format spec, self-check, style). Carries {session_id}
# placeholder; format() at use site with current session.
_SUMMARY_REQUIREMENTS = _load_prompt("chat", "SUMMARY_REQUIREMENTS")

COMPRESSOR_SYSTEM = _load_prompt("chat", "COMPRESSOR_SYSTEM")


def _shorten_summary_text(text: str, max_chars: int = 220) -> str:
    content = re.sub(r"\s+", " ", str(text or "")).strip()
    if not content:
        return ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "..."


def _format_summary_tool_args(args: dict) -> str:
    if not isinstance(args, dict):
        return "{}"

    normalized: dict = {}
    for key, value in args.items():
        if isinstance(value, str):
            normalized[key] = _shorten_summary_text(value, 120)
        elif isinstance(value, list):
            items = [_shorten_summary_text(item, 60) for item in value[:6]]
            if len(value) > 6:
                items.append(f"...(+{len(value) - 6})")
            normalized[key] = items
        else:
            normalized[key] = value

    try:
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(normalized)


def _build_summary_system_block() -> str:
    """Static 'constitutional' rules for the summary LLM: persona, tool semantics,
    HTML format spec, self-check, style. Injected as SystemMessage.
    """
    return SUMMARY_SYSTEM + "\n\n" + _SUMMARY_REQUIREMENTS


def _build_summary_human_block(
    current_request: str,
    recent_history_lines: list[str],
    tool_order_lines: list[str],
    tool_result_lines: list[str],
    history_summary: str = "",
    session_id: str = "",
) -> str:
    """Variable data for the summary LLM: session_id, history, current request,
    tool trace. Injected as HumanMessage.
    """
    sections = []

    # 🔧 显式注入 session_id, 让 LLM 知道当前会话的 ID, 避免它"怕编错 URL"而拒绝展示图片
    if session_id:
        sections.append(f"## 当前会话 ID\n`{session_id}`\n\n所有 `<img src=\"/api/output/...\">` 中的 `...` 部分必须以这个 session_id 起头, 例如 `/api/output/{session_id}/md/1A2B/plots/rmsd.png`。这个 HTTP 端点**确实存在**, 前端**能直接访问**, 直接用就行, 不要再犹豫或问用户。")

    if history_summary:
        sections.append(f"## 历史摘要\n{history_summary}")

    if recent_history_lines:
        sections.append("## 最近会话\n" + "\n".join(recent_history_lines))

    if current_request:
        sections.append(f"## 当前用户需求\n{current_request}")

    sections.append(
        "## 工具调用顺序\n"
        + ("\n".join(tool_order_lines) if tool_order_lines else "无")
    )
    sections.append(
        "## 工具调用结果\n"
        + ("\n".join(tool_result_lines) if tool_result_lines else "无")
    )

    return "\n\n".join(sections)


def _collect_recent_history_lines(messages: list, limit: int = 10) -> list[str]:
    if not messages:
        return []

    # Avoid duplicating the current round's last HumanMessage in the session history block.
    history_messages = list(messages)
    if history_messages and isinstance(history_messages[-1], HumanMessage):
        history_messages = history_messages[:-1]

    lines: list[str] = []
    for message in history_messages[-limit:]:
        if isinstance(message, HumanMessage):
            text = _shorten_summary_text(message.content, 260)
            if text:
                lines.append(f"用户: {text}")
        elif isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            text = _shorten_summary_text(message.content, 260)
            if text:
                lines.append(f"助手: {text}")
    return lines


def _collect_current_tool_trace(messages: list) -> tuple[list[str], list[str]]:
    tool_order_entries: list[tuple[int, str]] = []
    tool_result_entries: list[tuple[int, str]] = []
    call_lookup: dict[str, tuple[int, str, dict]] = {}
    call_index = 0

    for message in messages:
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            for tool_call in message.tool_calls:
                call_index += 1
                tool_name = tool_call.get("name") or "tool"
                tool_args = tool_call.get("args", {}) or {}
                tool_call_id = tool_call.get("id") or f"call_{call_index}"
                call_lookup[tool_call_id] = (call_index, tool_name, tool_args)
                tool_order_entries.append(
                    (call_index, f"{call_index}. {tool_name}({_format_summary_tool_args(tool_args)})")
                )
        elif isinstance(message, ToolMessage):
            content = str(message.content or "").strip()
            if not content:
                continue
            tool_call_id = getattr(message, "tool_call_id", "") or ""
            call_info = call_lookup.get(tool_call_id)
            if call_info:
                index, tool_name, tool_args = call_info
            else:
                index, tool_name, tool_args = len(tool_result_entries) + 1, tool_call_id or "tool", {}

            try:
                result_text = format_tool_summary(tool_name, tool_args, content)
            except Exception:
                result_text = ChatSession._clean_tool_result((tool_name, tool_args), content)

            tool_result_entries.append((index, f"{index}. {tool_name}: {result_text}"))

    tool_order_lines = [line for _, line in sorted(tool_order_entries, key=lambda item: (item[0], item[1]))]
    tool_result_lines = [line for _, line in sorted(tool_result_entries, key=lambda item: (item[0], item[1]))]
    return tool_order_lines, tool_result_lines

_SESSIONS_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "data" / "sessions"
_LLM_CONTEXT_LOG = Path(os.path.dirname(os.path.abspath(__file__))) / "log" / "chat_llm_context.jsonl"


class ChatSession:
    MAX_RECENT = 25       # 最近保留在上下文中的消息数
    COMPRESS_EVERY = 15    # 每新增 N 条触发一次压缩

    def __init__(self, session_id: str = "main", interactive: bool = True):
        self.session_id = session_id
        self.interactive = interactive
        self.llm = _build_llm(temperature=0.7, disable_thinking=True)
        tool_base = _build_llm(temperature=0.2, disable_thinking=True)
        self.tool_llm = tool_base.bind_tools(TOOLS, tool_choice="any")
        
        self._file = _SESSIONS_DIR / f"{session_id}.json"
        self._output_root = Path(langgraph_dir) / "output" / self.session_id
        self.prompt = _build_prompt(self.session_id)
        self._all_msgs: list = []
        self._cached_summary = ""
        self._read_cache: dict[str, str] = {}
        self._shell_allowed: bool = False
        self._load()

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps({
            "all_msgs": [m.model_dump() for m in self._all_msgs],
            "summary": getattr(self, "_cached_summary", ""),
        }, ensure_ascii=False, indent=2))

    def _load(self):
        SYSTEM_PROMPT = self.prompt
        if not self._file.exists():
            self._all_msgs = []
            self._cached_summary = ""
            self._rebuild_working_messages()
            return

        raw = json.loads(self._file.read_text())
        if isinstance(raw, list):
            # Old format: plain message list
            self._all_msgs = []
            for d in raw:
                t = d.get("type", "")
                if t == "human":
                    self._all_msgs.append(HumanMessage(content=d.get("content", "")))
                elif t == "ai":
                    kwargs = {k: v for k, v in d.items() if k not in ("type",)}
                    self._all_msgs.append(AIMessage(**kwargs))
            self._cached_summary = ""
        else:
            # New format: {"all_msgs": [...], "summary": "..."}
            self._all_msgs = []
            for d in raw.get("all_msgs", []):
                t = d.get("type", "")
                if t == "human":
                    self._all_msgs.append(HumanMessage(content=d.get("content", "")))
                elif t == "ai":
                    kwargs = {k: v for k, v in d.items() if k not in ("type",)}
                    self._all_msgs.append(AIMessage(**kwargs))
            self._cached_summary = raw.get("summary", "")
        self._rebuild_working_messages()

        # Clean trailing human messages
        cleaned = False
        while len(self._all_msgs) > 0 and isinstance(self._all_msgs[-1], HumanMessage):
            self._all_msgs.pop()
            cleaned = True
        if cleaned:
            self._save()

    def _rebuild_working_messages(self):
        """Build self.messages from cached summary + recent messages."""
        recent = self._all_msgs[-self.MAX_RECENT:] if len(self._all_msgs) > self.MAX_RECENT else list(self._all_msgs)
        msgs = [SystemMessage(content=self.prompt)]
        if self._cached_summary:
            msgs.append(HumanMessage(content=f"[历史摘要] {self._cached_summary}"))
        msgs.append(HumanMessage(content="请确认你的专业领域和身份"))
        msgs.append(AIMessage(content='我是计算化学AI助手。AutoMD = Automated Molecular Dynamics(自动化分子动力学)。我专门从事蛋白质-配体分子对接、分子动力学模拟和PyMOL三维分子可视化。我绝不涉及汽车诊断、文档管理或软件工程。用户说的[对接]永远指Molecular Docking(分子对接)，[1A2B]永远指PDB蛋白结构，[CCO]永远指小分子配体。'))
        msgs.extend(recent)
        self.messages = msgs

    def _maybe_compress(self):
        """Compress old messages when _all_msgs grows, always rebuild working msgs."""
        if not self._all_msgs:
            return
        full_history = list(self._all_msgs)
        effective_len = len(full_history)
        if self._cached_summary:
            effective_len += 1

        if effective_len > self.MAX_RECENT + self.COMPRESS_EVERY:
            to_compress = full_history[: -self.MAX_RECENT] if len(full_history) > self.MAX_RECENT else []
            if to_compress:
                lines = []
                if self._cached_summary:
                    lines.append(f"[历史摘要] {self._cached_summary}")
                for m in to_compress:
                    role = "用户" if isinstance(m, HumanMessage) else "助手"
                    lines.append(f"{role}: {str(m.content)[:300]}")
                compress_msgs = [
                    SystemMessage(content=COMPRESSOR_SYSTEM),
                    HumanMessage(content="请压缩以下对话:\n\n" + "\n".join(lines))
                ]
                try:
                    result = self.llm.invoke(compress_msgs)
                    self._cached_summary = (result.content or "").strip()[:200]
                except Exception:
                    self._cached_summary = self._cached_summary or ""

        self._save()
        self._rebuild_working_messages()  # always: strips intermediate tool msgs from last round

    def _append_to_all(self, msg):
        """Append a HumanMessage or final AIMessage to persistent history."""
        if isinstance(msg, HumanMessage):
            self._all_msgs.append(msg)
        elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            self._all_msgs.append(msg)

    # ── tool implementations ──────────────────────────────────────────

    def _workflow_summary(self, state: dict) -> str:
        parts = []
        if state.get("route"):
            parts.append(f"路由: {state['route']}")
        # 🆕 修: graph.py 节点填的是 *_summary 字段 (不是 *_result, 那是文件路径)
        for k, label in [
            ("protein_summary", "蛋白"),
            ("ligand_summary", "配体"),
            ("docking_summary", "对接"),
            ("md_summary", "MD"),
            ("analysis_summary", "分析"),
        ]:
            v = state.get(k)
            if v:
                parts.append(f"{label}: {v}")
        if not parts:
            return "流程已完成。\n（未获取到节点摘要, 请查看下方工作流日志）"
        return "流程已完成。\n" + "\n".join(f"- {item}" for item in parts)

    def _workflow_request_text(self, raw_task: str) -> str:
        _ = raw_task  # raw task is carried separately in workflow_request
        return "已请求执行工作流，请在下方工作流面板查看进度。"

    def _run_workflow_events(self, raw_task: str):
        """Yield structured workflow events from run_automd.

        The graph itself remains the source of truth; this adapter only
        normalizes interrupts and optionally prints a readable CLI transcript.
        """
        from graph import run_automd

        gen = run_automd(raw_task, thread_id=self.session_id)
        send_back = None
        report_state: dict = {}

        try:
            while True:
                msg = gen.send(send_back)
                if msg is None:
                    send_back = None
                    continue

                kind = msg.get("type")
                if kind == "section":
                    if self.interactive:
                        print(f"\n{msg.get('text', '')}")
                    yield msg
                elif kind == "step":
                    if self.interactive:
                        label = msg.get("label", "")
                        detail = msg.get("detail", "")
                        print(f"- {label}{(': ' + detail) if detail else ''}")
                    yield msg
                elif kind == "interrupt":
                    prompt = str(msg.get("text", ""))
                    if self.interactive:
                        print(f"\n[需要输入] {prompt}")
                        send_back = input("> ").strip() or "skip"
                        if self.interactive:
                            print(f"> 回复: {send_back}")
                        yield {"type": "interrupt", "text": prompt, "reply": send_back}
                    else:
                        send_back = "skip"
                        yield {"type": "interrupt", "text": prompt, "reply": "skip", "auto": True}
                elif kind == "report":
                    report_state = msg.get("state", {}) or {}
                    yield msg
        except StopIteration:
            pass

        return report_state

    def _read_output_file(self, path: str) -> str:
        # Path traversal check
        if ".." in path:
            return "[拒绝] 路径包含 '..' 不允许。"
        if path.startswith("LLM_PROMPT/"):
            return "[拒绝] 手册已从主流程移除，不再通过 read_output_file 直读。"
        path = str(Path(path))
        cached = self._read_cache.get(path)
        if cached is not None:
            return cached
        full = _resolve_path(path, session_id=self.session_id)
        if not full.exists():
            return f"文件不存在: {path}"
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
            result = content[:4000] if len(content) > 4000 else content
            self._read_cache[path] = result
            return result
        except Exception as e:
            return f"读取失败: {e}"

    def _list_outputs(self) -> str:
        files = []
        for root, _dirs, filenames in os.walk(str(self._output_root)):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(root, fn), str(self._output_root))
                files.append(rel)
        files.sort()
        return "\n".join(files[:80]) if files else "(output/ 目录为空)"

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool by name. Returns result text for ToolMessage."""
        if name == "permit_shell":
            self._shell_allowed = True
            return "shell 已授权（单次有效）。"
        if name == "read_output_file":
            return self._read_output_file(args.get("path", ""))
        if name == "list_outputs":
            return self._list_outputs()
        if name == "pymol_start":
            return pymol_start.func()
        if name == "pymol_quit":
            return pymol_quit.func()
        if name == "pymol_status":
            return pymol_status.func()
        if name == "pymol_execute":
            cli_list = args.get("cli_list") or args.get("CLI_LIST") or []
            return embed_pymol(cli_list)
        if name == "run_shell_command":
            if not self._shell_allowed:
                return "[权限拒绝] Shell命令需要用户明确许可。请用chat_reply向用户说明需要执行的命令并请求许可，用户同意后调用permit_shell授权，再调用run_shell_command。"
            self._shell_allowed = False  # one-shot
            cmd = args.get("command") or args.get("cmd") or ""
            cwd = args.get("cwd")
            timeout_seconds = args.get("timeout_seconds", 120)
            max_output_chars = args.get("max_output_chars", 20000)
            env = args.get("env")
            try:
                res = _run_shell_command(
                    command=cmd, cwd=cwd, timeout_seconds=timeout_seconds,
                    max_output_chars=max_output_chars, env=env,
                )
                return res.format_for_agent()
            except Exception as e:
                return f"run_shell_command 执行失败: {e}"
        return f"未知工具: {name}"

    @staticmethod
    def _clean_tool_result(tc_info: tuple | None, content: str) -> str:
        """Transform raw tool output for the summary LLM — preserve signal, strip noise."""
        content = str(content or "").strip()
        if not content:
            return content

        tool_name = tc_info[0] if tc_info else ""

        # pymol_execute: 工具调用 LLM 看到的是 embed_pymol() 输出的"逐条 CLI 富输出"
        # (形如 "1. {cmd} → OK: ..." / "1. {cmd} → ERROR: ..." / "1. {cmd} → EXCEPTION: ...")
        # 总结 LLM 看到的是"action 不可暴露, state 错误可暴露"的清洗版:
        #   - 不出现 CLI 字符串 (避免诱发"我应该再跑一条 color red, foo")
        #   - 不出现具体对象名/路径 (避免诱发动作建议)
        #   - 可出现"目标对象不存在或为空"这种 PyMOL state 错误, 让用户知道问题大致性质
        if re.search(r'^\d+\. .+?→\s*(?:OK|ERROR|EXCEPTION)', content, re.MULTILINE):
            oks = len(re.findall(r'→\s*OK\b', content))
            errs = len(re.findall(r'→\s*(?:ERROR|EXCEPTION)\b', content))

            if errs == 0:
                return f"PyMOL: {oks} 条命令全部成功"

            # 失败时: 提取"目标对象名"作为 state 错误, 不贴 CLI
            err_bodies = re.findall(
                r'^\d+\. .+?→\s*(?:ERROR|EXCEPTION):\s*(.+)$', content, re.MULTILINE
            )
            issues: list[str] = []
            for e in err_bodies[:2]:  # 最多 2 条错误, 避免过载
                # 匹配 PyMOL 常见错误中的"目标对象名"
                # 样本: "Object 'foo' not found" / "Selection 'bar' is empty"
                #       "Fragment myfrag is not loaded" / "Atom C5 not found"
                m = re.search(
                    r"(?:Object|Selection|Fragment|Atom|Residue|State)\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
                    e,
                )
                if m:
                    issues.append(f"'{m.group(1)}' 不存在或为空")
                else:
                    # 通用错误: 截短 50 字, 避免暴露任何含命令建议的字眼
                    issues.append(e[:50])
            msg = f"PyMOL: {oks} 成功, {errs} 失败"
            if issues:
                msg += "; 问题: " + "; ".join(issues)
            return msg

        # query_pymol_knowledge: 工具调用 LLM 看到的是完整的"合成 PyMOL CLI 答案"
        # (可能含多个代码块 + 解释段)。总结 LLM 只需要"是否成功检索到内容"三段式信号,
        # 不暴露具体 CLI 内容 (避免诱发"再调一次 pymol_execute 把这段跑一遍")。
        if tool_name == "query_pymol_knowledge":
            # 1) 工具层错误 (stub / 异常)
            if content.startswith("[PyMOL 知识库") or "检索失败" in content[:80] or "不可用" in content[:80]:
                return "PyMOL 知识库: 检索失败"
            # 2) 知识库里没找到
            if "未在知识库中找到" in content:
                return "PyMOL 知识库: 知识库中无相关答案"
            # 3) 正常: 成功检索
            return "PyMOL 知识库: 成功检索"

        # run_shell_command [SUCCESS]/[ERROR] format
        if content.startswith('[SUCCESS]') or content.startswith('[ERROR]'):
            # Extract exit_code
            ec_match = re.search(r'exit_code:\s*(\d+)', content)
            exit_code = ec_match.group(1) if ec_match else "?"
            status = "成功" if content.startswith('[SUCCESS]') else "失败"
            # Extract output section
            m = re.search(r'output:\s*(.*)', content, re.DOTALL)
            if m:
                tail = m.group(1).strip()
                if tail:
                    if len(tail) > 500:
                        tail = tail[:500] + "..."
                    return f"[shell exit={exit_code}] {tail}"
            return f"[shell exit={exit_code}] (无输出)"

        if tool_name == "read_output_file":
            limit = 800
            if len(content) > limit:
                return content[:limit] + "..."
            return content

        # pymol_status / pymol_start: summarize loaded objects, drop view matrices
        if tool_name in ("pymol_status", "pymol_start"):
            m = re.search(r'已加载对象:\s*(.*)', content)
            if m:
                objs_text = m.group(1).strip()
                if objs_text == '(空)':
                    return "PyMOL 未加载任何对象。"
                try:
                    objs_list = ast.literal_eval(objs_text)
                    if isinstance(objs_list, list):
                        n = len(objs_list)
                        disp = objs_list[:5]
                        more = f" 等 {n-5} 个" if n > 5 else ""
                        return f"PyMOL 已加载对象: {disp}{more}（共 {n} 个）"
                except Exception:
                    return f"PyMOL 已加载对象: {objs_text}"

        return content
    
    # 构建 总结功能的LLM 可见上文
    def _build_summary_messages(self):
        """Build the (SystemMessage, HumanMessage) pair used by the summary LLM.

        SystemMessage carries the persistent 'constitutional' rules (persona,
        tool semantics, HTML format spec, self-check, style).
        HumanMessage carries the per-call variable data (session_id, history,
        current request, tool trace).

        The prompt is assembled from structured messages rather than from the
        serialized LLM log, so it stays aligned with the actual user/session
        history and preserves tool ordering/results.
        """
        start = 0
        for i in range(len(self.messages) - 1, -1, -1):
            if isinstance(self.messages[i], HumanMessage):
                start = i
                break

        current_request = ""
        for m in reversed(self.messages[start:]):
            if isinstance(m, HumanMessage):
                current_request = str(m.content or "").strip()
                break

        recent_history_lines = _collect_recent_history_lines(self._all_msgs, limit=self.MAX_RECENT)
        tool_order_lines, tool_result_lines = _collect_current_tool_trace(self.messages[start:])

        system_text = _build_summary_system_block()
        human_text = _build_summary_human_block(
            current_request=current_request,
            recent_history_lines=recent_history_lines,
            tool_order_lines=tool_order_lines,
            tool_result_lines=tool_result_lines,
            history_summary=self._cached_summary,
            session_id=self.session_id,
        )

        return [
            SystemMessage(content=system_text),
            HumanMessage(content=human_text),
        ]

    async def _stream_summary(self):
        """Stream a final reply from the untooled summary LLM with clean prompt."""
        summary_msgs = self._build_summary_messages()
        log_llm_context(self.session_id, "summary_stream", summary_msgs, _LLM_CONTEXT_LOG)
        dsml_filter = DSMLStreamFilter()
        collected: list[str] = []
        async for chunk in self.llm.astream(summary_msgs):
            c = chunk.content if hasattr(chunk, "content") else str(chunk)
            if c:
                safe = dsml_filter.feed(c)
                if safe:
                    collected.append(safe)
                    yield {"type": "assistant_token", "token": safe}
        tail = dsml_filter.flush()
        if tail:
            collected.append(tail)
            yield {"type": "assistant_token", "token": tail}
        if collected:
            text = strip_dsml_text("".join(collected))
            text = sanitize_summary_output(text)
            final_msg = AIMessage(content=text)
            self.messages.append(final_msg)
            self._append_to_all(final_msg)
        self._maybe_compress()

    def _has_tool(self, tool_calls, name):
        """Check if tool_calls contains a specific tool name."""
        if not tool_calls:
            return False
        return any(tc.get("name") == name for tc in tool_calls)

    def _on_workflow_done(self, report_state: dict):
        """Called by WebSocket handler after workflow completes.
        Appends workflow summary so LLM knows results in the next turn."""
        summary = self._workflow_summary(report_state)
        tool_call_id = f"workflow_done_{self.session_id}_{uuid.uuid4().hex}"
        synthetic_call = {
            "name": "run_workflow",
            "args": {"raw_task": f"[工作流完成] session={self.session_id}"},
            "id": tool_call_id,
        }
        self.messages.append(AIMessage(content="", tool_calls=[synthetic_call]))
        self.messages.append(ToolMessage(
            content=f"[工作流完成] {summary}\n产物目录: output/{self.session_id}/",
            tool_call_id=tool_call_id))
        self._save()

    # ── server API ──────────────────────────────────────────────────

    async def ask_stream(self, user_message: str):
        """Multi-round tool-calling loop. Model outputs text when done → clean summary via summary LLM."""

        msg = sanitize_terminal_text(user_message)
        hmsg = HumanMessage(content=msg)
        self.messages.append(hmsg)
        self._append_to_all(hmsg)

        for _round in range(MAX_ROUNDS):
            log_llm_context(self.session_id, "tool_llm_stream", self.messages, _LLM_CONTEXT_LOG)
            ai_msg = self.tool_llm.invoke(self.messages)
            if ai_msg.content:
                cleaned = strip_dsml_text(ai_msg.content)
                if cleaned != ai_msg.content:
                    ai_msg = ai_msg.model_copy(update={"content": cleaned})
            self.messages.append(ai_msg)

            tool_calls = ai_msg.tool_calls if hasattr(ai_msg, "tool_calls") else []

            # ── Safety net: LLM produced no tool_calls ──
            if not tool_calls:
                self.messages.pop()
                async for evt in self._stream_summary():
                    yield evt
                return

            # ── chat_reply → exit loop, clean summary ──
            if self._has_tool(tool_calls, "chat_reply"):
                self.messages.pop()
                async for evt in self._stream_summary():
                    yield evt
                return

            # ── run_workflow: exclusive, handled specially ──
            if self._has_tool(tool_calls, "run_workflow"):
                for tc in tool_calls:
                    if tc.get("name") == "run_workflow":
                        raw_task = tc.get("args", {}).get("raw_task", "")
                        if self.interactive:
                            workflow_state: dict = {}
                            for event in self._run_workflow_events(raw_task):
                                if event.get("type") == "report":
                                    workflow_state = event.get("state", {}) or {}
                                yield event
                            result = self._workflow_summary(workflow_state)
                        else:
                            result = self._workflow_request_text(raw_task)
                            yield {"type": "workflow_request",
                                   "raw_task": raw_task, "text": result}
                            self.messages.append(ToolMessage(
                                content=result, tool_call_id=tc.get("id", "")))
                            self._save()
                            return  # web: SSE ends, frontend switches to WebSocket
                        self.messages.append(ToolMessage(
                            content=result, tool_call_id=tc.get("id", "")))
                        break  # only process the run_workflow call
                continue  # back to loop: LLM responds to workflow result

            # ── Execute other tools, continue loop ──
            for tc in tool_calls:
                if tc.get("name") == "chat_reply":
                    continue
                result = self._execute_tool(tc.get("name", ""),
                                            tc.get("args", {}) or {})
                self.messages.append(ToolMessage(
                    content=result, tool_call_id=tc.get("id", "")))

        # ── Exceeded max rounds: fallback to clean summary ──
        async for evt in self._stream_summary():
            yield evt

    def ask(self, user_message: str) -> str:
        """Blocking ask — multi-round tool calling. Model outputs text when done → summary LLM."""

        msg = sanitize_terminal_text(user_message)
        hmsg = HumanMessage(content=msg)
        self.messages.append(hmsg)
        self._append_to_all(hmsg)

        for _round in range(MAX_ROUNDS):
            log_llm_context(self.session_id, "tool_llm_blocking", self.messages, _LLM_CONTEXT_LOG)
            ai_msg = self.tool_llm.invoke(self.messages)
            if ai_msg.content:
                cleaned = strip_dsml_text(ai_msg.content)
                if cleaned != ai_msg.content:
                    ai_msg = ai_msg.model_copy(update={"content": cleaned})
            self.messages.append(ai_msg)

            tool_calls = ai_msg.tool_calls if hasattr(ai_msg, "tool_calls") else []

            def _finish(final_content):
                content = sanitize_summary_output(strip_dsml_text(final_content or ""))
                fmsg = AIMessage(content=content)
                self.messages.append(fmsg)
                self._append_to_all(fmsg)
                self._maybe_compress()
                return content

            # ── Safety net ──
            if not tool_calls:
                self.messages.pop()
                summary_msgs = self._build_summary_messages()
                log_llm_context(self.session_id, "summary_blocking", summary_msgs, _LLM_CONTEXT_LOG)
                final = self.llm.invoke(summary_msgs)
                return _finish(strip_dsml_text(final.content or ""))

            # ── chat_reply → clean summary ──
            if self._has_tool(tool_calls, "chat_reply"):
                self.messages.pop()
                summary_msgs = self._build_summary_messages()
                log_llm_context(self.session_id, "summary_blocking", summary_msgs, _LLM_CONTEXT_LOG)
                final = self.llm.invoke(summary_msgs)
                return _finish(strip_dsml_text(final.content or ""))

            # ── run_workflow ──
            if self._has_tool(tool_calls, "run_workflow"):
                for tc in tool_calls:
                    if tc.get("name") == "run_workflow":
                        raw_task = tc.get("args", {}).get("raw_task", "")
                        if self.interactive:
                            workflow_state: dict = {}
                            for event in self._run_workflow_events(raw_task):
                                if event.get("type") == "report":
                                    workflow_state = event.get("state", {}) or {}
                            result = self._workflow_summary(workflow_state)
                        else:
                            return self._workflow_request_text(raw_task)
                        self.messages.append(ToolMessage(
                            content=result, tool_call_id=tc.get("id", "")))
                        break
                continue

            # ── Execute other tools ──
            for tc in tool_calls:
                if tc.get("name") == "chat_reply":
                    continue
                result = self._execute_tool(tc.get("name", ""),
                                            tc.get("args", {}) or {})
                self.messages.append(ToolMessage(
                    content=result, tool_call_id=tc.get("id", "")))

        # ── Fallback to clean summary ──
        summary_msgs = self._build_summary_messages()
        log_llm_context(self.session_id, "summary_blocking", summary_msgs, _LLM_CONTEXT_LOG)
        final = self.llm.invoke(summary_msgs)

        return _finish(strip_dsml_text(final.content or ""))

    # ── main loop ─────────────────────────────────────────────────────

    def start(self):
        print(f"\n{'='*54}")
        print(f"  AutoMD Chat — session: {self.session_id}")
        print(f"{'='*54}")
        print("  输入 'quit' 或 'exit' 退出\n")

        print("助手> 你好，我是 AutoMD 助手。你可以直接输入任务，我会帮你完成对接、MD 和分析流程。")

        while True:
            try:
                user = sanitize_terminal_text(input("\n你> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user:
                continue
            if user.lower() in ("quit", "exit"):
                print("再见！")
                break

            reply = self.ask(user)
            print(f"\n助手> {reply or '(no response)'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoMD Chat LLM")
    parser.add_argument("--session", default="main", help="Session ID for multi-window isolation")
    parser.add_argument(
        "--test-embed-pymol",
        action="store_true",
        help="Run a minimal embed_pymol test (calls a few sample PyMOL CLI commands) and exit",
    )
    args = parser.parse_args()

    if args.test_embed_pymol:
        sample_cli = [
            "version",
            "get_session_info",
            "get_object_list",
            "load 1A2B.pdb",
            "fetch 1A2B",
            "show cartoon, all",
            "color chainbow, all",
            "orient",
            "zoom",
            "help",
        ]
        print("Calling embed_pymol with sample CLI:", sample_cli)
        try:
            out = embed_pymol(sample_cli)
            print("--- embed_pymol output ---")
            print(out)
        except Exception as e:
            print("embed_pymol raised:", repr(e))
        # Additionally try RPC-style queries which often return useful data
        try:
            server = _try_connect()
            if server is None:
                print("PyMOL RPC not connected (no server).")
            else:
                try:
                    sess = None
                    if hasattr(server, "get_session_info"):
                        sess = server.get_session_info()
                        print("--- server.get_session_info() ---")
                        print(repr(sess))
                    if hasattr(server, "get_object_list"):
                        objs = server.get_object_list()
                        print("--- server.get_object_list() ---")
                        print(repr(objs))
                except Exception as e:
                    print("RPC query raised:", repr(e))
        except Exception:
            pass
        sys.exit(0)

    ChatSession(session_id=args.session).start()
