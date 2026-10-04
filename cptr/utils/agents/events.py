"""Normalized event types emitted by coding agent adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentTextDelta:
    text: str


@dataclass
class AgentReasoningDelta:
    text: str


@dataclass
class AgentToolUpdate:
    call_id: str
    status: str
    name: str | None = None
    arguments: dict[str, Any] | None = None
    output: str | None = None


@dataclass
class AgentToolOutputDelta:
    call_id: str
    delta: str
    stream_kind: str = "tool_output"
    replace: bool = False


@dataclass
class AgentAskUser:
    """The agent is blocked until the user answers ``questions``.

    The consumer sets ``answers`` (question id → answer) before pulling the next
    event; ``None`` means nobody answered.
    """

    call_id: str
    questions: list[dict[str, Any]]
    auto_resolve: bool = True
    answers: dict[str, str] | None = None


@dataclass
class AgentDone:
    usage: dict[str, Any] | None = None
    resume_state: dict[str, Any] | None = None


@dataclass
class AgentError:
    message: str


AgentEvent = (
    AgentTextDelta
    | AgentReasoningDelta
    | AgentToolUpdate
    | AgentToolOutputDelta
    | AgentAskUser
    | AgentDone
    | AgentError
)
