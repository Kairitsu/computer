"""Composer settings each coding agent runs with."""

from __future__ import annotations

from typing import Any

# Settings each agent keeps for itself under `params.agents[<profile id>]`.
AGENT_PARAM_KEYS = ("reasoning_effort", "context_window", "tool_approval_mode", "permission_mode")
# Chats from before per-agent settings keep Grok's choices at the top level of `params`.
LEGACY_PARAMS_PROFILE = "grok"
# Higher is more thinking. "enabled" is a model's own unmarked default, not a strength.
_EFFORT_RANK = {
    "disabled": -2,
    "none": -2,
    "enabled": -1,
    "minimal": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "max": 5,
}


def highest_reasoning_effort(efforts: list[Any]) -> str | None:
    """Strongest listed effort. Unknown names stay below the known strengths."""
    best: str | None = None
    best_rank: float | None = None
    for index, effort in enumerate(efforts):
        value = effort.get("value") if isinstance(effort, dict) else None
        if not isinstance(value, str) or not value:
            continue
        rank = float(_EFFORT_RANK[value]) if value in _EFFORT_RANK else -0.5 + index * 0.001
        if best_rank is None or rank > best_rank:
            best = value
            best_rank = rank
    return best


def longest_context_window(windows: list[Any]) -> int | None:
    """Largest positive window in the list."""
    best: int | None = None
    for window in windows:
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            continue
        if best is None or window > best:
            best = window
    return best


def agent_params(chat_params: Any, profile_id: str | None) -> dict[str, Any]:
    """The chat's params with one agent's own model settings in place.

    Agents never share these settings: switching a chat from Grok to CodeBuddy starts
    CodeBuddy from its own choices, not Grok's. A missing effort or window is filled
    by the caller with that model's strongest effort and longest window.
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
