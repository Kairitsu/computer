"""Resolve selected model ids to agent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException

from cptr.utils.agents.detection import get_agent_status
from cptr.utils.agents.models import parse_agent_model_id


@dataclass(frozen=True)
class AgentModelTarget:
    kind: Literal["agent"]
    profile_id: str
    agent: Literal["codex", "claude_code", "cursor", "grok", "opencode", "cline", "gemini", "pi"]
    model: str
    full_model_id: str
    config: dict[str, Any]


async def resolve_agent_model_target(model_id: str, app_state=None) -> AgentModelTarget:
    parsed = parse_agent_model_id(model_id)
    if parsed is None:
        raise HTTPException(400, f"not an agent model: {model_id}")

    profile_id, model = parsed
    status = await get_agent_status(app_state)
    entry = next((p for p in status["profiles"] if p["id"] == profile_id), None)
    if entry is None:
        raise HTTPException(400, f"agent profile not found: {profile_id}")

    profile = entry["config"]
    if not entry["available"]:
        detection = entry.get("detected") or {}
        raise HTTPException(
            400,
            f"agent profile {profile_id} is not available: "
            f"{detection.get('message') or detection.get('status')}",
        )

    return AgentModelTarget(
        kind="agent",
        profile_id=profile["id"],
        agent=profile["agent"],
        model=model,
        full_model_id=model_id,
        config=profile,
    )


async def resolve_model_target(model_id: str, app_state=None) -> AgentModelTarget:
    return await resolve_agent_model_target(model_id, app_state)
