"""Run a model call on the user's GitHub Copilot seat through the official GitHub Copilot SDK.

ADK stays the agent: it owns the tool loop, approvals, guardrails and usage accounting. Each ADK model call becomes
one short, stateless Copilot session:

- ADK's system instruction replaces Copilot's system prompt, and the session gets only ADK's tools (no built-in
  Copilot tools: no shell, files or web).
- The conversation so far is sent as a transcript, the same information a stateless chat-completions API gets.
- Every tool is *terminal*: when the model calls tools the session ends and the calls are handed back to ADK as
  function calls. ADK runs them and calls the model again with the results.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

log = logging.getLogger(__name__)

TURN_TIMEOUT_S = 180.0
# Tool results can be large (rows, schemas); keep each one bounded in the transcript.
MAX_TOOL_RESULT_CHARS = 30_000
HANDED_BACK = "Accepted. The host application runs this tool and continues the conversation with its result."

_client: Any = None
_client_lock = asyncio.Lock()


class CopilotCallError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# --- request conversion (pure; unit-tested) ---------------------------------------------------------------------


def schema_to_json(schema: types.Schema | None) -> dict[str, Any]:
    """genai Schema (OpenAPI subset, upper-case types) -> JSON Schema."""
    if schema is None:
        return {"type": "object", "properties": {}}
    out: dict[str, Any] = {}
    if schema.type is not None:
        kind = schema.type.value if hasattr(schema.type, "value") else str(schema.type)
        out["type"] = kind.lower()
    if schema.description:
        out["description"] = schema.description
    if schema.enum:
        out["enum"] = list(schema.enum)
    if schema.format:
        out["format"] = schema.format
    if schema.properties:
        out["properties"] = {k: schema_to_json(v) for k, v in schema.properties.items()}
    if schema.required:
        out["required"] = list(schema.required)
    if schema.items is not None:
        out["items"] = schema_to_json(schema.items)
    if schema.any_of:
        out["anyOf"] = [schema_to_json(s) for s in schema.any_of]
    if schema.nullable and "type" in out:
        out["type"] = [out["type"], "null"]
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def function_declarations(llm_request: LlmRequest) -> list[types.FunctionDeclaration]:
    tools = (llm_request.config.tools if llm_request.config else None) or []
    return [fd for tool in tools if isinstance(tool, types.Tool) for fd in (tool.function_declarations or [])]


def declaration_schema(fd: types.FunctionDeclaration) -> dict[str, Any]:
    if fd.parameters_json_schema is not None:
        return dict(fd.parameters_json_schema)
    return schema_to_json(fd.parameters)


def system_instruction(llm_request: LlmRequest) -> str:
    si = llm_request.config.system_instruction if llm_request.config else None
    if si is None:
        return ""
    if isinstance(si, str):
        return si
    if isinstance(si, types.Content):
        return "\n".join(p.text for p in si.parts or [] if p.text)
    return str(si)


def _clip(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit:,} more characters omitted)"


def transcript(contents: list[types.Content]) -> str:
    """The conversation as plain text, oldest first, including earlier tool calls and their results."""
    blocks: list[str] = []
    for content in contents:
        who = "User" if content.role == "user" else "Assistant"
        for part in content.parts or []:
            if part.thought:
                continue
            if part.function_call is not None:
                args = json.dumps(part.function_call.args or {}, default=str)
                blocks.append(f"[Assistant called tool {part.function_call.name}] {args}")
            elif part.function_response is not None:
                result = json.dumps(part.function_response.response or {}, default=str)
                blocks.append(f"[Tool result: {part.function_response.name}]\n{_clip(result)}")
            elif part.text:
                blocks.append(f"[{who}]\n{part.text}")
            elif part.inline_data is not None:
                blocks.append(f"[{who} attached a {part.inline_data.mime_type or 'file'}]")
    return "\n\n".join(blocks)


def prompt(llm_request: LlmRequest) -> str:
    return (
        "Below is the conversation so far, oldest first, including tool calls you already made and their "
        "results. Continue as the assistant: answer the latest user message, or call tools if you need more data.\n\n"
        + transcript(list(llm_request.contents or []))
    )


def response_from(calls: list[tuple[str, Any]], text: str | None, model: str, usage: dict[str, int]) -> LlmResponse:
    parts: list[types.Part] = []
    if text and text.strip():
        parts.append(types.Part(text=text))
    for name, args in calls:
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=f"copilot-{uuid.uuid4().hex[:12]}", name=name, args=args if isinstance(args, dict) else {}
                )
            )
        )
    if not parts:
        parts.append(types.Part(text=""))
    return LlmResponse(
        content=types.Content(role="model", parts=parts),
        model_version=model,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage.get("input", 0),
            candidates_token_count=usage.get("output", 0),
            total_token_count=usage.get("input", 0) + usage.get("output", 0),
        ),
    )


# --- runtime ------------------------------------------------------------------------------------------------------


async def _shared_client() -> Any:
    """One Copilot runtime per agent process. Sessions carry each user's own token; the client has none."""
    global _client
    async with _client_lock:
        if _client is None:
            from copilot import ClientGitHubOptions, CopilotClient, RuntimeConnection

            home = os.environ.get("ODN_COPILOT_HOME") or tempfile.mkdtemp(prefix="odn-copilot-")
            client = CopilotClient(
                mode="empty",
                runtime=RuntimeConnection.for_spawned_stdio(base_directory=home, session_idle_timeout_seconds=300),
                github=ClientGitHubOptions(use_logged_in_user=False),
            )
            await client.start()
            _client = client
        return _client


