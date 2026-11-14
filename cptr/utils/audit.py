"""File-based HTTP audit logging middleware."""

from __future__ import annotations

import json
import re
import uuid
from enum import Enum
from typing import Any, Awaitable, Callable

from starlette.requests import Request

from cptr.env import AUDIT_LOG_LEVEL, AUDIT_MAX_BODY_SIZE
from cptr.utils.logger import audit_logger


class AuditLevel(str, Enum):
    NONE = "NONE"
    METADATA = "METADATA"
    REQUEST = "REQUEST"
    REQUEST_RESPONSE = "REQUEST_RESPONSE"


_SENSITIVE_KEYS = {
    "password",
    "current_password",
    "new_password",
    "grant",
    "code",
    "totp_secret",
    "recovery_codes",
    "otpauth_uri",
    "api_key",
    "token",
    "authorization",
    "cookie",
    "secret",
    "device_code",
    "user_code",
    "verification_uri",
    "verification_uri_complete",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "********" if k.lower() in _SENSITIVE_KEYS else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _decode_body(body: bytearray) -> str | None:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    try:
        return json.dumps(_redact(json.loads(text)), ensure_ascii=False, default=str)
    except Exception:
        for key in _SENSITIVE_KEYS:
            text = re.sub(
                rf'("{re.escape(key)}"\s*:\s*")[^"]*(")',
                r"\1********\2",
                text,
                flags=re.IGNORECASE,
            )
        return text


class AuditContext:
    def __init__(self, max_body_size: int) -> None:
        self.request_body = bytearray()
        self.response_body = bytearray()
        self.max_body_size = max_body_size
        self.status_code: int | None = None

    def add_request(self, chunk: bytes) -> None:
        self._add(self.request_body, chunk)

    def add_response(self, chunk: bytes) -> None:
        self._add(self.response_body, chunk)

    def _add(self, target: bytearray, chunk: bytes) -> None:
        if not chunk or len(target) >= self.max_body_size:
            return
        target.extend(chunk[: self.max_body_size - len(target)])


class AuditLoggingMiddleware:
    """Capture safe HTTP audit entries to the audit Loguru sink."""

    AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(
        self,
        app,
        *,
        audit_level: AuditLevel,
        excluded_paths: list[str] | None = None,
        max_body_size: int = AUDIT_MAX_BODY_SIZE,
    ) -> None:
        self.app = app
        self.audit_level = audit_level
        self.excluded_paths = excluded_paths or []
        self.max_body_size = max_body_size

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if self._should_skip(request):
            await self.app(scope, receive, send)
            return

        context = AuditContext(self.max_body_size)

        async def receive_wrapper() -> dict[str, Any]:
            message = await receive()
            if self.audit_level in (AuditLevel.REQUEST, AuditLevel.REQUEST_RESPONSE):
                if message.get("type") == "http.request":
                    context.add_request(message.get("body", b""))
            return message

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                context.status_code = message.get("status")
            elif (
                self.audit_level == AuditLevel.REQUEST_RESPONSE
                and message.get("type") == "http.response.body"
            ):
                context.add_response(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            self._write_entry(request, context)

    def _should_skip(self, request: Request) -> bool:
        if AUDIT_LOG_LEVEL == "NONE" or request.method not in self.AUDITED_METHODS:
            return True
        path = request.url.path
        return any(path.startswith(excluded) for excluded in self.excluded_paths)

    def _write_entry(self, request: Request, context: AuditContext) -> None:
        auth = getattr(request.state, "auth", None)
        user = {}
        if auth is not None:
            user = {
                "id": getattr(auth, "user_id", None),
                "username": getattr(auth, "username", None),
                "role": getattr(auth, "role", None),
            }

        audit_logger().bind(
            id=str(uuid.uuid4()),
            audit_level=self.audit_level.value,
            user=user,
            method=request.method,
            path=request.url.path,
            status_code=context.status_code,
            source_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_body=_decode_body(context.request_body)
            if self.audit_level in (AuditLevel.REQUEST, AuditLevel.REQUEST_RESPONSE)
            else None,
            response_body=_decode_body(context.response_body)
            if self.audit_level == AuditLevel.REQUEST_RESPONSE
            else None,
        ).info("")


def get_audit_level() -> AuditLevel:
    try:
        return AuditLevel(AUDIT_LOG_LEVEL)
    except ValueError:
        return AuditLevel.NONE


# Tool and file-read events are written even for GET, and even on paths the
# HTTP middleware skips. They still respect an explicit NONE level.
_READ_TOKENS = {"read", "read_file", "view", "view_file", "cat"}
_WRITE_TOKENS = {"write", "edit", "delete", "remove", "create", "move", "strreplace", "searchreplace"}
_PATH_KEYS = ("path", "file_path", "filepath", "target_file", "target_path")
_DETAIL_LIMIT = 200
_seen_tools: dict[tuple[str, str], None] = {}


def _clip(text: str) -> str:
    text = _redact_text(text.strip())
    if len(text) <= _DETAIL_LIMIT:
        return text
    return text[:_DETAIL_LIMIT]


def _redact_text(text: str) -> str:
    return re.sub(
        r"(?i)(password|token|secret|api_key|authorization|cookie)([\"\s:=]+)[^\s&,\"']+",
        r"\1\2********",
        text,
    )


def _tokens(text: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", text.lower()) if part}


def _find_path(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in _PATH_KEYS:
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        nested = value.get("input")
        if nested is not value:
            return _find_path(nested)
    return None


def agent_tool_subject(name: str | None, arguments: Any) -> dict[str, str] | None:
    """Command or file-read path worth auditing. Never includes command output."""
    args = arguments if isinstance(arguments, dict) else {}
    command = args.get("command")
    if name == "run_command" or (isinstance(command, str) and command.strip()):
        if isinstance(command, str) and command.strip():
            return {"tool": "run_command", "detail": _clip(command)}
        if name == "run_command":
            return {"tool": "run_command", "detail": ""}
    path = _find_path(args)
    labels = _tokens(f"{name or ''} {args.get('title') or ''} {args.get('kind') or ''}")
    if not path or not (labels & _READ_TOKENS) or (labels & _WRITE_TOKENS):
        return None
    tool = name if name and name != "agent_tool" else "read_file"
    return {"tool": tool, "detail": _clip(path)}


def user_fields(request: Request | None, user_id: str | None = None) -> dict[str, Any]:
    auth = getattr(getattr(request, "state", None), "auth", None)
    return {
        "id": getattr(auth, "user_id", None) or user_id,
        "username": getattr(auth, "username", None),
        "role": getattr(auth, "role", None),
    }


def _remember(call_id: str, status: str) -> bool:
    phase = "result" if status in {"completed", "failed", "rejected"} else "start"
    key = (call_id, phase)
    if key in _seen_tools:
        return False
    _seen_tools[key] = None
    if len(_seen_tools) > 4000:
        for old in list(_seen_tools)[:2000]:
            _seen_tools.pop(old, None)
    return True


def clear_tool_audit_seen() -> None:
    _seen_tools.clear()


def _emit(entry: dict[str, Any]) -> None:
    if get_audit_level() == AuditLevel.NONE:
        return
    from cptr.utils.logger import ensure_audit_file

    try:
        ensure_audit_file()
    except OSError:
        pass
    audit_logger().bind(
        id=str(uuid.uuid4()),
        audit_level=get_audit_level().value,
        user=entry.get("user") or {},
        method=entry.get("method") or "",
        path=entry.get("path") or "",
        status_code=entry.get("status_code"),
        source_ip=entry.get("source_ip"),
        user_agent=entry.get("user_agent"),
        request_body=None,
        response_body=None,
        event=entry.get("event"),
        tool=entry.get("tool"),
        detail=entry.get("detail"),
        result=entry.get("result"),
        session=entry.get("session"),
    ).info("")


def record_agent_tool(
    *,
    user: dict[str, Any],
    session: str,
    call_id: str,
    name: str | None,
    arguments: Any,
    status: str | None,
) -> dict[str, Any] | None:
    """Log the first sight of a command or file read, and its later result."""
    if get_audit_level() == AuditLevel.NONE:
        return None
    subject = agent_tool_subject(name, arguments)
    if subject is None or not call_id or not _remember(call_id, status or ""):
        return None
    entry = {
        "event": "agent_tool",
        "user": user,
        "session": session,
        "tool": subject["tool"],
        "detail": subject["detail"],
        "result": status or "",
    }
    _emit(entry)
    return entry


def record_tool_decision(*, user: dict[str, Any], session: str, call: dict[str, Any], action: str) -> None:
    """Log allow or deny. The command comes from the saved tool call, not the request body."""
    if get_audit_level() == AuditLevel.NONE:
        return
    arguments = call.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    subject = agent_tool_subject(call.get("name"), arguments) or {}
    _emit(
        {
            "event": "tool_decision",
            "user": user,
            "session": session,
            "tool": subject.get("tool") or call.get("name") or "agent_tool",
            "detail": subject.get("detail") or "",
            "result": action,
        }
    )


def record_workspace_read(request: Request, path: str, result: str) -> None:
    """Log a workspace file read. The path is kept. The contents are not."""
    if get_audit_level() == AuditLevel.NONE:
        return
    _emit(
        {
            "event": "workspace_read",
            "user": user_fields(request),
            "method": "GET",
            "path": request.url.path,
            "tool": "read",
            "detail": _clip(path or ""),
            "result": result,
            "source_ip": request.client.host if request.client else None,
        }
    )
