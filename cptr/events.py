"""Application event definitions and internal event bus."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any

logger = logging.getLogger(__name__)

MAX_STRING_LENGTH = 1000


@dataclass(frozen=True)
class EventDefinition:
    name: str
    description: str | None = None
    message: str | None = None

    @property
    def label(self) -> str:
        return self.message or self.name.replace(".", " ").replace("_", " ").title()


class EventDefinitions:
    CHAT_READ = EventDefinition(
        "chat.read",
        "The user explicitly marked a chat as read.",
        "Chat read",
    )
    CHAT_USER_MESSAGE = EventDefinition(
        "chat.user_message",
        "A user message was added to a chat.",
        "User message",
    )
    CHAT_FINISHED = EventDefinition(
        "chat.finished",
        "A chat run finished successfully.",
        "Chat finished",
    )
    CHAT_FAILED = EventDefinition(
        "chat.failed",
        "A chat run failed.",
        "Chat failed",
    )


EVENTS = EventDefinitions()
EVENT_DEFINITIONS = tuple(
    value for value in vars(EventDefinitions).values() if isinstance(value, EventDefinition)
)
EVENT_CATALOG = tuple(definition.name for definition in EVENT_DEFINITIONS)
EVENT_CATALOG_SET = set(EVENT_CATALOG)

SENSITIVE_KEYS = {
    "password",
    "hashed_password",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "secret",
    "key",
    "authorization",
    "cookie",
    "webhook_token",
}
SAFE_ACTOR_FIELDS = ("id", "name", "email", "role", "created_at", "updated_at")


@dataclass
class Event:
    schema: str
    id: str
    event: str
    resource: str
    operation: str
    created_at: int
    source: str
    actor: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    message: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _sanitize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump()

    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized in SENSITIVE_KEYS
                or normalized.endswith("_token")
                or normalized.endswith("_secret")
                or normalized.endswith("_api_key")
                or normalized.endswith("_key")
            ):
                continue
            sanitized[key] = _sanitize(item)
        return sanitized

    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]

    if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
        return f"{value[:MAX_STRING_LENGTH]}..."

    return value


def build_event(
    event: EventDefinition | str,
    *,
    actor: Any | None = None,
    subject_id: Any | None = None,
    subject_type: str | None = None,
    source: str = "api",
    data: dict | None = None,
    message: str | None = None,
) -> Event:
    event_name_value = event.name if isinstance(event, EventDefinition) else str(event)
    if event_name_value not in EVENT_CATALOG_SET:
        raise ValueError(f"Unknown event: {event_name_value}")

    parts = event_name_value.split(".")
    resource = ".".join(parts[:-1])
    subject = (
        {"type": subject_type or resource, "id": subject_id}
        if subject_id is not None or subject_type is not None
        else None
    )
    actor = _sanitize(actor)
    actor_payload = None
    if actor:
        get = actor.get if isinstance(actor, dict) else lambda key: getattr(actor, key, None)
        actor_payload = {field: get(field) for field in SAFE_ACTOR_FIELDS if get(field) is not None}
        if actor_payload:
            actor_payload["type"] = get("type") or "user"

    try:
        schema = package_version("cptr")
    except PackageNotFoundError:
        schema = "dev"

    return Event(
        schema=schema,
        id=str(uuid.uuid4()),
        event=event_name_value,
        resource=resource,
        operation=parts[-1],
        created_at=int(time.time()),
        source=source,
        actor=actor_payload,
        subject=_sanitize(subject) if subject else None,
        data=_sanitize(data or {}),
        message=message,
    )


# Nothing consumes events at the moment; sinks expose `async handle_event(event)`.
EVENT_SINKS: list[Any] = []


async def publish_event(
    event: EventDefinition | str,
    *,
    actor: Any | None = None,
    subject_id: Any | None = None,
    subject_type: str | None = None,
    source: str = "api",
    data: dict | None = None,
    message: str | None = None,
) -> Event:
    event_payload = build_event(
        event,
        actor=actor,
        subject_id=subject_id,
        subject_type=subject_type,
        source=source,
        data=data,
        message=message,
    )

    for sink in EVENT_SINKS:
        try:
            await sink.handle_event(event_payload)
        except Exception:
            logger.exception("Event sink failed for %s", event_payload.event)

    return event_payload
