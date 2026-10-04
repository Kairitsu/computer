"""Grok ACP adapter."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any, AsyncIterator

from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.acp import (
    ACP_PERMISSION_METHOD,
    AcpClient,
    acp_event_stream,
    acp_permission_events,
    acp_text_from_update,
    acp_tool_from_update,
)
from cptr.utils.agents.events import (
    AgentAskUser,
    AgentContextUsage,
    AgentDone,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.prompts import turn_prompt_text
from cptr.utils.identity import env_for, preexec_for


# Grok blocks its AskUserQuestion and ExitPlanMode tools on these requests to the client.
XAI_ASK_USER_QUESTION_METHODS = frozenset({"_x.ai/ask_user_question", "x.ai/ask_user_question"})
XAI_EXIT_PLAN_MODE_METHODS = frozenset({"_x.ai/exit_plan_mode", "x.ai/exit_plan_mode"})
XAI_EXTENSION_REQUESTS = XAI_ASK_USER_QUESTION_METHODS | XAI_EXIT_PLAN_MODE_METHODS
# Carries Grok's per-model-call usage and auto-compaction progress.
XAI_SESSION_NOTIFICATIONS = frozenset({"_x.ai/session_notification", "x.ai/session_notification"})

PLAN_APPROVE_LABEL = "Approve"
PLAN_KEEP_PLANNING_LABEL = "Keep planning"
PLAN_KEEP_PLANNING_FEEDBACK = (
    "The user wants to keep planning. Stay in plan mode and ask what to change."
)


def _ext_params(params: Any) -> dict[str, Any]:
    if isinstance(params, dict) and isinstance(params.get("params"), dict):
        return params["params"]
    return params if isinstance(params, dict) else {}


def _ext_call_id(params: dict[str, Any], request_id: Any) -> str:
    call_id = params.get("toolCallId")
    if isinstance(call_id, str) and call_id.strip():
        return call_id.strip()
    return f"ask-{request_id}"


def _ask_user_question(
    request_id: Any, params: dict[str, Any]
) -> tuple[AgentAskUser, list[str]] | None:
    """Map Grok questions to ask_user questions plus the question texts Grok keys answers by."""
    raw_questions = params.get("questions")
    if not isinstance(raw_questions, list):
        return None
    questions: list[dict[str, Any]] = []
    keys: list[str] = []
    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue
        text = raw.get("question")
        if not isinstance(text, str) or not text.strip():
            continue
        options = []
        for option in raw.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else None
            if isinstance(label, str) and label.strip():
                description = option.get("description")
                options.append(
                    {
                        "label": label.strip(),
                        "description": description.strip() if isinstance(description, str) else "",
                    }
                )
        header = raw.get("header")
        questions.append(
            {
                "id": f"q{len(questions) + 1}",
                "header": header.strip() if isinstance(header, str) else "",
                "question": text.strip(),
                "options": options,
            }
        )
        keys.append(text)
    if not questions:
        return None
    ask = AgentAskUser(
        call_id=_ext_call_id(params, request_id),
        questions=questions,
        auto_resolve=all(question["options"] for question in questions),
    )
    return ask, keys


def _ask_user_question_response(ask: AgentAskUser, keys: list[str]) -> dict[str, Any]:
    if ask.answers is None:
        return {"outcome": "cancelled"}
    answers: dict[str, list[str]] = {}
    annotations: dict[str, dict[str, str]] = {}
    for key, question in zip(keys, ask.questions):
        answer = ask.answers.get(question["id"])
        if not answer:
            continue
        if any(option["label"] == answer for option in question["options"]):
            answers[key] = [answer]
        else:
            # Free-text answers go in as "Other" with the text as notes, like Grok's own UI.
            answers[key] = ["Other"]
            annotations[key] = {"notes": answer}
    response: dict[str, Any] = {"outcome": "accepted", "answers": answers}
    if annotations:
        response["annotations"] = annotations
    return response


def _exit_plan_mode_question(request_id: Any, params: dict[str, Any]) -> AgentAskUser:
    plan = params.get("planContent")
    plan = plan.strip() if isinstance(plan, str) else ""
    return AgentAskUser(
        call_id=_ext_call_id(params, request_id),
        questions=[
            {
                "id": "plan",
                "header": "Plan ready",
                "question": plan or "The agent finished planning without writing a plan.",
                "options": [
                    {
                        "label": PLAN_APPROVE_LABEL,
                        "description": "Leave plan mode and start implementing.",
                    },
                    {
                        "label": PLAN_KEEP_PLANNING_LABEL,
                        "description": "Stay in plan mode and keep refining the plan.",
                    },
                ],
            }
        ],
        # Leaving plan mode unlocks edits, so never pick an answer for the user.
        auto_resolve=False,
    )


def _exit_plan_mode_response(ask: AgentAskUser) -> dict[str, Any]:
    answer = (ask.answers or {}).get("plan")
    if answer == PLAN_APPROVE_LABEL:
        return {"outcome": "approved"}
    if not answer or answer == PLAN_KEEP_PLANNING_LABEL:
        return {"outcome": "abandoned", "feedback": PLAN_KEEP_PLANNING_FEEDBACK}
    return {"outcome": "request_changes", "feedback": answer}


async def _ask_user_events(client: AcpClient, message: dict[str, Any]) -> AsyncIterator[AgentEvent]:
    """Surface one Grok question or plan approval request, then reply to Grok."""
    request_id = message.get("id")
    method = message.get("method")
    params = _ext_params(message.get("params"))
    if method in XAI_EXIT_PLAN_MODE_METHODS:
        ask = _exit_plan_mode_question(request_id, params)
        yield ask
        await client.respond(request_id, _exit_plan_mode_response(ask))
        return
    parsed = _ask_user_question(request_id, params)
    if parsed is None:
        await client.respond_error(request_id, -32602, f"Invalid params for {method}")
        return
    ask, keys = parsed
    yield ask
    await client.respond(request_id, _ask_user_question_response(ask, keys))


def _auth_method(env: dict[str, str]) -> str:
    return "xai.api_key" if env.get("XAI_API_KEY", "").strip() else "cached_token"


def _approval_mode(chat_params: dict[str, Any]) -> str:
    mode = chat_params.get("tool_approval_mode")
    if mode in {"ask", "auto", "full"}:
        return mode
    return "full" if chat_params.get("auto_approve_tools") else "auto"


def _session_meta(approval_mode: str) -> dict[str, bool]:
    """Grok's permission mode for the chat's approval mode.

    Sent on every session/new and session/load, so `[ui] permission_mode` in Grok's
    config cannot override it. `auto` is Grok's auto-review: routine calls run, and
    calls its classifier will not allow are blocked and reported to the model.
    """
    if approval_mode == "full":
        return {"yoloMode": True}
    return {"yoloMode": False, "autoMode": approval_mode == "auto"}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _context_update(update: Any) -> tuple[int | None, int | None]:
    """Context tokens and window from one Grok session notification.

    `response_completed` reports each model call: its prompt (`input_tokens` excludes
    the cached part) plus its reply is what the conversation holds after the call.
    Auto-compaction reports the size it shrank the conversation to.
    """
    if not isinstance(update, dict):
        return None, None
    kind = update.get("sessionUpdate")
    if kind == "response_completed":
        usage = update.get("usage") if isinstance(update.get("usage"), dict) else {}
        tokens = sum(
            _positive_int(usage.get(key)) or 0
            for key in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "output_tokens",
            )
        )
        return tokens or None, None
    if kind == "auto_compact_started":
        return None, _positive_int(update.get("context_window"))
    if kind == "auto_compact_completed":
        return _positive_int(update.get("tokens_after")), None
    return None, None


def _turn_usage(
    prompt_result: Any, context_tokens: int | None, context_window: int | None
) -> dict[str, Any] | None:
    """Usage for the chat message: the turn's billed totals plus the context it ended with."""
    meta = prompt_result.get("_meta") if isinstance(prompt_result, dict) else None
    totals = meta.get("usage") if isinstance(meta, dict) else None
    usage: dict[str, Any] = {}
    if isinstance(totals, dict):
        for source, target in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("totalTokens", "total_tokens"),
            ("cachedReadTokens", "cache_read_input_tokens"),
            ("reasoningTokens", "reasoning_tokens"),
        ):
            value = _positive_int(totals.get(source))
            if value:
                usage[target] = value
    if context_tokens:
        usage["context_tokens"] = context_tokens
    if context_window:
        usage["context_window"] = context_window
    return usage or None


