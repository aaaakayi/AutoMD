"""DSML-safe model client wrapper.

Wraps OpenAIChatCompletionClient to intercept API responses. When DeepSeek
returns DSML pseudo-protocol text instead of standard OpenAI tool_calls,
this wrapper parses the DSML into proper FunctionCall objects so that
AutoGen's native tool execution pipeline handles them automatically.
"""

from typing import Any, AsyncGenerator, List, Mapping, Optional, Sequence

from autogen_core.models import CreateResult, RequestUsage
from autogen_core.models._types import FunctionCall
from autogen_ext.models.openai import OpenAIChatCompletionClient

from orchestration.dsml_utils import has_dsml, parse_dsml_to_function_calls


class DSMLSafeOpenAIClient(OpenAIChatCompletionClient):
    """OpenAIChatCompletionClient that converts DSML text into FunctionCall objects.

    When the underlying API (e.g. DeepSeek) returns DSML-formatted tool calls
    embedded in the text content field instead of standard tool_calls, this
    client parses them into proper FunctionCall objects. AutoGen's
    AssistantAgent then sees native function calls and processes them through
    the standard tool execution loop.

    If the model already returns proper tool_calls (no DSML), the wrapper is
    a transparent pass-through with zero overhead beyond a string check.
    """

    async def create(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] | None = None,
        tool_choice: Any = "auto",
        json_output: bool | None = None,
        extra_create_args: Mapping[str, Any] | None = None,
        cancellation_token: Any = None,
    ) -> CreateResult:
        # Normalize tools: convert None to empty list
        if tools is None:
            tools = []
        if extra_create_args is None:
            extra_create_args = {}

        result = await super().create(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )

        # Only intercept string content that contains DSML markup.
        if not isinstance(result.content, str) or not has_dsml(result.content):
            return result

        fc_dicts = parse_dsml_to_function_calls(result.content)
        if not fc_dicts:
            return result

        function_calls: List[FunctionCall] = [
            FunctionCall(
                id=d["id"],
                name=d["name"],
                arguments=d["arguments"],
            )
            for d in fc_dicts
        ]

        return CreateResult(
            content=function_calls,
            thought=result.content,
            finish_reason=result.finish_reason,
            usage=result.usage or RequestUsage(prompt_tokens=0, completion_tokens=0),
            cached=result.cached,
            logprobs=result.logprobs,
        )

    async def create_stream(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] | None = None,
        tool_choice: Any = "auto",
        json_output: bool | None = None,
        extra_create_args: Mapping[str, Any] | None = None,
        cancellation_token: Any = None,
    ) -> AsyncGenerator[CreateResult, None]:
        accumulated_parts: List[str] = []
        last_result: Optional[CreateResult] = None

        if extra_create_args is None:
            extra_create_args = {}

        async for chunk in super().create_stream(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        ):
            if isinstance(chunk.content, str):
                accumulated_parts.append(chunk.content)
            last_result = chunk
            yield chunk

        full_text = "".join(accumulated_parts)
        if not full_text or not has_dsml(full_text):
            return

        fc_dicts = parse_dsml_to_function_calls(full_text)
        if not fc_dicts:
            return

        function_calls: List[FunctionCall] = [
            FunctionCall(
                id=d["id"],
                name=d["name"],
                arguments=d["arguments"],
            )
            for d in fc_dicts
        ]

        base_usage = (
            last_result.usage
            if last_result is not None
            else RequestUsage(prompt_tokens=0, completion_tokens=0)
        )

        yield CreateResult(
            content=function_calls,
            thought=full_text,
            finish_reason="function_calls" if last_result is None else last_result.finish_reason,
            usage=base_usage,
            cached=False,
            logprobs=None,
        )
