from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from icloud_caldav_mcp.auth import BearerAuthMiddleware


async def ok(_request: object) -> JSONResponse:
    return JSONResponse({"ok": True})


def test_bearer_gate_rejects_missing_and_wrong_tokens() -> None:
    app = BearerAuthMiddleware(Starlette(routes=[Route("/mcp", ok)]), "correct-secret")
    client = TestClient(app)

    missing = client.get("/mcp")
    wrong = client.get("/mcp", headers={"Authorization": "Bearer wrong-secret"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == 'Bearer realm="mcp"'
    assert "correct-secret" not in missing.text + wrong.text


def test_bearer_gate_allows_exact_token() -> None:
    app = BearerAuthMiddleware(Starlette(routes=[Route("/mcp", ok)]), "correct-secret")
    response = TestClient(app).get("/mcp", headers={"Authorization": "Bearer correct-secret"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
