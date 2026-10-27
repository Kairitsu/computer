"""Composer settings each coding agent runs with."""

from __future__ import annotations

from typing import Any

# Settings each agent keeps for itself under `params.agents[<profile id>]`.
AGENT_PARAM_KEYS = ("reasoning_effort", "context_window", "tool_approval_mode", "permission_mode")
# Chats from before per-agent settings keep Grok's choices at the top level of `params`.
LEGACY_PARAMS_PROFILE = "grok"


def agent_params(chat_params: Any, profile_id: str | None) -> dict[str, Any]:
    """The chat's params with one agent's own model settings in place.

    Agents never share these settings: switching a chat from Grok to CodeBuddy starts
    CodeBuddy from its own choices (or its CLI defaults), not Grok's.
    """
    params = dict(chat_params) if isinstance(chat_params, dict) else {}
    agents = params.pop("agents", None)
    own = agents.get(profile_id) if isinstance(agents, dict) and profile_id else None
    if isinstance(own, dict):
        shared = {key: value for key, value in params.items() if key not in AGENT_PARAM_KEYS}
        return {**shared, **{key: own[key] for key in AGENT_PARAM_KEYS if key in own}}
    if profile_id == LEGACY_PARAMS_PROFILE:
        return params
    return {key: value for key, value in params.items() if key not in AGENT_PARAM_KEYS}
