"""Claude Code adapter for the fixed AnyRouter profile.

Each turn runs ``claude -p`` with newline-delimited stream-json on both pipes and
continues the chat's session with ``--resume``. The process comes from the
profile's own Claude Code install; ``claude_child_env`` points it at AnyRouter.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import os
import re
import tempfile
from collections import deque
from contextlib import suppress
from typing import Any, AsyncIterator

from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.events import (
    AgentContextUsage,
    AgentDone,
    AgentError,
    AgentEvent,
    AgentPermissionRequest,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolUpdate,
    AgentTurnStarted,
)
from cptr.utils.agents.prompts import session_turn_prompt_text
from cptr.utils.anyrouter import (
    ANYROUTER_CLAUDE,
    ANYROUTER_PROFILE_ID,
    CLAUDE_HOME,
    KEY_FILE,
    LONG_WINDOW,
    claude_child_env,
    ensure_claude_home,
    read_api_key,
    redact,
    turn_context_window,
    turn_reasoning_effort,
)
from cptr.utils.identity import env_for, preexec_for

logger = logging.getLogger(__name__)

_STDOUT_CHUNK = 64 * 1024
_MAX_MESSAGE = 16 * 1024 * 1024


class ClaudePrint:
    def __init__(
        self, command: str, args: list[str], cwd: str, env: dict[str, str], preexec_fn=None
    ) -> None:
        self.command = command
        self.args = args
        self.cwd = cwd
        self.env = env
        self.preexec_fn = preexec_fn
        self.proc: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=20)

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd or os.getcwd(),
            env=self.env,
            preexec_fn=self.preexec_fn,
        )
        self.reader_task = asyncio.create_task(self._reader_loop())
        self.stderr_task = asyncio.create_task(self._stderr_loop())

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            if self.proc.returncode is None:
                self.proc.kill()
                await self.proc.wait()
        for task in (self.reader_task, self.stderr_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def send(self, payload: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self.proc.stdin.drain()

    async def _reader_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        reader_error: str | None = None
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = await self.proc.stdout.read(_STDOUT_CHUNK)
                if not chunk:
                    break
                buffer += decoder.decode(chunk)
                if len(buffer) > _MAX_MESSAGE:
                    reader_error = "Claude Code emitted an oversized message"
                    break
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        await self._route_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the turn reports the transport failure.
            reader_error = redact(str(exc))
        if buffer.strip() and reader_error is None:
            await self._route_line(buffer)
        await self.events.put(
            {"type": "transport", "message": reader_error or self._exit_message()}
        )

    async def _route_line(self, line: str) -> None:
        if len(line) > _MAX_MESSAGE:
            raise RuntimeError("Claude Code emitted an oversized message")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude Code emitted an invalid message") from exc
        if not isinstance(message, dict):
            raise RuntimeError("Claude Code emitted an invalid message")
        await self.events.put(message)

    async def _stderr_loop(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            try:
                line = await self.proc.stderr.readline()
            except (asyncio.CancelledError, ValueError):
                break
            if not line:
                break
            text = redact(line.decode(errors="replace")).strip()
            if text:
                self.stderr_tail.append(text[:500])

    def _exit_message(self) -> str:
        detail = redact("\n".join(self.stderr_tail)).strip()
        if detail:
            return f"Claude Code exited: {detail}"
        if self.proc and self.proc.returncode not in {None, 0}:
            return f"Claude Code exited with code {self.proc.returncode}."
        return "Claude Code exited."


def resume_session_id(resume_state: dict[str, Any] | None, workspace: str) -> str | None:
    """Claude session to continue, if any.

    A session with a retracted turn pending (``rewind``) is not continued: the next
    turn starts a new session seeded with the chat as it now stands.
    """
    if not isinstance(resume_state, dict) or resume_state.get("agent") != "claude":
        return None
    if resume_state.get("workspace") and resume_state.get("workspace") != workspace:
        return None
    value = resume_state.get("session_id")
    if not isinstance(value, str) or not value.strip():
        return None
    rewind = resume_state.get("rewind")
    if isinstance(rewind, dict) and rewind.get("session_id") == value:
        return None
    return value.strip()


def _prompt_index(resume_state: dict[str, Any] | None, session_id: str | None) -> int:
    """Index of this turn's prompt in the session; it lets a retract mark the session."""
    if not session_id or not isinstance(resume_state, dict):
        return 0
    count = resume_state.get("prompts")
    return count if isinstance(count, int) and count > 0 else 1


