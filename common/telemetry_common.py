"""Helpers shared by metrics and logs (depends only on opentelemetry-api)."""
from opentelemetry import trace

EXCLUDED_PATHS = {"/metrics", "/health"}

# Keep in sync with le="0.1" in prometheus/rules/alerts.yml,
# prometheus/tests/alerts.test.yaml and grafana/dashboards/service-overview.json.
SLO_LATENCY_SECONDS = 0.1


def current_trace_ids() -> dict | None:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
    }


def route_template(scope) -> str:
    # Route template (set by the router on the shared scope), never the raw path.
    return getattr(scope.get("route"), "path_format", None) or "unmatched"
