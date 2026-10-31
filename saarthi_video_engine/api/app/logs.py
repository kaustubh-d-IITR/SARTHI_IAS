"""Structured JSON logs with request, user, session, job, render, and plan-version ids.

Values that look like credentials are masked before a line is written. File contents
and request bodies are never logged.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

_CONTEXT: contextvars.ContextVar[dict] = contextvars.ContextVar("studio_log_context", default={})
_SECRET = re.compile(r"(?i)(api[_-]?key|secret|password|token|authorization|cookie|signature|x-amz-[a-z-]+)=?[^\s&\"',]*")
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def mask(text: str) -> str:
    return _SECRET.sub(lambda match: match.group(1) + "=[redacted]", str(text))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": mask(record.getMessage()),
        }
        payload.update(_CONTEXT.get())
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = mask(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = mask(self.formatException(record.exc_info))[-4000:]
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True


@contextmanager
def log_context(**values):
    merged = {**_CONTEXT.get(), **{key: value for key, value in values.items() if value}}
    token = _CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _CONTEXT.reset(token)


def bind(**values) -> None:
    _CONTEXT.set({**_CONTEXT.get(), **{key: value for key, value in values.items() if value}})


def current() -> dict:
    return dict(_CONTEXT.get())
