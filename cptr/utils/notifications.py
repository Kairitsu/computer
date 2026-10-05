"""User-owned chat notification targets."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urlparse

import httpx

from cptr.events import CHAT_NOTIFICATION_EVENTS, EVENT_DEFINITIONS_BY_NAME, EVENTS, Event
from cptr.models import UserStates
from cptr.socket.main import is_user_active

logger = logging.getLogger(__name__)

VALID_EVENTS = {event.name for event in CHAT_NOTIFICATION_EVENTS}
VALID_TYPES = {"webhook"}
VALID_DELIVERY = {"away", "always"}


class NotificationError(ValueError):
    pass


def _now() -> int:
    return int(time.time())


async def _state(user_id: str) -> dict:
    return dict(await UserStates.get_data(user_id) or {})


async def _save_state(user_id: str, data: dict) -> None:
    await UserStates.save_data(user_id, data)


def _targets(data: dict) -> list[dict]:
    notifications = data.setdefault("notifications", {})
    targets = notifications.setdefault("targets", [])
    if not isinstance(targets, list):
        targets = []
        notifications["targets"] = targets
    return targets


def _mask_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.hostname:
        return "****"
    path = parsed.path or ""
    suffix = path[-4:] if len(path) > 4 else path
    return f"{parsed.scheme}://{parsed.hostname}/...{suffix}"


def _public_target(target: dict) -> dict:
    public = {**target, "config": dict(target.get("config") or {})}
    if public.get("type") == "webhook":
        url = str(public["config"].pop("url", "") or "")
        public["config"]["url_masked"] = _mask_url(url) if url else ""
    return public


def _default_target_id(data: dict) -> str:
    return str((data.get("notifications") or {}).get("default_target_id") or "")


def _validate_events(events) -> list[str]:
    if events is None:
        return []
    if not isinstance(events, list):
        raise NotificationError("events must be a list")
    cleaned = []
    for event in events:
        if event not in VALID_EVENTS:
            raise NotificationError(f"unsupported notification event: {event}")
        if event not in cleaned:
            cleaned.append(event)
    return cleaned


def validate_webhook_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NotificationError("webhook URL must be http or https")

    host = parsed.hostname.lower()
    if host in {"metadata.google.internal"} or host.endswith(".internal"):
        raise NotificationError("webhook URL host is not allowed")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise NotificationError("webhook URL hostname could not be resolved") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise NotificationError("webhook URL resolves to a blocked address")
    return url


async def list_targets(user_id: str) -> list[dict]:
    data = await _state(user_id)
    visible = []
    default_id = _default_target_id(data)
    for target in _targets(data):
        if target.get("type") not in VALID_TYPES:
            continue
        public = _public_target(target)
        public["is_default"] = target.get("id") == default_id
        visible.append(public)
    return visible


def _validate_config(target_type: str, config: dict) -> dict:
    if not isinstance(config, dict):
        raise NotificationError("config must be an object")
    if target_type == "webhook":
        return {"url": validate_webhook_url(str(config.get("url") or ""))}
    raise NotificationError("unsupported notification target type")


def _validate_target(payload: dict, existing: dict | None = None) -> dict:
    merged = {**(existing or {}), **payload}
    target_id = str(merged.get("id") or "").strip()
    target_type = str(merged.get("type") or "").strip()
    delivery = str(merged.get("delivery") or "away").strip()
    if not target_id:
        raise NotificationError("target id is required")
    if target_type not in VALID_TYPES:
        raise NotificationError("target type must be webhook")
    if delivery not in VALID_DELIVERY:
        raise NotificationError("delivery must be away or always")
    config = _validate_config(target_type, dict(merged.get("config") or {}))
    now = _now()
    return {
        "id": target_id,
        "type": target_type,
        "enabled": bool(merged.get("enabled", True)),
        "events": _validate_events(merged.get("events", [])),
        "delivery": delivery,
        "config": config,
        "created_at": int(merged.get("created_at") or now),
        "updated_at": now,
    }


def _check_unique_id(targets: list[dict], new_id: str, old_id: str | None = None) -> None:
    lowered = new_id.lower()
    for target in targets:
        if target.get("id") != old_id and str(target.get("id", "")).lower() == lowered:
            raise NotificationError("notification target id already exists")


async def create_target(user_id: str, payload: dict) -> dict:
    data = await _state(user_id)
    targets = _targets(data)
    payload = dict(payload)
    if not str(payload.get("id") or "").strip():
        config = payload.get("config") or {}
        target_type = str(payload.get("type") or "target").strip() or "target"
        if target_type == "webhook":
            base = urlparse(str(config.get("url") or "")).hostname or "webhook"
        else:
            base = target_type
        base = re.sub(r"[^a-zA-Z0-9_-]+", "-", base).strip("-").lower() or "target"
        candidate = base
        suffix = 2
        while any(str(target.get("id", "")).lower() == candidate.lower() for target in targets):
            candidate = f"{base}-{suffix}"
            suffix += 1
        payload["id"] = candidate
    target = _validate_target(payload)
    _check_unique_id(targets, target["id"])
    targets.append(target)
    notifications = data.setdefault("notifications", {})
    is_default = False
    if not notifications.get("default_target_id"):
        notifications["default_target_id"] = target["id"]
        is_default = True
    await _save_state(user_id, data)
    public = _public_target(target)
    public["is_default"] = is_default
    return public


async def update_target(user_id: str, target_id: str, payload: dict) -> dict:
    data = await _state(user_id)
    targets = _targets(data)
    for idx, current in enumerate(targets):
        if current.get("id") == target_id:
            target = _validate_target(payload, existing=current)
            _check_unique_id(targets, target["id"], target_id)
            targets[idx] = target
            notifications = data.setdefault("notifications", {})
            if notifications.get("default_target_id") == target_id:
                notifications["default_target_id"] = target["id"]
            await _save_state(user_id, data)
            public = _public_target(target)
            public["is_default"] = notifications.get("default_target_id") == target["id"]
            return public
    raise NotificationError("notification target not found")


async def delete_target(user_id: str, target_id: str) -> bool:
    data = await _state(user_id)
    targets = _targets(data)
    kept = [target for target in targets if target.get("id") != target_id]
    if len(kept) == len(targets):
        return False
    data["notifications"]["targets"] = kept
    if data["notifications"].get("default_target_id") == target_id:
        data["notifications"]["default_target_id"] = kept[0]["id"] if kept else None
    await _save_state(user_id, data)
    return True


async def set_default_target(user_id: str, target_id: str) -> dict:
    data = await _state(user_id)
    for target in _targets(data):
        if target.get("id") == target_id:
            data.setdefault("notifications", {})["default_target_id"] = target_id
            await _save_state(user_id, data)
            public = _public_target(target)
            public["is_default"] = True
            return public
    raise NotificationError("notification target not found")


async def _find_target(user_id: str, target_id: str) -> dict | None:
    data = await _state(user_id)
    needle = target_id.lower()
    for target in _targets(data):
        if str(target.get("id", "")).lower() == needle:
            return target
    return None


def _webhook_payload(event: str, title: str, message: str, context: dict) -> dict:
    return {
        "event": event,
        "title": title,
        "message": message,
        "source": "computer",
        "chat_id": context.get("chat_id"),
        "workspace": context.get("workspace"),
        "created_at": _now(),
    }


def _sink_payload(url: str, payload: dict) -> dict:
    title = payload["title"]
    message = payload["message"]
    if "hooks.slack.com" in url or "chat.googleapis.com" in url:
        return {"text": f"*{title}*\n{message}" if title else message}
    if "discord.com/api/webhooks" in url:
        content = f"**{title}**\n{message}" if title else message
        return {"content": content[:1997] + "..." if len(content) > 2000 else content}
    if "webhook.office.com" in url:
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "0076D7",
            "summary": title or "Computer",
            "sections": [
                {
                    "activityTitle": title or "Computer",
                    "activitySubtitle": "Computer",
                    "facts": [{"name": "Message", "value": message}],
                    "markdown": True,
                }
            ],
        }
    return payload


async def _send_webhook(target: dict, event: str, title: str, message: str, context: dict) -> None:
    url = validate_webhook_url(str((target.get("config") or {}).get("url") or ""))
    payload = _webhook_payload(event, title, message, context)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.post(url, json=_sink_payload(url, payload))
        response.raise_for_status()


async def _deliver(target: dict, event: str, title: str, message: str, context: dict) -> None:
    if target.get("type") == "webhook":
        await _send_webhook(target, event, title, message, context)
    else:
        raise NotificationError("unsupported notification target type")


async def test_target(user_id: str, target_id: str) -> dict:
    target = await _find_target(user_id, target_id)
    if not target:
        raise NotificationError("notification target not found")
    await _deliver(
        target,
        EVENTS.NOTIFICATION_TEST.name,
        EVENTS.NOTIFICATION_TEST.label,
        "Computer can reach this target.",
        {"chat_id": None, "workspace": None, "user_id": user_id},
    )
    return {"ok": True}


def get_notification_event_catalog() -> list[dict[str, str]]:
    return [
        {
            "event": event.name,
            "label": event.label,
            "description": event.description or "",
        }
        for event in CHAT_NOTIFICATION_EVENTS
    ]


async def dispatch_notification_event(event: Event) -> None:
    if event.event not in VALID_EVENTS:
        return
    definition = EVENT_DEFINITIONS_BY_NAME[event.event]
    actor = event.actor or {}
    subject = event.subject or {}
    event_data = event.data or {}
    user_ids = set()
    if actor.get("id"):
        user_ids.add(str(actor["id"]))
    if subject.get("type") == "user" and subject.get("id"):
        user_ids.add(str(subject["id"]))
    if event_data.get("user_id"):
        user_ids.add(str(event_data["user_id"]))
    for user_id in event_data.get("user_ids") or []:
        if user_id:
            user_ids.add(str(user_id))

    message = str(event.message or event_data.get("preview") or event_data.get("message") or "")
    chat_id = event_data.get("chat_id") or (
        subject.get("id") if subject.get("type") == "chat" else None
    )
    workspace = event_data.get("workspace")

    for user_id in user_ids:
        data = await _state(user_id)
        active = is_user_active(user_id)
        for target in list(_targets(data)):
            if not target.get("enabled", True):
                continue
            if event.event not in target.get("events", []):
                continue
            if target.get("delivery", "away") == "away" and active:
                continue
            try:
                await _deliver(
                    target,
                    event.event,
                    definition.label,
                    message,
                    {"chat_id": chat_id, "workspace": workspace, "user_id": user_id},
                )
            except Exception:
                logger.exception(
                    "[notifications] failed to deliver %s to %s",
                    event.event,
                    target.get("id"),
                )


