"""Structured JSON logging: formatter with trace correlation + per-request log line."""
import json
import logging
import os
import time
from datetime import datetime, timezone

from telemetry_common import EXCLUDED_PATHS, SLO_LATENCY_SECONDS, current_trace_ids, route_template

request_logger = logging.getLogger("http.request")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, timezone.utc)
        out = {
            "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "service": os.getenv("OTEL_SERVICE_NAME"),
            "message": record.getMessage(),
        }
        ids = current_trace_ids()
        if ids:
            out.update(ids)
        out.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def _level_for(status_code: int, duration_s: float) -> int:
    if status_code >= 500:
        return logging.ERROR
    if duration_s > SLO_LATENCY_SECONDS:
        return logging.WARNING
    return logging.INFO


class RequestLogMiddleware:
    """Pure ASGI middleware: one log line per request, emitted even if the client disconnected."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status = {"code": 500}  # if the app raises before responding

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            request_logger.log(
                _level_for(status["code"], duration),
                "request",
                extra={"fields": {
                    "method": scope["method"],
                    "route": route_template(scope),
                    "path": scope["path"],
                    "status_code": int(status["code"]),
                    "duration_ms": round(duration * 1000, 1),
                }},
            )


def setup_request_log(app) -> None:
    app.add_middleware(RequestLogMiddleware)
