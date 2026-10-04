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


def _auto_approve(chat_params: dict[str, Any]) -> bool:
    if chat_params.get("tool_approval_mode") == "full":
        return True
    return bool(chat_params.get("auto_approve_tools"))


GROK_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


def _agent_args(chat_params: dict[str, Any]) -> list[str]:
    """`grok agent [--reasoning-effort <effort>] stdio` for the composer's effort."""
    effort = chat_params.get("reasoning_effort")
    if effort in GROK_REASONING_EFFORTS:
        return ["agent", "--reasoning-effort", effort, "stdio"]
    return ["agent", "stdio"]


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

    client = AcpClient(
        command=str(profile["command"]),
        args=_agent_args(chat_params),
        cwd=workspace,
        env=env,
        auth_method_id=_auth_method(env),
        resume_session_id=session_id,
        auto_approve_permissions=_auto_approve(chat_params),
        extension_requests=XAI_EXTENSION_REQUESTS | {ACP_PERMISSION_METHOD},
        preexec_fn=preexec_for(identity) if identity and identity.is_pam else None,
    )
    try:
        await client.start()
        if model != "default":
            await client.set_model(model)

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
                text = acp_text_from_update(params)
                if text:
                    yield AgentTextDelta(text)
                tool = acp_tool_from_update(params)
                if tool:
                    yield AgentToolUpdate(**tool)
            await prompt_task
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task

        yield AgentDone(
            resume_state={
                "profile_id": profile["id"],
                "session_id": client.session_id,
                "workspace": workspace,
                "model": model,
            }
        )
    except asyncio.CancelledError:
        await client.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(str(exc))
    finally:
        await client.close()
