#!/usr/bin/env python3
"""
AutoMD Knowledge Retrieval LLM
==============================

按 chat.py 中 tool-calling LLM 的写法, 仿写一个轻量的"知识检索 LLM"。
它从 AutoMD 项目的所有知识库里拉文档、读文档、合成简洁可执行的答案。

设计目标
--------
1. 与 chat.py 风格一致: `_build_llm()` + `@tool` 装饰器 + `bind_tools(TOOLS, tool_choice="any")`
   + 多轮 tool-calling 循环 + `_execute_tool()` dispatch。
2. 工具极简: 只暴露 `read_file` 和 `chat_reply`, 没有 pymol_execute / run_shell_command 等副作用工具。
3. 入口轻量: 不依赖 nodes/、tools/、visual_docking, 只依赖 langchain_*。
4. 一次一问 (stateless per query): 每调一次 `ask()`, 重新初始化消息历史, 互不污染。
5. **多 KB 支持**: 自动扫描 `knowledge/` 下所有含 INDEX.md 的子目录作为独立知识库,
   让用户能按 pymol 格式自建新 KB (例 `knowledge/cpptraj-analysis/`)。

用法
----
模块化调用:
    from LLM.retrieval_llm import RetrievalSession
    rs = RetrievalSession()
    answer = rs.ask("怎么找出配体周围 5Å 的所有蛋白残基?")
    # 也可以指定 KB 根:
    rs2 = RetrievalSession(kb_root=Path("/path/to/single/kb"))

命令行调试:
    python D:\\AutoMD\\LLM\\retrieval_llm.py "怎么做出出版级的 PyMOL 截图?"

如何新建一个知识库
------------------
1. 创建目录 `knowledge/<your-topic>/`
2. 写一个 INDEX.md, 格式参考 `knowledge/pymol/INDEX.md`:
   - 顶部说明 KB 是什么, 给谁用
   - 列出所有子文档的相对路径 (例 `interactions/h-bond.md`)
   - 可选: 关键词速查表
3. 放原子文档到子目录 (例 `interactions/h-bond.md`), 格式跟 pymol 那 25 个 .md 一样
4. 完成。本 LLM 模块加载时会自动扫描并列出新 KB
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import Optional

from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool


# ── Configuration ────────────────────────────────────────────────────────

# 候选 KB 根目录, 按优先级排, 第一个存在且是目录的就用。
# 两种模式:
#   1) PYMOL_KB_ROOT 环境变量 → 指向单个 KB 目录 (向后兼容旧配置)
#   2) 未设置 → 默认指向 knowledge/ 父目录, 模块加载时扫描其下所有含 INDEX.md 的子目录
# 同时支持 Windows Python (D:\...) 和 WSL Python (/mnt/d/...),
# 避免在不同环境下 KB 解析失败。
_KB_ROOT_CANDIDATES: list[Optional[str]] = [
    os.getenv("PYMOL_KB_ROOT"),                              # 单 KB 模式 (兼容旧 env)
    r"D:\AutoMD\knowledge",                                   # Windows Python 默认父目录
    "/mnt/d/AutoMD/knowledge",                                # WSL Python 默认父目录
    "/d/AutoMD/knowledge",                                    # WSL 备用挂载点
]

# read_file 的"兜底前缀剥离"也会遍历这些根, 加上 CWD 一起尝试。
_KB_TRY_ROOTS: list[Path] = [
    Path.cwd(),                                                # 当前工作目录
    Path(r"D:\AutoMD"),                                        # Windows 项目根
    Path("/mnt/d/AutoMD"),                                     # WSL 项目根
    Path("/d/AutoMD"),                                         # WSL 备用
]


def _resolve_kb_root() -> Path:
    """挑出当前 Python 能看到的 KB 根 (兼容 Windows Python + WSL Python)。

    注意: KB_ROOT 指的是"知识库们的父目录" (即 `knowledge/`),
    真正的 KB 是其下含 INDEX.md 的子目录。模块加载时进一步扫描。
    特殊: 若环境变量 PYMOL_KB_ROOT 指向含 INDEX.md 的子目录, 则直接当单 KB 用
    (向后兼容, 行为不变)。
    """
    # 优先解析 PYMOL_KB_ROOT (单 KB 模式)
    py_kb = os.getenv("PYMOL_KB_ROOT")
    if py_kb:
        for try_path in (
            py_kb,
            str(Path(py_kb) / "pymol"),  # 兼容旧版直接指到 knowledge/ 父目录的情况
        ):
            try:
                p = Path(try_path).resolve()
                if p.exists() and p.is_dir():
                    return p
            except Exception:
                continue

    # 默认: knowledge/ 父目录
    for c in _KB_ROOT_CANDIDATES[1:]:  # 跳过 PYMOL_KB_ROOT
        if not c:
            continue
        try:
            p = Path(c).resolve()
            if p.exists() and p.is_dir():
                return p
        except Exception:
            continue
    # 全失败: 返回默认候选 (后续 read_file 都会报"文件不存在",
    # 但至少 import 不会崩, 错误信息对调试也友好)
    fallback = next((c for c in _KB_ROOT_CANDIDATES if c), None)
    return Path(fallback or r"D:\AutoMD\knowledge")


KB_ROOT: Path = _resolve_kb_root()
"""模块加载时一次性解析的"知识库父目录"。

