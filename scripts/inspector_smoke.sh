#!/usr/bin/env bash
set -euo pipefail

SMOKE_DIR=$(mktemp -d)
RADICALE_PID=""
MCP_PID=""

cleanup() {
  if [[ -n "$MCP_PID" ]]; then kill "$MCP_PID" 2>/dev/null || true; fi
  if [[ -n "$RADICALE_PID" ]]; then kill "$RADICALE_PID" 2>/dev/null || true; fi
  if [[ -n "$MCP_PID" ]]; then wait "$MCP_PID" 2>/dev/null || true; fi
  if [[ -n "$RADICALE_PID" ]]; then wait "$RADICALE_PID" 2>/dev/null || true; fi
  rm -rf -- "$SMOKE_DIR"
}
trap cleanup EXIT

read -r CALDAV_PORT MCP_PORT < <(uv run python - <<'PY'
import socket

ports = []
for _ in range(2):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    ports.append(sock.getsockname()[1])
    sock.close()
print(*ports)
PY
)

uv run radicale -C /dev/null \
  --server-hosts "127.0.0.1:${CALDAV_PORT}" \
  --auth-type none \
  --storage-filesystem-folder "$SMOKE_DIR/storage" \
  >"$SMOKE_DIR/radicale.log" 2>&1 &
RADICALE_PID=$!

uv run python - "$CALDAV_PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
for _ in range(100):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            break
    except OSError:
        time.sleep(0.05)
else:
    raise SystemExit("Radicale did not start")
PY

uv run python - "$CALDAV_PORT" <<'PY'
import sys
from caldav import DAVClient

client = DAVClient(
    f"http://127.0.0.1:{sys.argv[1]}",
    username="inspector@example.com",
    password="local-app-password",
)
principal = client.principal()
if not principal.calendars():
    principal.make_calendar(name="Inspector Calendar", cal_id="inspector")
client.close()
PY

MCP_API_KEY=inspector-local-key-123 \
ICLOUD_USERNAME=inspector@example.com \
ICLOUD_APP_PASSWORD=local-app-password \
CALDAV_URL="http://127.0.0.1:${CALDAV_PORT}" \
PORT="$MCP_PORT" \
uv run icloud-caldav-mcp >"$SMOKE_DIR/mcp.log" 2>&1 &
MCP_PID=$!

for _ in {1..100}; do
  HTTP_CODE=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${MCP_PORT}/mcp" || true)
  if [[ "$HTTP_CODE" == "401" ]]; then break; fi
  sleep 0.05
done
if [[ "$HTTP_CODE" != "401" ]]; then
  echo "MCP server did not expose an authenticated /mcp endpoint" >&2
  exit 1
fi

npx --yes @modelcontextprotocol/inspector@1.0.0 --cli \
  "http://127.0.0.1:${MCP_PORT}/mcp" \
  --method tools/list \
  --transport http \
  --header 'Authorization: Bearer inspector-local-key-123' \
  >"$SMOKE_DIR/inspector.json"

grep -q '"name": "list_calendars"' "$SMOKE_DIR/inspector.json"
grep -q '"name": "create_event"' "$SMOKE_DIR/inspector.json"
grep -q '"name": "batch_create_events"' "$SMOKE_DIR/inspector.json"

if grep -Fq 'inspector-local-key-123' "$SMOKE_DIR/mcp.log"; then
  echo "MCP bearer key appeared in logs" >&2
  exit 1
fi
if grep -Fq 'local-app-password' "$SMOKE_DIR/mcp.log"; then
  echo "CalDAV password appeared in logs" >&2
  exit 1
fi

echo "Inspector smoke passed: authenticated Streamable HTTP /mcp exposes the full tool registry."

