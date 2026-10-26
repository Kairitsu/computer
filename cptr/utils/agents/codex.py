"""Codex 0.150 app-server adapter for the fixed AnyRouter profile.

The process speaks newline-delimited JSON-RPC. ``thread/start`` and ``turn/start``
use the Responses provider configured under this profile's CODEX_HOME.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import re
import time
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
    AgentToolOutputDelta,
    AgentToolUpdate,
    AgentTurnStarted,
)
from cptr.utils.agents.prompts import session_turn_prompt_text
from cptr.utils.anyrouter import (
    ANYROUTER_CODEX,
    ANYROUTER_HOME,
    ANYROUTER_PROFILE_ID,
    classify_failure,
    codex_child_env,
    read_api_key,
    redact,
)
from cptr.utils.identity import env_for, preexec_for

_STDOUT_CHUNK = 64 * 1024
_MAX_MESSAGE = 16 * 1024 * 1024
_ACCEPT_DECISION = {"decision": "accept"}
_DECLINE_DECISION = {"decision": "decline"}


class CodexAppServer:
    def __init__(self, command: str, cwd: str, env: dict[str, str], preexec_fn=None) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.preexec_fn = preexec_fn
        self.proc: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self.next_id = 1

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd or os.getcwd(),
            env=self.env,
            preexec_fn=self.preexec_fn,
        )
        self.reader_task = asyncio.create_task(self._reader_loop())
        self.stderr_task = asyncio.create_task(self._stderr_loop())
        await self.request(
            "initialize",
            {"clientInfo": {"name": "cptr", "title": "Computer", "version": "4.0.0"}},
        )
        await self.notify("initialized", {})

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
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending[request_id] = future
        await self._send({"id": request_id, "method": method, "params": params})
        response = await future
        if response.get("error"):
            raise RuntimeError(redact(json.dumps(response["error"], ensure_ascii=False)[:800]))
        return response

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        await self._send({"id": request_id, "result": result})

    async def respond_error(self, request_id: Any, code: int, message: str) -> None:
        await self._send({"id": request_id, "error": {"code": code, "message": message}})

    async def _send(self, payload: dict[str, Any]) -> None:
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
                    reader_error = "Codex app-server emitted an oversized message"
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
        self._fail_pending(reader_error or self._exit_message())
        await self.events.put(
            {
                "method": "transport/error",
                "params": {"message": reader_error or self._exit_message()},
            }
        )

    async def _route_line(self, line: str) -> None:
        if len(line) > _MAX_MESSAGE:
            raise RuntimeError("Codex app-server emitted an oversized message")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex app-server emitted an invalid message") from exc
        if not isinstance(message, dict):
            raise RuntimeError("Codex app-server emitted an invalid message")
        if "id" in message and "method" in message:
            await self._handle_server_request(message)
            return
        if "id" in message and ("result" in message or "error" in message):
            try:
                request_id = int(message["id"])
            except (TypeError, ValueError):
                return
            future = self.pending.pop(request_id, None)
            if future and not future.done():
                future.set_result(message)
            return
        if "method" in message:
            await self.events.put(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        if message.get("method") == "currentTime/read":
            await self.respond(message.get("id"), {"currentTimeAt": int(time.time())})
            return
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

    def _fail_pending(self, message: str) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))
        self.pending.clear()

    def _exit_message(self) -> str:
        detail = redact("\n".join(self.stderr_tail)).strip()
        if detail:
            return f"Codex app-server exited: {detail}"
        if self.proc and self.proc.returncode is not None:
            return f"Codex app-server exited with code {self.proc.returncode}."
        return "Codex app-server exited."


def _approval_mode(chat_params: dict[str, Any]) -> str:
    if "tool_approval_mode" in chat_params:
        mode = str(chat_params.get("tool_approval_mode") or "auto")
        return mode if mode in {"ask", "auto", "full"} else "auto"
    if "auto_approve_tools" in chat_params:
        return "full" if chat_params.get("auto_approve_tools") else "auto"
    return "auto"


def _approval_policy(mode: str) -> str:
    return "never" if mode == "full" else "on-request"


def _sandbox(mode: str) -> str:
    return "read-only" if mode == "ask" else "workspace-write"


def _thread_id(resume_state: dict[str, Any] | None) -> str | None:
    if not resume_state:
        return None
    value = resume_state.get("thread_id")
    return value if isinstance(value, str) and value.strip() else None


def _result_thread_id(response: dict[str, Any]) -> str | None:
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    if isinstance(thread, dict) and isinstance(thread.get("id"), str):
        return thread["id"]
    if isinstance(result.get("id"), str):
        return result["id"]
    return None


async def _open_thread(
    client: CodexAppServer,
    params: dict[str, Any],
    resume_state: dict[str, Any] | None,
) -> str | None:
    thread_id = _thread_id(resume_state)
    if thread_id:
        try:
            response = await client.request("thread/resume", {"threadId": thread_id, **params})
            opened = _result_thread_id(response) or thread_id
            return opened
        except RuntimeError:
            pass
    response = await client.request("thread/start", params)
    return _result_thread_id(response)


def _permission_name(method: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    command = params.get("command")
    if isinstance(command, str) and command.strip():
        return "run_command", {"command": command.strip()}
    reason = params.get("reason")
    if isinstance(reason, str) and reason.strip():
        return "agent_tool", {"reason": reason.strip()}
    return "agent_tool", {"action": method}


async def _answer_server_request(
    client: CodexAppServer, message: dict[str, Any], accepted: bool
) -> None:
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    request_id = message.get("id")
    if method == "item/permissions/requestApproval":
        granted = params.get("permissions") if accepted and isinstance(params.get("permissions"), dict) else {}
        await client.respond(request_id, {"permissions": granted, "scope": "turn"})
        return
    if method in {"execCommandApproval", "applyPatchApproval"}:
        if accepted:
            await client.respond(request_id, {"decision": "approved"})
        else:
            await client.respond(
                request_id,
                {"decision": {"denied": {"rejection": "The user declined this action."}}},
            )
        return
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        await client.respond(request_id, _ACCEPT_DECISION if accepted else _DECLINE_DECISION)
        return
    await client.respond_error(request_id, -32601, f"Method not found: {method}")


def _tool_from_item(method: str, params: dict[str, Any]) -> AgentToolUpdate | None:
    if method not in {"item/started", "item/completed"}:
        return None
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    normalized = re.sub(r"[^a-z0-9]+", " ", item_type.lower()).strip()
    if not normalized or any(
        word in normalized for word in ("user", "agent message", "reasoning", "plan")
    ):
        return None
    if not any(
        word in normalized
        for word in ("command", "file change", "patch", "mcp", "tool", "web search", "collab")
    ):
        return None
    call_id = params.get("itemId") or item.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    command = item.get("command")
    title = item.get("title") or item.get("name") or item_type
    arguments: dict[str, Any]
    name = "agent_tool"
    if isinstance(command, str) and command.strip():
        name = "run_command"
        arguments = {"command": command.strip()}
    else:
        arguments = {"title": str(title)}
    output = item.get("aggregatedOutput") or item.get("output")
    return AgentToolUpdate(
        call_id=call_id.strip(),
        name=name,
        status="completed" if method == "item/completed" else "in_progress",
        arguments=arguments,
        output=output if isinstance(output, str) else None,
    )


def _output_delta(method: str, params: dict[str, Any]) -> AgentToolOutputDelta | None:
    if method not in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
        return None
    call_id = params.get("itemId")
    delta = params.get("delta")
    if not isinstance(call_id, str) or not isinstance(delta, str) or not delta:
        return None
    return AgentToolOutputDelta(
        call_id=call_id,
        delta=delta,
        stream_kind="command_output" if "command" in method else "file_change_output",
    )


async def run_codex_agent(
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
        yield AgentError("AnyRouter key file is missing")
        return
    base_env = env_for(identity, workspace) if identity and identity.is_pam else os.environ.copy()
    env = codex_child_env(base_env)
    command = str(profile.get("command") or ANYROUTER_CODEX)
    client = CodexAppServer(
        command,
        workspace,
        env,
        preexec_for(identity) if identity and identity.is_pam else None,
    )
    approval = _approval_mode(chat_params)
    thread_id: str | None = None
    turn_id: str | None = None
    streamed: dict[str, int] = {}
    last_usage: dict[str, Any] | None = None
    try:
        await client.start()
        thread_params: dict[str, Any] = {
            "cwd": workspace or str(ANYROUTER_HOME),
            "approvalPolicy": _approval_policy(approval),
            "sandbox": _sandbox(approval),
            "model": model,
            "modelProvider": ANYROUTER_PROFILE_ID,
        }
        if system_prompt and not _thread_id(resume_state):
            thread_params["developerInstructions"] = system_prompt
        thread_id = await _open_thread(client, thread_params, resume_state)
        if not thread_id:
            yield AgentError("Codex did not return a thread id")
            return
        prompt = session_turn_prompt_text(messages, resumed=bool(_thread_id(resume_state)))
        turn_input: list[dict[str, Any]] = []
        if prompt:
            turn_input.append({"type": "text", "text": prompt})
        for image in attachments.images:
            turn_input.append(
                {"type": "image", "url": f"data:{image.mime_type};base64,{image.base64}"}
            )
        if not turn_input:
            yield AgentError("Codex received an empty turn")
            return
        yield AgentTurnStarted(session_id=thread_id)
        started = await client.request("turn/start", {"threadId": thread_id, "input": turn_input})
        turn = started.get("result", {}).get("turn") if isinstance(started.get("result"), dict) else None
        if isinstance(turn, dict) and isinstance(turn.get("id"), str):
            turn_id = turn["id"]

        while True:
            event = await client.events.get()
            method = str(event.get("method") or "")
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            if "id" in event and method:
                if approval == "ask":
                    name, arguments = _permission_name(method, params)
                    call_id = str(params.get("itemId") or event.get("id"))
                    request = AgentPermissionRequest(
                        call_id=call_id, name=name, arguments=arguments
                    )
                    yield request
                    accepted = request.approved is True
                else:
                    accepted = True
                await _answer_server_request(client, event, accepted)
                continue
            event_turn = params.get("turnId")
            if event_turn and turn_id and event_turn != turn_id and method != "turn/completed":
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                item_id = str(params.get("itemId") or "")
                if isinstance(delta, str) and delta:
                    streamed[item_id] = streamed.get(item_id, 0) + len(delta)
                    yield AgentTextDelta(delta)
            elif method in {"item/reasoning/textDelta", "item/reasoning/summaryTextDelta"}:
                delta = params.get("delta")
                if isinstance(delta, str) and delta:
                    yield AgentReasoningDelta(delta)
            elif method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    item_id = str(item.get("id") or params.get("itemId") or "")
                    text = item["text"]
                    already = streamed.get(item_id, 0)
                    if len(text) > already:
                        yield AgentTextDelta(text[already:])
                        streamed[item_id] = len(text)
                else:
                    tool = _tool_from_item(method, params)
                    if tool:
                        yield tool
            elif method == "item/started":
                tool = _tool_from_item(method, params)
                if tool:
                    yield tool
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
                total = usage.get("total") if isinstance(usage.get("total"), dict) else {}
                tokens = total.get("totalTokens")
                window = usage.get("modelContextWindow")
                if isinstance(total, dict):
                    last_usage = total
                if isinstance(tokens, int):
                    yield AgentContextUsage(
                        tokens=tokens, window=window if isinstance(window, int) else None
                    )
            else:
                output = _output_delta(method, params)
                if output:
                    yield output
            if method == "turn/completed":
                turn_body = params.get("turn") if isinstance(params.get("turn"), dict) else params
                status = str(turn_body.get("status") or "")
                if status == "failed":
                    error = turn_body.get("error")
                    message = error.get("message") if isinstance(error, dict) else error
                    yield AgentError(redact(str(message or "Codex turn failed")))
                    return
                yield AgentDone(
                    usage=_usage_payload(last_usage),
                    resume_state={
                        "profile_id": profile.get("id") or ANYROUTER_PROFILE_ID,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "workspace": workspace,
                        "model": model,
                    },
                )
                return
            if method == "error":
                yield AgentError(redact(str(params.get("message") or params or "Codex error")))
                return
            if method in {"process/exited", "transport/error"}:
                yield AgentError(redact(str(params.get("message") or "Codex app-server exited.")))
                return
    except asyncio.CancelledError:
        if thread_id and turn_id:
            with suppress(Exception):
                await client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in the chat.
        yield AgentError(redact(str(exc)))
    finally:
        await client.close()


def _usage_payload(total: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(total, dict):
        return None
    payload: dict[str, int] = {}
    for source, target in (
        ("inputTokens", "input_tokens"),
        ("outputTokens", "output_tokens"),
        ("totalTokens", "total_tokens"),
    ):
        value = total.get(source)
        if isinstance(value, int):
            payload[target] = value
    return payload or None


async def probe_models(models: list[str]) -> dict[str, dict[str, str]]:
    """Ask Codex, once per model, whether AnyRouter accepts that model."""
    from cptr.utils.anyrouter import load_probe, save_probe

    results: dict[str, dict[str, str]] = {}
    stored = load_probe()
    for model in models:
        results[model] = await _probe_model(model)
        stored[model] = {**results[model], "probed_at": time.time()}
        save_probe(stored)
    return results


async def _probe_model(model: str) -> dict[str, str]:
    if read_api_key() == "":
        return {"status": "error", "detail": "AnyRouter key file is missing"}
    env = codex_child_env(os.environ.copy())
    client = CodexAppServer(ANYROUTER_CODEX, "/tmp", env)
    try:
        await asyncio.wait_for(client.start(), timeout=20)
        response = await asyncio.wait_for(
            client.request(
                "thread/start",
                {
                    "cwd": "/tmp",
                    "model": model,
                    "modelProvider": ANYROUTER_PROFILE_ID,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": True,
                    "developerInstructions": "Reply with exactly ok. Do not use tools.",
                },
            ),
            timeout=30,
        )
        thread_id = _result_thread_id(response)
        if not thread_id:
            return {"status": "error", "detail": "Codex did not return a thread id"}
        await asyncio.wait_for(
            client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": "Reply with exactly the word ok."}],
                },
            ),
            timeout=30,
        )
        deadline = asyncio.get_running_loop().time() + 50
        turn_started = False
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if turn_started:
                    return {
                        "status": "accepted",
                        "detail": "upstream did not answer before the probe timeout",
                    }
                return {"status": "error", "detail": "timed out before the model responded"}
            event = await asyncio.wait_for(client.events.get(), timeout=remaining)
            method = str(event.get("method") or "")
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            if "id" in event and method:
                await _answer_server_request(client, event, True)
                return {"status": "accepted", "detail": "model requested approval"}
            if method == "turn/started":
                turn_started = True
                continue
            if method in {
                "item/agentMessage/delta",
                "item/reasoning/textDelta",
                "item/reasoning/summaryTextDelta",
            }:
                snippet = params.get("delta")
                detail = method
                if isinstance(snippet, str) and snippet.strip():
                    detail = redact(snippet.strip())[:120]
                return {"status": "accepted", "detail": detail}
            if method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    return {"status": "accepted", "detail": redact(item["text"])[:120] or "agent message"}
            if method == "turn/completed":
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                if str(turn.get("status") or "") == "failed":
                    error = turn.get("error")
                    message = error.get("message") if isinstance(error, dict) else str(error or "")
                    return {
                        "status": classify_failure(str(message)),
                        "detail": redact(str(message))[:300],
                    }
                text = ""
                for item in turn.get("items") or []:
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        text = str(item.get("text") or "")
                        break
                return {"status": "accepted", "detail": redact(text)[:120] or "completed"}
            if method == "error":
                message = str(params.get("message") or params)
                return {"status": classify_failure(message), "detail": redact(message)[:300]}
            if method == "transport/error":
                message = str(params.get("message") or "transport error")
                return {"status": "error", "detail": redact(message)[:300]}
    except Exception as exc:  # noqa: BLE001 - one model must not hide the others.
        message = redact(str(exc))
        return {"status": classify_failure(message), "detail": message[:300]}
    finally:
        await client.close()