def _approval_mode(chat_params: dict[str, Any]) -> str:
    if "tool_approval_mode" in chat_params:
        mode = str(chat_params.get("tool_approval_mode") or "auto")
        return mode if mode in {"ask", "auto", "full"} else "auto"
    if "auto_approve_tools" in chat_params:
        return "full" if chat_params.get("auto_approve_tools") else "auto"
    return "auto"


def _launch_args(
    *,
    model: str,
    window: int,
    approval: str,
    session_id: str | None,
    effort: str | None,
    system_file: str | None,
) -> list[str]:
    # A [1m] suffix gives Claude Code the 1M window; --autocompact sets where it compacts.
    args = [
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model",
        f"{model}[1m]" if window >= LONG_WINDOW else model,
        "--autocompact",
        str(window),
        "--thinking-display",
        "summarized",
        "--strict-mcp-config",
        "--setting-sources",
        "user",
        # Nobody can answer its questions in print mode.
        "--disallowed-tools",
        "AskUserQuestion",
    ]
    # Without --permission-prompt-tool stdio, Claude Code denies what needs approval
    # instead of sending can_use_tool to cptr.
    if approval == "full":
        args.extend(["--permission-mode", "bypassPermissions"])
    else:
        mode = "manual" if approval == "ask" else "acceptEdits"
        args.extend(["--permission-mode", mode, "--permission-prompt-tool", "stdio"])
    if session_id:
        args.extend(["--resume", session_id])
    if effort:
        args.extend(["--effort", effort])
    if system_file:
        args.extend(["--append-system-prompt-file", system_file])
    return args


def _user_message(
    prompt: str, attachments: PreparedAgentAttachments, session_id: str | None
) -> dict[str, Any]:
    if attachments.images:
        content: str | list[dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        for image in attachments.images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.mime_type,
                        "data": image.base64,
                    },
                }
            )
    else:
        content = prompt
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
        "session_id": session_id or "",
    }


def _block_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part)
    return ""


def _tool_update(
    name: str, call_id: str, raw_input: Any, status: str, output: str | None = None
) -> AgentToolUpdate:
    arguments = dict(raw_input) if isinstance(raw_input, dict) else {}
    command = arguments.get("command")
    tool_name = "agent_tool"
    if name in {"Bash", "PowerShell"} and isinstance(command, str) and command.strip():
        tool_name = "run_command"
        arguments = {"command": command.strip()}
    else:
        arguments = {**arguments, "title": name or "Claude action"}
    return AgentToolUpdate(
        call_id=call_id,
        name=tool_name,
        status=status,
        arguments=arguments,
        output=output,
    )


