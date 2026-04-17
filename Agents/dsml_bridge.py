import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage

from tools.use_tools import TOOL_MAP, sanitize_text

_DSML_INVOKE_RE = re.compile(
    r"<\｜DSML\｜invoke\s+name=\"(?P<name>[^\"]+)\"\>\s*(?P<body>.*?)\s*<\/\｜DSML\｜invoke\>",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r"<\｜DSML\｜parameter\s+name=\"(?P<key>[^\"]+)\"[^>]*\>(?P<value>.*?)<\/\｜DSML\｜parameter\>",
    re.DOTALL,
)

_DSML_BLOCK_RE = re.compile(r"<\｜DSML\｜function_calls\>[\s\S]*?<\/\｜DSML\｜function_calls\>")


def extract_dsml_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    calls: List[Tuple[str, Dict[str, Any]]] = []
    if "<｜DSML｜" not in text:
        return calls

    for match in _DSML_INVOKE_RE.finditer(text):
        tool_name = (match.group("name") or "").strip()
        body = match.group("body") or ""
        args: Dict[str, Any] = {}
        for p in _DSML_PARAM_RE.finditer(body):
            key = (p.group("key") or "").strip()
            value = (p.group("value") or "").strip()
            args[key] = value
        if tool_name:
            calls.append((tool_name, args))

    return calls


def _coerce_value(value: str) -> Any:
    v = value.strip()
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False

    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return value


def run_tool_by_name(tool_name: str, args: Dict[str, Any]) -> str:
    func = TOOL_MAP.get(tool_name)
    if func is None:
        return f"错误：未找到工具 {tool_name}（TOOL_MAP 未注册）。"

    parsed_args = {k: _coerce_value(str(v)) for k, v in args.items()}
    try:
        return str(func(**parsed_args))
    except TypeError as exc:
        return f"工具参数不匹配：{tool_name}({parsed_args}) -> {exc}"
    except Exception as exc:
        return f"工具执行异常：{tool_name}({parsed_args}) -> {type(exc).__name__}: {exc}"


def _tool_allowed(tool_name: str, allowed_functions: Optional[Iterable[str]]) -> bool:
    if allowed_functions is None:
        return True
    return tool_name in set(allowed_functions)


def redact_dsml_markup(text: str) -> str:
    if not text:
        return text
    redacted = _DSML_BLOCK_RE.sub("[已隐藏模型伪工具协议，见下方真实工具调用可视化]", text)
    return redacted.strip()


async def run_agent_with_dsml_visualization(
    agent: AssistantAgent,
    task: str,
    *,
    max_bridge_rounds: int = 3,
    bridge_prefix: str = "[DSML Bridge]",
    verbose: bool = True,
    allowed_functions: Optional[Iterable[str]] = None,
) -> str:
    """Run an agent, display its text and any DSML pseudo-calls as real tool execution."""
    last_assistant_text: Optional[str] = None
    async for msg in agent.run_stream(task=sanitize_text(task)):
        if isinstance(msg, TextMessage) and msg.source == agent.name:
            last_assistant_text = msg.content or ""
            if verbose:
                print(f"\n{bridge_prefix} {agent.name} 思考/输出:")
                visible_text = redact_dsml_markup(last_assistant_text)
                print(visible_text or "[模型输出已被协议块占满，见下方工具调用可视化]")

    for round_index in range(max_bridge_rounds):
        if not last_assistant_text:
            break

        dsml_calls = extract_dsml_calls(last_assistant_text)
        if not dsml_calls:
            break

        if verbose:
            print(f"\n{bridge_prefix} 第 {round_index + 1} 轮工具调用可视化:")
        tool_results: List[str] = []
        for name, args in dsml_calls:
            if not _tool_allowed(name, allowed_functions):
                result = f"拒绝执行未授权工具：{name}（未在当前 Agent allowlist 中）"
                if verbose:
                    print(f"- 工具: {name}")
                    print(f"  结果: {result}")
                tool_results.append(f"tool={name}, args={args}\nresult:\n{result}")
                continue
            if verbose:
                print(f"- 工具: {name}")
                if args:
                    print(f"  参数: {args}")
            result = run_tool_by_name(name, args)
            if verbose:
                print(f"  结果: {result}")
            tool_results.append(f"tool={name}, args={args}\nresult:\n{result}")

        bridge_task = (
            "上一轮你输出了 DSML 伪调用；系统不会自动执行。我已把结果执行并可视化如下：\n\n"
            + "\n\n".join(tool_results)
            + "\n\n请基于上述结果继续推进，禁止再输出 DSML，直接给出新的自然语言结论。"
        )

        last_assistant_text = None
        async for msg in agent.run_stream(task=bridge_task):
            if isinstance(msg, TextMessage) and msg.source == agent.name:
                last_assistant_text = msg.content or ""
                if verbose:
                    print(f"\n{bridge_prefix} {agent.name} 继续思考/输出:")
                    visible_text = redact_dsml_markup(last_assistant_text)
                    print(visible_text or "[模型输出已被协议块占满，见下方工具调用可视化]")

    return redact_dsml_markup(last_assistant_text or "")