- 若 PYMOL_KB_ROOT 指向单 KB, KB_ROOT 就是那个单 KB (向后兼容)
- 否则 KB_ROOT = `knowledge/` 父目录, 含 1+ 个含 INDEX.md 的子 KB
"""


def _scan_knowledge_bases(kb_root: Path) -> list[dict]:
    """扫描 kb_root 下所有含 INDEX.md 的子目录, 返回 KB 列表。

    行为:
    - 若 kb_root 本身有 INDEX.md, 视为"单 KB" (向后兼容, kb_root 自身就是 KB)
    - 否则扫描 kb_root 的所有子目录, 找含 INDEX.md 的当 KB
    - KB 按名字排序

    返回: [{"name": "pymol", "path": Path(".../knowledge/pymol"), "index": Path(".../INDEX.md"),
             "file_count": 25, "subdirs": ["load", "select", ...]}, ...]
    """
    results: list[dict] = []

    def _describe_kb(name: str, path: Path) -> dict | None:
        index = path / "INDEX.md"
        if not index.is_file():
            return None
        # 数 .md 文件
        try:
            md_files = [p for p in path.rglob("*.md") if p.is_file()]
            subdirs = sorted({p.parent.relative_to(path).parts[0]
                              for p in md_files
                              if p.parent != path and len(p.parent.relative_to(path).parts) >= 1})
        except Exception:
            md_files, subdirs = [], []
        return {
            "name": name,
            "path": path,
            "index": index,
            "file_count": len(md_files),
            "subdirs": subdirs,
        }

    # 情况 1: kb_root 自身就是 KB (单 KB 模式, 向后兼容)
    direct = _describe_kb(kb_root.name, kb_root)
    if direct:
        results.append(direct)
        return results

    # 情况 2: kb_root 是父目录, 扫子目录
    if not kb_root.is_dir():
        return results
    for child in sorted(kb_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith((".", "__")):
            continue
        info = _describe_kb(child.name, child)
        if info:
            results.append(info)
    return results


KNOWLEDGE_BASES: list[dict] = _scan_knowledge_bases(KB_ROOT)
"""模块加载时扫描出的 KB 列表。每项含 name / path / index / file_count / subdirs。"""


def _format_kb_list_for_prompt_for(knowledge_bases: list[dict], kb_root: Path) -> str:
    """把 knowledge_bases 列表格式化成 prompt 注入用的文本 (相对 kb_root 算 path)。"""
    if not knowledge_bases:
        return (
            f"⚠️ 知识库根目录 {kb_root} 下未找到任何含 INDEX.md 的子目录。\n"
            f"   请确认 knowledge/ 存在, 或检查文件权限。"
        )
    lines = ["可用知识库:"]
    for kb in knowledge_bases:
        rel = kb["path"].relative_to(kb_root) if kb_root in kb["path"].parents or kb["path"] == kb_root else kb["path"]
        if str(rel) in (".", ""):
            display_path = ""
        else:
            display_path = f"{rel}/"
        subdir_summary = ", ".join(kb["subdirs"][:8]) if kb["subdirs"] else "(无分类子目录, 所有 .md 平铺)"
        lines.append(
            f"  - **{kb['name']}** (`{display_path}`, {kb['file_count']} 个 .md 文件)\n"
            f"    入口: `{display_path}INDEX.md`\n"
            f"    分类: {subdir_summary}"
        )
    return "\n".join(lines)


def _format_kb_list_for_prompt() -> str:
    """Backward-compat wrapper using the module-level KNOWLEDGE_BASES / KB_ROOT."""
    return _format_kb_list_for_prompt_for(KNOWLEDGE_BASES, KB_ROOT)

MAX_ROUNDS = 8
"""工具调用轮数上限。INDEX + 2-3 个原子文档大约 4-5 轮, 8 留足缓冲。"""

MAX_RECENT = 6
"""历史消息上限。检索场景短上下文够用, 不需要压缩。"""

MAX_FILE_CHARS = 8000
"""单文件读取字符上限。超出截断, 避免 token 爆掉。"""


# ── LLM builder (mimics chat.py's _build_llm) ───────────────────────────

def _build_llm(temperature: float = 0.2, disable_thinking: bool = True) -> ChatDeepSeek:
    """构造检索 LLM。模型默认 deepseek-v4-flash, 跟 chat.py 一致。
    temperature=0.2 (低, 偏确定性), thinking 关闭 (少干扰)。
    """
    api_key = os.getenv("LLM_API_KEY")
    model_name = os.getenv("PYMOL_RETRIEVAL_MODEL", "deepseek-v4-flash")
    kwargs: dict = {}
    if disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatDeepSeek(
        model=model_name,
        temperature=temperature,
        max_tokens=4096,
        api_key=api_key,
        model_kwargs=kwargs,
    )


# ── System prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """你是 AutoMD 的知识检索助手。
你的唯一职责: 根据用户的问题, 从 AutoMD 的知识库中检索最相关的文档, 并综合成简洁、可执行的答案。