def _model_state(setup: dict[str, Any], initialize_meta: Any) -> dict[str, Any]:
    """`models` from session setup, else the copy Grok puts in its initialize `_meta`."""
    models = setup.get("models")
    if not isinstance(models, dict) and isinstance(initialize_meta, dict):
        models = initialize_meta.get("modelState")
    return models if isinstance(models, dict) else {}


def _session_reasoning_effort(setup: dict[str, Any]) -> str | None:
    for option in setup.get("configOptions") or []:
        if isinstance(option, dict) and option.get("id") == "reasoning_effort":
            value = option.get("currentValue")
            return value if isinstance(value, str) and value else None
    return None


def grok_model_options(
    setup: dict[str, Any], initialize_meta: Any = None
) -> dict[str, dict[str, Any]]:
    """Reasoning efforts and context windows Grok offers per model, with Grok's defaults.

    The default effort is the session's (`[models].default_reasoning_effort` in Grok's
    config) when the model offers it, else the model's own default. The default context
    window is the model's catalog window.
    """
    session_effort = _session_reasoning_effort(setup)
    result: dict[str, dict[str, Any]] = {}
    for item in _model_state(setup, initialize_meta).get("availableModels") or []:
        model_id = item.get("modelId") if isinstance(item, dict) else None
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}

        efforts: list[dict[str, str]] = []
        recommended = None
        if meta.get("supportsReasoningEffort") is True:
            for raw in meta.get("reasoningEfforts") or []:
                value = raw.get("value") if isinstance(raw, dict) else None
                if not isinstance(value, str) or not value:
                    continue
                label = raw.get("label")
                description = raw.get("description")
                efforts.append(
                    {
                        "value": value,
                        "label": label if isinstance(label, str) and label else value,
                        "description": description if isinstance(description, str) else "",
                    }
                )
                if raw.get("default") is True:
                    recommended = value
        values = [effort["value"] for effort in efforts]
        default_effort = next(
            (
                value
                for value in (session_effort, meta.get("reasoningEffort"), recommended)
                if value in values
            ),
            values[0] if values else None,
        )

        windows = [
            window
            for window in (_positive_int(raw) for raw in meta.get("contextWindows") or [])
            if window
        ]
        default_window = _positive_int(meta.get("totalContextTokens"))
        if default_window and default_window not in windows:
            windows.insert(0, default_window)
        default_window = default_window or (windows[0] if windows else None)

        result[model_id.strip()] = {
            "reasoning_efforts": efforts,
            "default_reasoning_effort": default_effort,
            "context_windows": windows,
            "default_context_window": default_window,
        }
    return result


