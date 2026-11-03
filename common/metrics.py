"""Shared RED metrics for the FastAPI services.

Code depends only on opentelemetry-api (to read the current trace id for
exemplars); the SDK and exporters stay outside the code (zero-code tracing).
"""
import time

from prometheus_client import Histogram, make_asgi_app
from telemetry_common import EXCLUDED_PATHS, SLO_LATENCY_SECONDS, current_trace_ids, route_template

LATENCY_BUCKETS = (.06, SLO_LATENCY_SECONDS, .25, .5, 1.0, 2.5, 3.5, 6.0)

REQUEST_DURATION = Histogram(
    "http_server_request_duration_seconds",
    "HTTP server request duration in seconds.",
    ["method", "route", "status_code"],
    buckets=LATENCY_BUCKETS,
)


class MetricsMiddleware:
    """Pure ASGI middleware: measures the full response, incl. HTTPException 5xx."""

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
            REQUEST_DURATION.labels(
                method=scope["method"], route=route_template(scope), status_code=str(status["code"])
            ).observe(duration, exemplar=current_trace_ids())


class _AsgiEndpoint:
    """Starlette treats non-function callables as raw ASGI apps (no redirect, no Request wrapping)."""

    def __init__(self, asgi_app):
        self._app = asgi_app

    async def __call__(self, scope, receive, send):
        await self._app(scope, receive, send)


def setup_metrics(app) -> None:
    app.add_middleware(MetricsMiddleware)
    # make_asgi_app negotiates OpenMetrics (exemplars) via the Accept header.
    app.add_route("/metrics", _AsgiEndpoint(make_asgi_app()))
