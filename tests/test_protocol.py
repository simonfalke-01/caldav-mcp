from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from pydantic import SecretStr
from starlette.testclient import TestClient

from icloud_caldav_mcp.config import Settings
from icloud_caldav_mcp.server import build_mcp, create_http_app


class FakeService:
    settings = Settings(
        mcp_api_key=SecretStr("correct-protocol-key"),
        icloud_username=SecretStr("fake@example.com"),
        icloud_app_password=SecretStr("fake-password"),
        caldav_url="http://127.0.0.1:9999",
    )

    def list_calendars(self) -> dict[str, Any]:
        return {"calendars": [{"calendar_id": "abc", "name": "Primary", "color": None}], "count": 1}


@pytest.mark.asyncio
async def test_mcp_lists_and_calls_real_tool_contract() -> None:
    mcp = build_mcp(FakeService())  # type: ignore[arg-type]
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        result = await client.call_tool("list_calendars", {})

    assert {
        "list_calendars",
        "create_event",
        "get_event",
        "list_events",
        "agenda",
        "update_event",
        "edit_event",
        "delete_event",
        "search_events",
        "free_busy",
        "find_free_slots",
        "quick_add",
        "import_ics",
        "export_ics",
    } <= names
    assert result.data["count"] == 1
    create_schema = next(tool.inputSchema for tool in tools if tool.name == "create_event")
    assert create_schema["additionalProperties"] is False
    assert "request" in create_schema["properties"]


def test_streamable_http_initialize_is_globally_bearer_gated() -> None:
    service = FakeService()
    app = create_http_app(service.settings, service)  # type: ignore[arg-type]
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "auth-test", "version": "1"},
        },
    }
    accept = {"Accept": "application/json, text/event-stream"}
    with TestClient(app) as client:
        missing = client.post("/mcp", json=initialize, headers=accept)
        wrong = client.post(
            "/mcp",
            json=initialize,
            headers={**accept, "Authorization": "Bearer wrong-key"},
        )
        accepted = client.post(
            "/mcp",
            json=initialize,
            headers={**accept, "Authorization": "Bearer correct-protocol-key"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert '"name":"iCloud Calendar"' in accepted.text
    assert "correct-protocol-key" not in missing.text + wrong.text + accepted.text