## 知识库结构
根目录: {kb_root}/

{kb_list}

每个知识库 (KB) 的入口是它自己的 `INDEX.md` 文件。INDEX.md 是检索的"地图", 必先读它, 凭印象选文件是错的。

## ⚠️ read_file 路径规则 (最容易出错的地方, 务必严格遵守)
`path` 参数是 **相对 {kb_root}/ 的相对路径**。**绝对不要**把 KB 根目录本身拼进去, 也不要拼知识库名 (因为一个查询可能跨多个 KB)。

  ✅ 正确示例 (假设 {kb_root} 是 D:\\AutoMD\\knowledge):
    - read_file("pymol/INDEX.md")                       — 知识库名 + INDEX
    - read_file("pymol/interactions/h-bond.md")         — 知识库名 + 子目录 + 文件
    - read_file("cpptraj-analysis/INDEX.md")            — 另一个 KB 的入口
    - read_file("amber-topology/some-doc.md")           — 另一个 KB 的子文件

  ❌ 错误示例 (会被工具直接拒掉或找不到):
    - read_file("D:\\\\AutoMD\\\\knowledge\\\\pymol\\\\INDEX.md")   — 绝对路径
    - read_file("./pymol/INDEX.md")                              — 不要加点
    - read_file("/pymol/INDEX.md")                               — 不要斜杠开头
    - read_file("INDEX.md")                                      — 路径里必须有知识库名, 不能只给 INDEX.md

  即使 system prompt 上面写了 KB 的绝对路径 (只是为了让你知道知识库在哪儿), **也不要**把它当 path 拼进去。

