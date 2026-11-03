"""Telemetry test: send a request with our own trace id, then check Tempo holds the full chain."""
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("API_URL", "http://localhost:8000")
TEMPO = os.environ.get("TEMPO_URL", "http://localhost:3200")
TIMEOUT_S = 30


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode()


def norm(span_id):
    """Tempo returns base64 ids; accept hex too. Normalise to lowercase hex."""
    if not span_id:
        return ""
    if len(span_id) == 16 and all(c in "0123456789abcdefABCDEF" for c in span_id):
        return span_id.lower()
    return base64.b64decode(span_id).hex()


def fetch_spans(trace_id):
    """Return [(service, kind, span_id, parent_id)] or None if Tempo has no trace yet."""
    try:
        _, body = get(f"{TEMPO}/api/traces/{trace_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    out = []
    for batch in json.loads(body).get("batches", []):
        attrs = {a["key"]: a["value"].get("stringValue") for a in batch["resource"]["attributes"]}
        for scope in batch["scopeSpans"]:
            for s in scope["spans"]:
                out.append((attrs.get("service.name"), s.get("kind"),
                            norm(s["spanId"]), norm(s.get("parentSpanId"))))
    return out


def find(spans, service, kind, parent):
    return [s for s in spans if s[0] == service and s[1] == kind and s[3] == parent]


trace_id, parent_id = secrets.token_hex(16), secrets.token_hex(8)

# 1. Request with a self-supplied trace context.
try:
    status, _ = get(f"{API}/products/sku-1", {"traceparent": f"00-{trace_id}-{parent_id}-01"})
except urllib.error.HTTPError as e:
    status = e.code
if status != 200:
    fail(f"GET /products/sku-1 returned {status}, expected 200")

# 2. Poll Tempo until both services have flushed spans (they arrive separately).
deadline, spans = time.time() + TIMEOUT_S, None
while time.time() < deadline:
    spans = fetch_spans(trace_id)
    if spans and {"api-service", "inventory-service"} <= {s[0] for s in spans}:
        break
    time.sleep(2)
else:
    seen = sorted({s[0] for s in spans or []})
    if not spans:
        fail(f"trace {trace_id} not found in Tempo after {TIMEOUT_S}s (no spans from any service)")
    missing = [s for s in ("api-service", "inventory-service") if s not in seen]
    hint = " - trace context may not have crossed the api->inventory hop" if "inventory-service" in missing else ""
    fail(f"trace {trace_id}: no spans from {missing} after {TIMEOUT_S}s (saw {seen}){hint}")

# 3. The hop survived: SERVER(parent=ours) -> CLIENT -> inventory SERVER.
api_server = find(spans, "api-service", "SPAN_KIND_SERVER", parent_id)
if not api_server:
    fail(f"api-service has no SERVER span with parent {parent_id} (incoming traceparent not honoured)")
api_client = find(spans, "api-service", "SPAN_KIND_CLIENT", api_server[0][2])
if not api_client:
    fail("api-service has no CLIENT span under its SERVER span")
if not find(spans, "inventory-service", "SPAN_KIND_SERVER", api_client[0][2]):
    fail("inventory-service has no SERVER span whose parent is the api-service CLIENT span "
         "(context did not propagate across the hop)")

# 4. Alert guard: the le="0.1" latency bucket must still exist on /metrics.
_, metrics = get(f"{API}/metrics")
if not any(l.startswith("http_server_request_duration_seconds_bucket") and 'le="0.1"' in l
           for l in metrics.splitlines()):
    fail('api-service /metrics has no le="0.1" bucket for http_server_request_duration_seconds '
         "(the latency alert would silently stop working)")

print(f"PASS: trace {trace_id} has api SERVER -> api CLIENT -> inventory SERVER; le=\"0.1\" bucket present")