def _usage_payload(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    payload: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            payload[key] = value
    if not payload:
        return None
    payload["total_tokens"] = sum(payload.values())
    return payload


def _prompt_tokens(raw: Any) -> int:
    """Tokens of one API call's whole prompt, cached parts included."""
    usage = _usage_payload(raw) or {}
    return sum(
        usage.get(key, 0)
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )


def _api_error_text(status: Any, message: str) -> str:
    """Final API failure for the chat, with a plain explanation for AnyRouter's usual ones.

    AnyRouter answers a saturated upstream with 503 or with 429 "Service Unavailable".
    """
    detail = redact(message).strip() or "Claude Code failed"
    code = str(status or "")
    if not code:
        match = re.search(r"\b(401|403|429|503)\b", detail)
        code = match.group(1) if match else ""
    if code in {"429", "503"}:
        return f"AnyRouter 暂时不可用（{code}），多半是上游繁忙或限流。请稍后再试，或换一个模型。\n{detail}"
    if code in {"401", "403"}:
        return f"AnyRouter 拒绝了令牌（{code}），请检查 {KEY_FILE}。\n{detail}"
    return detail


def _control_response(request_id: Any, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": body},
    }


async def _answer_permission(
    client: ClaudePrint, request_id: Any, accepted: bool, raw_input: Any
) -> None:
    if accepted:
        updated = raw_input if isinstance(raw_input, dict) else {}
        await client.send(
            _control_response(request_id, {"behavior": "allow", "updatedInput": updated})
        )
        return
    await client.send(
        _control_response(
            request_id,
            {"behavior": "deny", "message": "The user declined this action."},
        )
    )


def _permission_arguments(tool_name: str, raw_input: Any) -> tuple[str, dict[str, Any]]:
    arguments = dict(raw_input) if isinstance(raw_input, dict) else {}
    command = arguments.get("command")
    if tool_name in {"Bash", "PowerShell"} and isinstance(command, str) and command.strip():
        return "run_command", {"command": command.strip()}
    return "agent_tool", {"title": tool_name or "Claude action", **arguments}


async def run_claude_code_agent(
    *,
    profile: dict[str, Any],
    model: str,
    workspace: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    chat_params: dict[str, Any],
    resume_state: dict[str, Any] | None,
    attachments: PreparedAgentAttachments,
    identity=None,
) -> AsyncIterator[AgentEvent]:
    if read_api_key() == "":
        yield AgentError(f"AnyRouter 令牌文件不存在：{KEY_FILE}")
        return
    command = str(profile.get("command") or ANYROUTER_CLAUDE)
    if not os.access(command, os.X_OK):
        yield AgentError(
            f"AnyRouter 用的 Claude Code 没有安装：{command}。重新运行 install.sh 即可安装。"
        )
        return
    session_id = resume_session_id(resume_state, workspace)
    prompt = session_turn_prompt_text(messages, resumed=bool(session_id))
    if not prompt and not attachments.images:
        yield AgentError("Claude Code received an empty turn")
        return

    base_env = env_for(identity, workspace) if identity and identity.is_pam else os.environ.copy()
    env = claude_child_env(base_env)
    approval = _approval_mode(chat_params)
    effort = turn_reasoning_effort(model, chat_params)
    window = turn_context_window(model, chat_params)
    prompt_index = _prompt_index(resume_state, session_id)
    cwd = workspace if workspace and os.path.isdir(workspace) else str(os.path.expanduser("~"))
    system_file = _system_file(system_prompt) if system_prompt and not session_id else None
    client = ClaudePrint(
        command,
        _launch_args(
            model=model,
            window=window,
            approval=approval,
            session_id=session_id,
            effort=effort,
            system_file=system_file,
        ),
        cwd,
        env,
        preexec_for(identity) if identity and identity.is_pam else None,
    )
    observed_session = session_id
    started = False
    streamed_text = False
    streamed_thinking = False
    seen_tools: set[str] = set()
    tool_inputs: dict[int, str] = {}
    tool_calls: dict[int, AgentToolUpdate] = {}
    # The window holds the last API call's prompt plus its reply.
    call_prompt = 0
    context_tokens = 0
    api_error: str | None = None
    try:
        await client.start()
        await client.send(_user_message(prompt, attachments, session_id))
        while True:
            event = await client.events.get()
            kind = str(event.get("type") or "")
            if kind == "transport":
                yield AgentError(redact(str(event.get("message") or "Claude Code exited.")))
                return
            if kind == "control_request":
                async for permission in _handle_control(client, event, approval):
                    yield permission
                continue
            if kind == "system":
                subtype = event.get("subtype")
                if subtype == "init":
                    found = event.get("session_id")
                    if isinstance(found, str) and found.strip():
                        observed_session = found.strip()
                    if not started and observed_session:
                        started = True
                        yield AgentTurnStarted(
                            session_id=observed_session, prompt_index=prompt_index
                        )
                elif subtype == "api_retry":
                    logger.info(
                        "AnyRouter %s retry %s/%s: %s %s",
                        model,
                        event.get("attempt"),
                        event.get("max_retries"),
                        event.get("error_status"),
                        redact(str(event.get("error") or "")),
                    )
                continue
            if kind == "stream_event":
                body = event.get("event") if isinstance(event.get("event"), dict) else {}
                if body.get("type") == "message_start":
                    message = body.get("message") if isinstance(body.get("message"), dict) else {}
                    call_prompt = _prompt_tokens(message.get("usage"))
                    context_tokens = call_prompt
                elif body.get("type") == "message_delta":
                    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                    output = usage.get("output_tokens")
                    if isinstance(output, int) and call_prompt:
                        context_tokens = call_prompt + output
                text, thinking, tool = _stream_pieces(event, tool_inputs, tool_calls)
                if text:
                    streamed_text = True
                    yield AgentTextDelta(text)
                if thinking:
                    streamed_thinking = True
                    yield AgentReasoningDelta(thinking)
                if tool and tool.call_id not in seen_tools:
                    seen_tools.add(tool.call_id)
                    yield tool
                continue
            if kind == "assistant":
                if event.get("is_api_error_message"):
                    message = event.get("error") or _block_text(
                        (event.get("message") or {}).get("content")
                        if isinstance(event.get("message"), dict)
                        else ""
                    )
                    api_error = str(message or "")
                    continue
                if not streamed_text or not streamed_thinking:
                    for piece in _assistant_pieces(event, streamed_text, streamed_thinking):
                        if isinstance(piece, AgentTextDelta):
                            streamed_text = True
                        elif isinstance(piece, AgentReasoningDelta):
                            streamed_thinking = True
                        yield piece
                for tool in _assistant_tools(event):
                    seen_tools.add(tool.call_id)
                    yield tool
                if not context_tokens:
                    message = event.get("message") if isinstance(event.get("message"), dict) else {}
                    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                    output = usage.get("output_tokens")
                    context_tokens = _prompt_tokens(usage) + (
                        output if isinstance(output, int) else 0
                    )
                continue
            if kind == "user":
                for tool in _tool_results(event):
                    yield tool
                continue
            if kind == "result":
                found = event.get("session_id")
                if isinstance(found, str) and found.strip():
                    observed_session = found.strip()
                if observed_session and not started:
                    started = True
                    yield AgentTurnStarted(session_id=observed_session, prompt_index=prompt_index)
                if event.get("is_error"):
                    message = event.get("result") or api_error or event.get("error")
                    yield AgentError(
                        _api_error_text(event.get("api_error_status"), str(message or ""))
                    )
                    return
                if not streamed_text:
                    text = event.get("result")
                    if isinstance(text, str) and text:
                        yield AgentTextDelta(text)
                usage: dict[str, Any] | None = _usage_payload(event.get("usage"))
                if context_tokens:
                    yield AgentContextUsage(tokens=context_tokens, window=window)
                    usage = {**(usage or {}), "context_tokens": context_tokens}
                if usage is not None:
                    usage["context_window"] = window
                yield AgentDone(
                    usage=usage,
                    resume_state={
                        "profile_id": profile.get("id") or ANYROUTER_PROFILE_ID,
                        "agent": "claude",
                        "session_id": observed_session,
                        "workspace": workspace,
                        "model": model,
                        "prompts": prompt_index + 1,
                    },
                )
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in the chat.
        yield AgentError(redact(str(exc)))
    finally:
        await client.close()
        if system_file:
            with suppress(OSError):
                os.remove(system_file)


def _system_file(system_prompt: str) -> str | None:
    text = system_prompt.strip()
    if not text:
        return None
    ensure_claude_home()
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="system-",
        suffix=".txt",
        dir=CLAUDE_HOME,
        delete=False,
    )
    with handle:
        handle.write(text)
    os.chmod(handle.name, 0o600)
    return handle.name


