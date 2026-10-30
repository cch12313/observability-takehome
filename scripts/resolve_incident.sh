#!/usr/bin/env bash
# Turn the fault scenario off.
set -euo pipefail

INVENTORY_ADMIN_URL="${INVENTORY_ADMIN_URL:-http://localhost:8001}"

echo "Disabling fault scenario ..."
curl -sS -X POST "${INVENTORY_ADMIN_URL}/admin/chaos" \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false}'
echo
echo "Fault scenario DISABLED."
