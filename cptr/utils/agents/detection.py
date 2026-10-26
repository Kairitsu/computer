"""Detection helpers for the Grok and AnyRouter agent profiles."""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass, asdict
from typing import Any

from cptr.utils.agents.models import (
    default_agent_profiles,
    get_raw_agent_profiles,
    model_id_for_profile,
    normalize_agent_profiles,
    parse_agent_model_id,
)

DETECTION_TTL_SECONDS = 30


@dataclass
class AgentDetection:
    status: str
    command: str | None = None
    version: str | None = None
    message: str | None = None
    models: list[str] | None = None
    # Per-model reasoning efforts / context windows the agent CLI offers, keyed by model.
    model_options: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_command(command: str) -> str | None:
    command = command.strip()
    if not command:
        return None
    expanded = os.path.expanduser(command)
    if os.path.isabs(expanded):
        return expanded if os.access(expanded, os.X_OK) else None
    return shutil.which(command)


async def _run_probe(
    argv: list[str],
    timeout: float = 3.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return 124, "probe timed out"
    except Exception as exc:  # noqa: BLE001 - surfaced as detection status.
        return 1, str(exc)

    text = (stdout or stderr).decode(errors="replace").strip()
    return proc.returncode or 0, text


async def detect_profile(profile: dict[str, Any]) -> AgentDetection:
    raw_command = str(profile.get("command") or "").strip()
    command = _resolve_command(raw_command)
    if command is None:
        return AgentDetection("not_found", None, None, "Command not found")

    code, version_text = await _run_probe([command, "--version"])
    version = version_text.splitlines()[0] if code == 0 and version_text else None

    if profile.get("agent") == "codex":
        return await _detect_anyrouter(command, version)
    if profile.get("agent") != "grok":
        return AgentDetection("error", command, version, "Unknown agent")

    models, model_options = await _probe_grok_models(command, profile)
    if not models:
        return AgentDetection(
            "auth_unknown",
            command,
            version,
            "Could not discover Grok models. Check Grok login or XAI_API_KEY.",
            [],
        )
    return AgentDetection("ready", command, version, None, models, model_options)


async def _detect_anyrouter(command: str, version: str | None) -> AgentDetection:
    from cptr.utils.anyrouter import redact, selectable_model_ids

    try:
        models = await selectable_model_ids()
    except Exception as exc:  # noqa: BLE001 - detection reports the failure in the profile.
        return AgentDetection("auth_unknown", command, version, redact(str(exc))[:300], [])
    if not models:
        return AgentDetection(
            "auth_unknown",
            command,
            version,
            "AnyRouter did not accept any models for this token.",
            [],
        )
    return AgentDetection("ready", command, version, None, models, {})


async def _probe_grok_models(
    command: str, profile: dict[str, Any]
) -> tuple[list[str] | None, dict[str, dict[str, Any]]]:
    from cptr.utils.agents.acp import AcpClient, acp_models_from_setup
    from cptr.utils.agents.grok import await_grok_model_catalog, grok_model_options

    env = os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))
    env["GROK_OAUTH2_REFERRER"] = "cptr"
    auth_method_id = "xai.api_key" if env.get("XAI_API_KEY", "").strip() else "cached_token"
    client = AcpClient(
        command=command,
        args=["agent", "stdio"],
        cwd=os.getcwd(),
        env=env,
        auth_method_id=auth_method_id,
    )
    try:
        await asyncio.wait_for(client.start(), timeout=10)
        await await_grok_model_catalog(client)
        options = grok_model_options(client.setup_result, client.initialize_result.get("_meta"))
        return acp_models_from_setup(client.setup_result) or None, options
    except Exception:
        return None, {}
    finally:
        await client.close()


async def get_agent_status(app_state=None, refresh: bool = False) -> dict[str, Any]:
    now = asyncio.get_running_loop().time()
    cache = getattr(app_state, "AGENTS", None) if app_state is not None else None
    if not refresh and isinstance(cache, dict):
        cached_at = cache.get("cached_at")
        if isinstance(cached_at, (int, float)) and now - cached_at < DETECTION_TTL_SECONDS:
            return cache["payload"]

    raw_profiles = await get_raw_agent_profiles()
    implicit_defaults = raw_profiles is None
    profiles = [] if implicit_defaults else normalize_agent_profiles(raw_profiles)
    entries = []
    candidates = profiles if not implicit_defaults else default_agent_profiles()
    for profile in candidates:
        detected = await detect_profile(profile)
        if implicit_defaults and detected.status in {"not_found", "error"}:
            continue
        mode = profile.get("mode", "auto")
        models = list(dict.fromkeys([*(detected.models or []), *(profile.get("models") or [])]))
        available = mode != "disabled" and (mode != "auto" or detected.status == "ready")
        effective_profile = dict(profile)
        effective_profile["models"] = models
        if models and effective_profile.get("default_model") not in models:
            effective_profile["default_model"] = models[0]
        entries.append(
            {
                "id": effective_profile["id"],
                "agent": effective_profile["agent"],
                "name": effective_profile["name"],
                "config": effective_profile,
                "detected": detected.to_dict(),
                "available": available,
                "implicit": implicit_defaults,
                "model_ids": [model_id_for_profile(effective_profile, model) for model in models],
            }
        )

    payload = {"profiles": entries}
    if app_state is not None:
        app_state.AGENTS = {"cached_at": now, "payload": payload}
    return payload


def invalidate_agent_detection_cache(app_state) -> None:
    if app_state is not None and hasattr(app_state, "AGENTS"):
        delattr(app_state, "AGENTS")


async def get_available_agent_model_entries(app_state=None) -> list[dict[str, Any]]:
    status = await get_agent_status(app_state)
    entries: list[dict[str, Any]] = []
    for profile in status["profiles"]:
        if not profile["available"]:
            continue
        config = profile["config"]
        model_options = profile["detected"].get("model_options") or {}
        for model in config.get("models") or []:
            model_id = model_id_for_profile(config, model)
            entry = {
                "id": model_id,
                "provider": "agent",
                "connection_id": f"agent:{config['id']}",
                "agent_id": config["agent"],
                "profile_id": config["id"],
                **model_options.get(model, {}),
            }
            # `id` routes the chat. The selector shows the model id, so a later
            # catalog entry such as grok-4.8 never displays agent:grok/.
            entry["name"] = model
            entry["profile_name"] = str(config.get("name") or config["id"])
            entries.append(entry)
    return entries


def cached_agent_model_options(app_state, model_id: str) -> dict[str, Any]:
    """Options the agent CLI offers for one model, from the last detection (never probes)."""
    parsed = parse_agent_model_id(model_id or "")
    cache = getattr(app_state, "AGENTS", None) if app_state is not None else None
    if not parsed or not isinstance(cache, dict):
        return {}
    profile_id, model = parsed
    for profile in cache["payload"]["profiles"]:
        if profile["id"] == profile_id:
            return (profile["detected"].get("model_options") or {}).get(model, {})
    return {}
