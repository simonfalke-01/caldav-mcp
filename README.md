# iCloud CalDAV MCP

A production-oriented [FastMCP](https://gofastmcp.com/) server that gives an AI calendar
operator full iCloud Calendar access through CalDAV. It exposes Streamable HTTP at `/mcp` and
requires a separate bearer key on every HTTP request.

## Security model

The two authentication layers are intentionally independent:

1. `MCP_API_KEY` protects the public MCP endpoint. Poke sends it as
   `Authorization: Bearer <MCP_API_KEY>`.
2. `ICLOUD_USERNAME` and `ICLOUD_APP_PASSWORD` authenticate this server to Apple CalDAV.

Never use your normal Apple ID password. Apple requires two-factor authentication before you can
create an app-specific password. Create one at
[account.apple.com](https://account.apple.com/) under **Sign-In and Security → App-Specific
Passwords**. Rotate both secrets immediately if they are exposed.

Configuration precedence is process environment, then `.env`, then documented defaults. The
server exits before binding if any required secret is absent. Secret values are never logged or
returned by tools.

## Local setup

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are recommended.

```bash
cp .env.example .env
# Edit .env and replace every placeholder.
uv sync --all-extras
uv run icloud-caldav-mcp
```

The server binds to `0.0.0.0:${PORT:-8000}`. Its MCP URL is:

```text
http://localhost:8000/mcp
```

Exact development checks:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
./scripts/inspector_smoke.sh
```

Tests marked `integration` launch an ephemeral Radicale process bound only to loopback. They never
contact iCloud. When real `ICLOUD_*` credentials are present, the separately selected manual smoke
test can verify Apple discovery without modifying events:

```bash
RUN_ICLOUD_SMOKE=1 uv run pytest -m icloud -v
```

## Connect Poke

Poke requires a public HTTPS endpoint. In Poke, create a custom MCP connection with:

- URL: `https://<your-host>/mcp`
- Header: `Authorization: Bearer <the exact MCP_API_KEY value>`
- Transport: Streamable HTTP

Do not put the Apple credentials in Poke; only this server needs them.

## Docker

One-command build and run after creating `.env`:

```bash
docker build -t icloud-caldav-mcp . && docker run --rm --env-file .env -p 8000:8000 icloud-caldav-mcp
```

The image runs as an unprivileged user and listens on `PORT`.

## HTTPS deployment

For a hosted service, deploy the Dockerfile to Render, Fly.io, Railway, Cloud Run, or another
container platform; configure the three required secrets in that platform's secret manager and
set its internal port to `8000`. The platform terminates TLS, yielding
`https://<service-host>/mcp`.

For a temporary local endpoint, run the server and a reputable HTTPS tunnel such as Cloudflare
Tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

Append `/mcp` to the generated HTTPS hostname. Treat an ephemeral tunnel URL and its bearer key as
production credentials; stop the tunnel and rotate the key when finished.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `MCP_API_KEY` | yes | — | Bearer credential protecting every HTTP request |
| `ICLOUD_USERNAME` | yes | — | Apple ID email |
| `ICLOUD_APP_PASSWORD` | yes | — | Apple app-specific password |
| `CALDAV_URL` | no | `https://caldav.icloud.com` | CalDAV origin; override only for local testing |
| `PORT` | no | `8000` | HTTP listen port |
| `DEFAULT_TIMEZONE` | no | `UTC` | IANA timezone used when a tool omits one |
| `LOG_LEVEL` | no | `INFO` | Structured log threshold |

## Tool safety conventions

Mutations return the event UID, opaque calendar ID, and exactly what changed. Deletions require
the UID and explicit scope. Datetimes are ISO 8601; timed values without an offset are interpreted
in the supplied `timezone`, while all-day values are calendar dates with an exclusive end date.
Conflict warnings are advisory and do not silently reject a requested mutation.

The server exposes 21 tools: calendar list/create/update/delete; event create/get/list/agenda,
search/update/edit/delete/move; invite response; free/busy and free-slot finding; quick-add; ICS
import/export; and safe batch create/delete. Create requests support stable idempotency keys, and
updates support expected `SEQUENCE` checks to prevent lost concurrent edits.


## iCloud behavior

Apple can redirect discovery from `caldav.icloud.com` to a partition host such as
`pXX-caldav.icloud.com`. The client follows principal and calendar-home-set discovery and keeps the
canonical URLs Apple returns. Calendar permissions and server-side scheduling behavior remain
subject to the iCloud account and calendar sharing configuration.