async def _apply_session_settings(
    client: AcpClient, model: str, chat_params: dict[str, Any]
) -> int | None:
    """Carry the composer's model, context window and reasoning effort into the session.

    Only values Grok offers for the model are sent, so a leftover choice made for another
    model falls back to Grok's own default. `grok agent --reasoning-effort` does not reach
    ACP sessions, which is why the effort goes through session/set_config_option.
    Returns the context window the session runs with, when Grok lists it.
    """
    setup = client.setup_result
    initialize_meta = client.initialize_result.get("_meta")
    target = model
    if model == "default":
        current = _model_state(setup, initialize_meta).get("currentModelId")
        target = current if isinstance(current, str) else ""
    options = grok_model_options(setup, initialize_meta).get(target, {})

    window = _positive_int(chat_params.get("context_window"))
    meta = {"contextWindow": window} if window in options.get("context_windows", []) else None
    if target and (model != "default" or meta):
        await client.set_model(target, meta=meta)

    effort = chat_params.get("reasoning_effort")
    if any(option["value"] == effort for option in options.get("reasoning_efforts", [])):
        await client.set_config_option("reasoning_effort", effort)
    return meta["contextWindow"] if meta else options.get("default_context_window")


async def run_grok_agent(
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
    env = env_for(identity, workspace) if identity and identity.is_pam else os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))
    env["GROK_OAUTH2_REFERRER"] = "cptr"

    session_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        session_id = resume_state["session_id"]
    approval_mode = _approval_mode(chat_params)

    client = AcpClient(
        command=str(profile["command"]),
        args=["agent", "stdio"],
        cwd=workspace,
        env=env,
        auth_method_id=_auth_method(env),
        resume_session_id=session_id,
        auto_approve_permissions=approval_mode == "full",
        extension_requests=XAI_EXTENSION_REQUESTS | {ACP_PERMISSION_METHOD},
        session_meta=_session_meta(approval_mode),
        preexec_fn=preexec_for(identity) if identity and identity.is_pam else None,
    )
    try:
        await client.start()
        context_window = await _apply_session_settings(client, model, chat_params)
        context_tokens: int | None = None

        prompt = turn_prompt_text(messages, system_prompt, resumed=bool(session_id))

        images = [
            {"data": image.base64, "mimeType": image.mime_type} for image in attachments.images
        ]
        prompt_task = asyncio.create_task(client.prompt(prompt, images=images))
        try:
            async for event in acp_event_stream(client, prompt_task):
                if "id" in event and event.get("method") == ACP_PERMISSION_METHOD:
                    async for permission_event in acp_permission_events(client, event):
                        yield permission_event
                    continue
                if "id" in event and event.get("method") in XAI_EXTENSION_REQUESTS:
                    async for ask_event in _ask_user_events(client, event):
                        yield ask_event
                    continue
                params = event.get("params") if isinstance(event.get("params"), dict) else {}
                if event.get("method") in XAI_SESSION_NOTIFICATIONS:
                    # Subagents run in their own sessions; only this one fills the chat's window.
                    if params.get("sessionId") == client.session_id:
                        tokens, window = _context_update(params.get("update"))
                        context_window = window or context_window
                        if tokens:
                            context_tokens = tokens
                            yield AgentContextUsage(tokens=tokens, window=context_window)
                    continue
                text = acp_text_from_update(params)
                if text:
                    yield AgentTextDelta(text)
                tool = acp_tool_from_update(params)
                if tool:
                    yield AgentToolUpdate(**tool)
            prompt_result = await prompt_task
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task

        yield AgentDone(
            usage=_turn_usage(prompt_result, context_tokens, context_window),
            resume_state={
                "profile_id": profile["id"],
                "session_id": client.session_id,
                "workspace": workspace,
                "model": model,
            },
        )
    except asyncio.CancelledError:
        await client.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(str(exc))
    finally:
        await client.close()
