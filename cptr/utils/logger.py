"""Loguru-based app, audit, and upstream diagnostic logging."""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

from loguru import logger as loguru_logger

from cptr.env import (
    AUDIT_LOG_LEVEL,
    AUDIT_LOG_PATH,
    AUDIT_LOG_ROTATION,
    LOG_FORMAT,
    LOG_LEVEL,
    LOG_UPSTREAM_REQUESTS,
    UPSTREAM_REQUEST_LOG_PATH,
    UPSTREAM_REQUEST_LOG_ROTATION,
)


_LEVELS = {
    "CRITICAL": "critical",
    "ERROR": "error",
    "WARNING": "warning",
    "INFO": "info",
    "DEBUG": "debug",
    "TRACE": "trace",
}
_AUDIT_LEVELS = {"METADATA", "REQUEST", "REQUEST_RESPONSE"}
_configured = False


class InterceptHandler(logging.Handler):
    """Route stdlib logging records through Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _json_stdout_sink(message) -> None:
    record = message.record
    entry: dict[str, Any] = {
        "timestamp": record["time"].isoformat(),
        "level": _LEVELS.get(record["level"].name, record["level"].name.lower()),
        "message": record["message"],
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
    }
    if record["extra"]:
        entry["extra"] = record["extra"]
    if record["exception"]:
        exc = record["exception"]
        entry["error"] = {
            "type": exc.type.__name__ if exc.type else None,
            "message": str(exc.value) if exc.value else None,
            "stacktrace": "".join(
                traceback.format_exception(exc.type, exc.value, exc.traceback)
            ).rstrip(),
        }
    sys.stdout.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _text_stdout_format(record) -> str:
    extra = ""
    visible_extra = {
        k: v
        for k, v in record["extra"].items()
        if k not in {"auditable", "upstream_diagnostic"}
    }
    if visible_extra:
        extra = " - {extra[extra_json]}"
        record["extra"]["extra_json"] = json.dumps(visible_extra, ensure_ascii=False, default=str)
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
        f"{extra}\n{{exception}}"
    )


def _audit_format(record) -> str:
    extra = record["extra"]
    entry = {
        "id": extra.get("id", ""),
        "timestamp": extra.get("timestamp") or record["time"].isoformat(),
        "audit_level": extra.get("audit_level", ""),
        "user": extra.get("user") or {},
        "method": extra.get("method", ""),
        "path": extra.get("path", ""),
        "status_code": extra.get("status_code"),
        "source_ip": extra.get("source_ip"),
        "user_agent": extra.get("user_agent"),
        "request_body": extra.get("request_body"),
        "response_body": extra.get("response_body"),
    }
    for key in ("event", "tool", "detail", "result", "session"):
        if extra.get(key) not in (None, ""):
            entry[key] = extra.get(key)
    record["extra"]["file_json"] = json.dumps(entry, ensure_ascii=False, default=str)
    return "{extra[file_json]}\n"


def _upstream_format(record) -> str:
    extra = record["extra"]
    entry = {
        "id": extra.get("id", ""),
        "timestamp": extra.get("timestamp") or record["time"].isoformat(),
        "provider": extra.get("provider", ""),
        "endpoint": extra.get("endpoint", ""),
        "model": extra.get("model", ""),
        "api_type": extra.get("api_type", ""),
        "body": extra.get("body"),
    }
    record["extra"]["file_json"] = json.dumps(entry, ensure_ascii=False, default=str)
    return "{extra[file_json]}\n"


def ensure_audit_file(path: Path | None = None) -> None:
    """Create the audit log as mode 600. Tighten a looser file, including after rotation."""
    target = path or AUDIT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        fd = os.open(target, os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        return
    try:
        if target.stat().st_mode & 0o777 != 0o600:
            os.chmod(target, 0o600)
    except OSError:
        pass


def setup_logging() -> None:
    """Configure stdout logging and optional file sinks once."""
    global _configured
    if _configured:
        return
    _configured = True

    loguru_logger.remove()

    def normal_filter(record) -> bool:
        return not record["extra"].get("auditable") and not record["extra"].get(
            "upstream_diagnostic"
        )

    if LOG_FORMAT == "json":
        loguru_logger.add(_json_stdout_sink, level=LOG_LEVEL, filter=normal_filter)
    else:
        loguru_logger.add(
            sys.stdout,
            level=LOG_LEVEL,
            format=_text_stdout_format,
            filter=normal_filter,
        )

    if AUDIT_LOG_LEVEL in _AUDIT_LEVELS:
        ensure_audit_file(AUDIT_LOG_PATH)
        loguru_logger.add(
            str(AUDIT_LOG_PATH),
            level="INFO",
            rotation=AUDIT_LOG_ROTATION,
            format=_audit_format,
            filter=lambda record: record["extra"].get("auditable") is True,
        )

    if LOG_UPSTREAM_REQUESTS:
        UPSTREAM_REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        loguru_logger.add(
            str(UPSTREAM_REQUEST_LOG_PATH),
            level="INFO",
            rotation=UPSTREAM_REQUEST_LOG_ROTATION,
            format=_upstream_format,
            filter=lambda record: record["extra"].get("upstream_diagnostic") is True,
        )

    logging.basicConfig(handlers=[InterceptHandler()], level=LOG_LEVEL, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [InterceptHandler()]
        uvicorn_logger.setLevel(LOG_LEVEL)

    loguru_logger.info("logging configured")


def audit_logger():
    return loguru_logger.bind(auditable=True)


def log_upstream_request(
    *,
    provider: str,
    endpoint: str,
    model: str,
    api_type: str,
    body: dict,
) -> None:
    """Write an exact upstream request body to the diagnostic sink when enabled."""
    if not LOG_UPSTREAM_REQUESTS:
        return
    loguru_logger.bind(
        upstream_diagnostic=True,
        id=str(uuid.uuid4()),
        provider=provider,
        endpoint=endpoint,
        model=model,
        api_type=api_type,
        body=body,
    ).info("")