async def _handle_control(
    client: ClaudePrint, event: dict[str, Any], approval: str
) -> AsyncIterator[AgentEvent]:
    request_id = event.get("request_id")
    request = event.get("request") if isinstance(event.get("request"), dict) else {}
    subtype = str(request.get("subtype") or "")
    if subtype == "can_use_tool":
        tool_name = str(request.get("tool_name") or "Claude action")
        raw_input = request.get("input")
        name, arguments = _permission_arguments(tool_name, raw_input)
        call_id = request.get("tool_use_id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = str(request_id or tool_name)
        if approval == "full":
            accepted = True
        else:
            permission = AgentPermissionRequest(call_id=call_id, name=name, arguments=arguments)
            yield permission
            accepted = permission.approved is True
        await _answer_permission(client, request_id, accepted, raw_input)
        return
    if subtype == "initialize":
        await client.send(_control_response(request_id, {}))
        return
    await client.send(
        {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": request_id,
                "error": "unsupported",
            },
        }
    )


def _stream_pieces(
    event: dict[str, Any],
    tool_inputs: dict[int, str],
    tool_calls: dict[int, AgentToolUpdate],
) -> tuple[str | None, str | None, AgentToolUpdate | None]:
    body = event.get("event") if isinstance(event.get("event"), dict) else {}
    event_type = body.get("type")
    if event_type == "content_block_delta":
        delta = body.get("delta") if isinstance(body.get("delta"), dict) else {}
        if (
            delta.get("type") == "text_delta"
            and isinstance(delta.get("text"), str)
            and delta["text"]
        ):
            return delta["text"], None, None
        if (
            delta.get("type") == "thinking_delta"
            and isinstance(delta.get("thinking"), str)
            and delta["thinking"]
        ):
            return None, delta["thinking"], None
        if delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            index = body.get("index")
            if isinstance(index, int):
                tool_inputs[index] = tool_inputs.get(index, "") + delta["partial_json"]
        return None, None, None
    if event_type == "content_block_start":
        block = body.get("content_block") if isinstance(body.get("content_block"), dict) else {}
        if block.get("type") not in {"tool_use", "server_tool_use", "mcp_tool_use"}:
            return None, None, None
        call_id = block.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            return None, None, None
        tool = _tool_update(
            str(block.get("name") or "Claude action"),
            call_id.strip(),
            block.get("input"),
            "in_progress",
        )
        index = body.get("index")
        if isinstance(index, int):
            tool_calls[index] = tool
        return None, None, tool
    return None, None, None