## 检索策略 (如何选 KB)
1. **先判断问题属于哪个 KB** (看用户问题主题, 与上面的"可用知识库"列表对照)
   - 例: 问 PyMOL CLI → `pymol/`
   - 问 cpptraj → `cpptraj-analysis/` (若有)
   - 主题模糊 → 读所有相关 KB 的 INDEX.md
2. **第 1 步**: 调 read_file(path="<kb_name>/INDEX.md") 拿到该 KB 的索引
3. **第 2 步**: 根据 INDEX.md, 调 read_file(path="<kb_name>/<选中的 1-3 个相对路径>")
4. **第 3 步**: 调 chat_reply(answer="<最终答案>")

  - 至少读 1 个 KB 的 INDEX.md。简单问题读 1 个 INDEX + 1 个原子文档即可, 复杂问题可读 2-3 个 KB 的 INDEX + 2-3 个原子文档
  - 总共**最多读 5 个文件** (避免 token 浪费)
  - 读完就立刻 chat_reply, **不要反复读同一个文件**

## 答案要求
- **直接给可执行代码块** (PyMOL CLI / bash / Python, 看 KB 内容定), 不要寒暄、不要写"以下是答案:"之类的话。
- 包含完整的 import/加载/显示/截图 流程 (如果用户问题涉及)。
- 如果多个文档给出不同写法, 选最简洁、项目标准的那一个。
- 路径用 Windows 风格 D:\\\\..., **绝不能用 WSL 风格 /mnt/d/**。
- 默认配色 (如果 KB 是 PyMOL): 蛋白 cyan, 配体 yellow, 口袋 magenta/hotpink, H 键 yellow 虚线, 疏水 orange 虚线。
- 默认 binding site 距离: 5Å。
- 如果知识库里没答案, 老实说"未在知识库中找到, 请补充知识后重试", **绝不要编造 CLI 代码**。
- 简洁但完整, 一个代码块就够, 不要堆 3 个变体。

## 工具使用规则
- 你只有两个工具: read_file 和 chat_reply。
- chat_reply(answer=...) 只在最后一轮调用一次, answer 是要返回的最终文本。
- 不要尝试调其他工具 (本 LLM 也没有提供其他工具)。
"""


# ── Path safety ──────────────────────────────────────────────────────────

def _resolve_kb_path(rel_path: str) -> Path:
    """把相对路径拼到 KB_ROOT, 并拒绝逃出知识库 (防 path traversal)。"""
    rel = (rel_path or "").strip().lstrip("/\\")
    full = (KB_ROOT / rel).resolve()
    if not str(full).startswith(str(KB_ROOT.resolve())):
        raise ValueError(f"Path escapes knowledge base: {rel_path!r}")
    return full


# ── Tools (mimics chat.py's @tool definitions) ───────────────────────────

@tool
def read_file(path: str) -> str:
    """读取知识库中的某个文档内容。

    参数 path: **相对 KB 根目录的相对路径**, 例 "INDEX.md" 或 "interactions/h-bond.md"。
              绝对路径 / 含 KB 根前缀的路径 / 以 "./" 或 "/" 开头的路径都会被拒或找不到。

    何时用: 检索信息时, 第一步必先 read_file("INDEX.md"), 再根据 INDEX 选 1-3 个原子文档读。
    何时不用: 读完该读的就停, 不要无限循环读; 不允许读取知识库外的文件 (会被拒绝)。
    """
    try:
        full = _resolve_kb_path(path)
    except ValueError as e:
        return f"[拒绝] {e}"

    # 🛟 兜底: LLM 偶尔会自作聪明把 KB 根前缀拼进 path, 导致 full 不存在。
    # 这种情况我们"剥前缀"再试一次: 把 path 当作"项目根或 CWD 的相对路径",
    # 解析后看结果是不是落到了 KB 内部且确实存在, 是就当正解用。
    if not full.exists():
        kb_str = str(KB_ROOT.resolve())
        for raw in (path, str(Path(path).as_posix())):
            for try_root in _KB_TRY_ROOTS:
                try:
                    maybe = (try_root / raw).resolve()
                    if str(maybe).startswith(kb_str) and maybe.exists() and maybe.is_file():
                        full = maybe
                        break
                except Exception:
                    continue
            if full.exists():
                break

    if not full.exists():
        # 友好错误: 给出"附近有什么"提示, 帮 LLM 自我纠正
        if path and "/" in path:
            sub = path.split("/")[0]
            subdir = KB_ROOT / sub
            if subdir.exists() and subdir.is_dir():
                nearby = sorted(
                    p.name for p in subdir.iterdir() if p.suffix == ".md"
                )[:5]
                return (
                    f"文件不存在: {path}\n"
                    f"💡 '{sub}/' 子目录下的 .md 文件: {nearby}\n"
                    f"💡 path 必须是相对 KB 根的相对路径, 不能含 KB 根的前缀。"
                )
        return (
            f"文件不存在: {path}\n"
            f"💡 path 必须是相对 KB 根的相对路径, 例: 'INDEX.md' 或 'interactions/h-bond.md'。"
        )

    if not full.is_file():
        return f"不是文件: {path}"
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_FILE_CHARS:
            return (
                content[:MAX_FILE_CHARS]
                + f"\n\n[... 已截断, 原文共 {len(content)} 字符, 只读前 {MAX_FILE_CHARS} 字符]"
            )
        return content
    except Exception as e:
        return f"读取失败: {e}"


@tool
def chat_reply(answer: str) -> str:
    """把综合后的答案返回给调用方。**仅在最后一轮调用一次**。

    参数 answer: 简洁、可执行的 PyMOL CLI 答案 (含必要的代码块)。这是本 LLM 的产出。

    何时用: 已读完 INDEX + 相关原子文档, 准备给最终答案。
    何时不用: 还没读 INDEX, 或还没读完相关文档, 或还想再多读几个。
    """
    return "ok"


TOOLS = [read_file, chat_reply]


# ── Session (mimics chat.py's ChatSession, but stateless-per-query) ──────

class RetrievalSession:
    """轻量检索会话。每次 ask() 都从零开始, 不留跨调用的状态。"""

    def __init__(self, kb_root: Optional[Path] = None):
        # 决定本次 session 用哪个 KB 根 (默认用模块加载时扫描的)
        if kb_root is not None:
            # 显式传入, 临时构造一个"单 KB 模式"的 KB 列表
            self.kb_root = kb_root
            if (kb_root / "INDEX.md").is_file():
                # 直接当 KB 用
                self.knowledge_bases = _scan_knowledge_bases(kb_root)
            else:
                # 当作父目录扫
                self.knowledge_bases = _scan_knowledge_bases(kb_root)
        else:
            self.kb_root = KB_ROOT
            self.knowledge_bases = KNOWLEDGE_BASES
        self.llm = _build_llm(temperature=0.2, disable_thinking=True)
        self.tool_llm = self.llm.bind_tools(TOOLS, tool_choice="any")
        self.system = SYSTEM_PROMPT_TEMPLATE.format(
            kb_root=str(self.kb_root),
            kb_list=_format_kb_list_for_prompt_for(self.knowledge_bases, self.kb_root),
        )
        # 不在 __init__ 预建 messages, 每次 ask() 重新建 (stateless)
        self.messages: list = []

    # ── tool dispatch (mimics ChatSession._execute_tool) ──────────────

    def _execute_tool(self, name: str, args: dict) -> tuple[str, bool]:
        """分发一个工具调用。返回 (ToolMessage 文本, 是否 final)。"""
        if name == "read_file":
            return read_file.func(args.get("path", "")), False
        if name == "chat_reply":
            return args.get("answer", ""), True
        return f"未知工具: {name}", False

    # ── public API ────────────────────────────────────────────────────

    def ask(self, query: str) -> str:
        """单次检索: 一次问一个问题, 返回合成后的答案字符串。

        内部走最多 MAX_ROUNDS 轮 tool-calling 循环, 直到 LLM 调 chat_reply 为止。
        """
        # 每次重新建消息历史 (stateless per query)
        self.messages = [SystemMessage(content=self.system)]
        self.messages.append(HumanMessage(content=query))

        for _round in range(MAX_ROUNDS):
            ai_msg = self.tool_llm.invoke(self.messages)
            self.messages.append(ai_msg)

            tool_calls = ai_msg.tool_calls or []

            # 安全网: LLM 没出 tool_calls (异常路径), 用它的 content 当答案
            if not tool_calls:
                return (ai_msg.content or "").strip()

            final_answer = ""
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {}) or {}
                result, is_final = self._execute_tool(name, args)
                if is_final:
                    final_answer = result
                self.messages.append(ToolMessage(
                    content=result, tool_call_id=tc.get("id", "")))

            if final_answer:
                return final_answer

        # 用尽 MAX_ROUNDS 仍没 chat_reply — 退回最后一条 AI 消息的 content
        return (
            (self.messages[-1].content or "").strip()
            or "(未在限定轮数内得到答案, 请检查问题或缩短查询)"
        )

    # ── debug helper ──────────────────────────────────────────────────

    def trace(self) -> list[dict]:
        """返回当前会话的消息轨迹 (用于调试)。每条是 {role, content, tool_calls}。"""
        out: list[dict] = []
        for m in self.messages:
            if isinstance(m, SystemMessage):
                out.append({"role": "system", "content": (m.content or "")[:200]})
            elif isinstance(m, HumanMessage):
                out.append({"role": "user", "content": m.content or ""})
            elif isinstance(m, AIMessage):
                tcs = m.tool_calls or []
                out.append({
                    "role": "assistant",
                    "content": (m.content or "")[:200],
                    "tool_calls": [
                        {"name": tc.get("name"), "args": tc.get("args")}
                        for tc in tcs
                    ],
                })
            elif isinstance(m, ToolMessage):
                out.append({
                    "role": "tool",
                    "name": getattr(m, "name", "") or "",
                    "content": (m.content or "")[:200],
                })
        return out


# ── Public wrapper (最常用入口) ─────────────────────────────────────────

def query_pymol_knowledge(query: str, kb_root: Optional[Path] = None) -> str:
    """用自然语言查询 PyMOL 知识库, 返回合成的可执行 PyMOL CLI 答案。

    这是模块的"门面函数", 适合被外部代码直接调用 (例如 chat.py 把它包装成工具)。
    内部每次新建一个 RetrievalSession (stateless per query, 不留上下文)。

    参数 query: 任意自然语言问题, 例如 "怎么做出出版级截图?" 或 "PyMOL 报路径错怎么办?"
    参数 kb_root: 可选, 覆盖默认 KB_ROOT (默认从环境变量 PYMOL_KB_ROOT 读)
    返回: 字符串, 含 PyMOL CLI 代码块

    用法:
        from LLM.retrieval_llm import query_pymol_knowledge
        print(query_pymol_knowledge("怎么找 binding site 残基?"))
    """
    return RetrievalSession(kb_root=kb_root).ask(query)


# ── CLI entry (用于本地调试) ─────────────────────────────────────────────

# 示例问题: 覆盖 PyMOL KB 核心场景。
# 这些问题都用上一步扫到的 KB, 真实环境若有更多 KB 可继续追加。
_EXAMPLES = [
    ("加载", "怎么把一个 PDBQT 蛋白和 MOL2 配体加载到 PyMOL? 用 `receptor` 和 `ligand` 作为对象名。"),
    ("口袋", "怎么找配体周围 5Å 内的所有蛋白残基, 并用 sticks 高亮显示?"),
    ("H 键", "怎么显示蛋白-配体之间的氢键? 距离阈值、虚线颜色、距离对象名各用什么?"),
    ("出版级截图", "怎么做出出版级质量的 PyMOL 截图? 要含 ray、antialias、dpi 等参数。"),
    ("路径错误", "PyMOL 报 'Could not open file' 怎么办? 路径应该用 Windows 风格还是 WSL 风格?"),
    ("野生型 vs 突变型", "怎么把野生型和突变型蛋白叠在一起比较结构差异? 怎么高亮突变位点?"),
    ("配色约定", "AutoMD 项目的标准配色是什么? 蛋白、配体、口袋残基、H 键、疏水各用什么颜色?"),
    ("标准 binding view", "给我一个完整的蛋白-配体结合模式可视化代码, 含卡通、半透明、口袋侧链、H 键、疏水、残基标签。"),
]


def _run_single_query(query: str) -> None:
    """跑单个查询, 印 query + answer + trace。"""
    print(f"\n{'=' * 70}")
    print(f"[query] {query}")
    print("=" * 70)
    rs = RetrievalSession()
    answer = rs.ask(query)
    print(answer)
    _print_trace(rs, prefix="[single]")


def _print_trace(rs: RetrievalSession, prefix: str = "") -> None:
    """🔍 诊断: 把会话消息轨迹 (role / content / tool_calls) 印出来, 方便排查 LLM 行为。"""
    print(f"\n--- 🔍 {prefix} trace ---")
    for m in rs.trace():
        role = m.get("role", "")
        if role == "tool":
            print(f"  🔧 tool[{m.get('name', '?')}]: {m.get('content', '')[:160]!r}")
        elif role == "assistant":
            tcs = m.get("tool_calls") or []
            if tcs:
                tc_summary = ", ".join(
                    f"{tc.get('name')}({tc.get('args')})" for tc in tcs
                )
                print(f"  🤖 assistant → tool_calls: {tc_summary}")
            else:
                content = m.get("content") or ""
                if content:
                    print(f"  🤖 assistant: {content[:160]!r}")
        elif role == "user":
            print(f"  👤 user: {m.get('content', '')[:160]!r}")
        elif role == "system":
            print(f"  📋 system: {m.get('content', '')[:80]!r}...")


def main():
    # 简单的 .env 兼容 (跟 chat.py 一致)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    if len(sys.argv) >= 2:
        # CLI 模式: 跑单个查询
        query = " ".join(sys.argv[1:])
        _run_single_query(query)
        return

    # Demo 模式: 跑 8 个示例
    print("=" * 70)
    print("  AutoMD Knowledge Retrieval LLM — Demo")
    print(f"  Python  : {sys.executable}")
    print(f"  CWD     : {Path.cwd()}")
    print(f"  KB_ROOT : {KB_ROOT}")
    print(f"  KB 数   : {len(KNOWLEDGE_BASES)}")
    for kb in KNOWLEDGE_BASES:
        print(f"    - {kb['name']}  ({kb['file_count']} .md files, "
              f"categories: {', '.join(kb['subdirs']) or '(flat)'})")
    print(f"  示例数  : {len(_EXAMPLES)}")
    print("=" * 70)

    for i, (tag, query) in enumerate(_EXAMPLES, 1):
        print(f"\n{'─' * 70}")
        print(f"[示例 {i}/{len(_EXAMPLES)}] ({tag}) {query}")
        print(f"{'─' * 70}")
        rs = RetrievalSession()
        answer = rs.ask(query)
        print(answer)
        # _print_trace(rs, prefix=f"示例 {i}")

    print(f"\n{'=' * 70}")
    print("  全部示例跑完。")
    print("=" * 70)


if __name__ == "__main__":
    main()