async def _reset_client() -> None:
    global _client
    async with _client_lock:
        if _client is not None:
            with contextlib.suppress(Exception):
                await _client.stop()
        _client = None


async def _run(llm_request: LlmRequest, model: str, token: str) -> LlmResponse:
    from copilot import (
        SessionGitHubOptions,
        SessionInferenceOptions,
        SessionPermissionOptions,
        SessionToolOptions,
    )
    from copilot.session import SystemMessageReplaceConfig
    from copilot.tools import Tool, ToolResult

    calls: list[tuple[str, Any]] = []

    def hand_back(invocation: Any) -> Any:
        calls.append((invocation.tool_name, invocation.arguments))
        return ToolResult(text_result_for_llm=HANDED_BACK)

    declarations = function_declarations(llm_request)
    tools = [
        Tool(
            name=fd.name or "tool",
            description=fd.description or "",
            handler=hand_back,
            parameters=declaration_schema(fd),
            is_terminal=True,
            skip_permission=True,
        )
        for fd in declarations
    ]
    usage = {"input": 0, "output": 0}

    def on_event(event: Any) -> None:
        if str(getattr(event, "type", "")).endswith("ASSISTANT_USAGE"):
            data = getattr(event, "data", None)
            usage["input"] += int(getattr(data, "input_tokens", 0) or 0)
            usage["output"] += int(getattr(data, "output_tokens", 0) or 0)

    client = await _shared_client()
    session = await client.create_session(
        model=model,
        system_message=SystemMessageReplaceConfig(mode="replace", content=system_instruction(llm_request)),
        tools=SessionToolOptions(definitions=tools, available_tools=[f"custom:{t.name}" for t in tools]),
        inference=SessionInferenceOptions(allow_multiple_tool_calls=True),
        permissions=SessionPermissionOptions(on_request=_deny_everything_else),
        github=SessionGitHubOptions(token=token, telemetry_enabled=False),
    )
    try:
        session.on(on_event)
        final = await session.send_and_wait(prompt(llm_request), timeout=TURN_TIMEOUT_S)
        text = getattr(getattr(final, "data", None), "content", None) if final is not None else None
    finally:
        with contextlib.suppress(Exception):
            await session.disconnect()
        with contextlib.suppress(Exception):
            await client.delete_session(session.session_id)
    return response_from(calls, text if not calls else None, model, usage)


def _deny_everything_else(request: Any, invocation: Any) -> Any:
    # Only ADK's own tools are available (and they skip permission checks); refuse anything else outright.
    from copilot.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

    if type(request).__name__ == "PermissionRequestCustomTool":
        return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Only EasyQuery's own tools are available.")


async def generate(llm_request: LlmRequest, model: str, token: str) -> AsyncGenerator[LlmResponse, None]:
    try:
        response = await _run(llm_request, model, token)
    except TimeoutError:
        raise CopilotCallError("GitHub Copilot did not answer in time") from None
    except Exception as exc:  # noqa: BLE001 - any SDK/runtime failure becomes one clear message for the user
        message = str(exc)
        if "401" in message or "403" in message or "auth" in message.lower():
            raise CopilotCallError("GitHub rejected the sign-in or Copilot access", status_code=401) from None
        log.warning("Copilot call failed: %s", type(exc).__name__)
        # A broken runtime process is replaced on the next call.
        await _reset_client()
        raise CopilotCallError(type(exc).__name__) from None
    yield response