def _assistant_pieces(
    event: dict[str, Any], streamed_text: bool, streamed_thinking: bool
) -> list[AgentTextDelta | AgentReasoningDelta]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content")
    pieces: list[AgentTextDelta | AgentReasoningDelta] = []
    if not isinstance(content, list):
        return pieces
    for block in content:
        if not isinstance(block, dict):
            continue
        if (
            block.get("type") == "text"
            and not streamed_text
            and isinstance(block.get("text"), str)
            and block["text"]
        ):
            pieces.append(AgentTextDelta(block["text"]))
        elif (
            block.get("type") == "thinking"
            and not streamed_thinking
            and isinstance(block.get("thinking"), str)
            and block["thinking"]
        ):
            pieces.append(AgentReasoningDelta(block["thinking"]))
    return pieces


def _assistant_tools(event: dict[str, Any]) -> list[AgentToolUpdate]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content")
    tools: list[AgentToolUpdate] = []
    if not isinstance(content, list):
        return tools
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        call_id = block.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            continue
        tools.append(
            _tool_update(
                str(block.get("name") or "Claude action"),
                call_id.strip(),
                block.get("input"),
                "in_progress",
            )
        )
    return tools


def _tool_results(event: dict[str, Any]) -> list[AgentToolUpdate]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content")
    tools: list[AgentToolUpdate] = []
    if not isinstance(content, list):
        return tools
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        call_id = block.get("tool_use_id")
        if not isinstance(call_id, str) or not call_id.strip():
            continue
        output = _block_text(block.get("content"))
        # No name: the call keeps the one it started with (run_command shows its command).
        status = "failed" if block.get("is_error") else "completed"
        tools.append(AgentToolUpdate(call_id=call_id.strip(), status=status, output=output))
    return tools
