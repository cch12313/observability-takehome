#!/usr/bin/env bash
# Trigger a fault scenario in the running environment.
# Enables degraded behaviour in inventory-service via its admin endpoint.
#
# Usage:
#   ./scripts/trigger_incident.sh            # same as: slow
#   ./scripts/trigger_incident.sh slow       # slow upstream, still under api-service timeout
#   ./scripts/trigger_incident.sh timeout    # slow enough to exceed api-service timeout
#
# Env overrides (applied after the mode preset):
#   LATENCY_MS, PROBABILITY, INVENTORY_ADMIN_URL
set -euo pipefail

MODE="${1:-slow}"
INVENTORY_ADMIN_URL="${INVENTORY_ADMIN_URL:-http://localhost:8001}"

case "${MODE}" in
  slow)
    DEFAULT_LATENCY_MS=2000
    DEFAULT_PROBABILITY=0.3
    DESCRIPTION="a share of stock lookups becomes slow, but stays under the api-service upstream timeout"
    ;;
  timeout)
    DEFAULT_LATENCY_MS=4000
    DEFAULT_PROBABILITY=0.3
    DESCRIPTION="a share of stock lookups exceeds the api-service upstream timeout"
    ;;
  *)
    echo "Unknown mode: ${MODE} (expected 'slow' or 'timeout')" >&2
    exit 1
    ;;
esac

LATENCY_MS="${LATENCY_MS:-${DEFAULT_LATENCY_MS}}"
PROBABILITY="${PROBABILITY:-${DEFAULT_PROBABILITY}}"

echo "Enabling fault scenario '${MODE}' (latency_ms=${LATENCY_MS}, probability=${PROBABILITY}) ..."
echo "Effect: ${DESCRIPTION}"
curl -sS -X POST "${INVENTORY_ADMIN_URL}/admin/chaos" \
  -H 'Content-Type: application/json' \
  -d "{\"enabled\": true, \"latency_ms\": ${LATENCY_MS}, \"probability\": ${PROBABILITY}}"
echo
echo "Fault scenario ENABLED. Observe the impact via your dashboards and traces,"
echo "then run scripts/resolve_incident.sh to turn it off."
