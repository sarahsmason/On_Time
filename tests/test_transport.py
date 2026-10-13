"""Streamable HTTP compliance tests (MCP spec 2025-11-25). IDs match docs/spec-compliance.md T1-T11."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from on_time.server import create_app

PROTOCOL_VERSION = "2025-11-25"
TOKEN = "test-token-123"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {TOKEN}",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "on-time-tests", "version": "0"},
    },
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    app = create_app("127.0.0.1", port, api_token=TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def post(url: str, body: dict, **headers: str) -> httpx.Response:
    return httpx.post(url, json=body, headers={**HEADERS, **headers}, timeout=5)


def rpc(url: str, body: dict) -> httpx.Response:
    return post(url, body, **{"MCP-Protocol-Version": PROTOCOL_VERSION})


# T1 + T2 + T3: single /mcp endpoint, negotiates 2025-11-25, JSON response
def test_initialize_negotiates_2025_11_25(base_url):
    r = post(base_url, INITIALIZE)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    result = r.json()["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "on-time"


# T10: stateless, no session id issued
def test_stateless_no_session_id(base_url):
    r = post(base_url, INITIALIZE)
    assert "mcp-session-id" not in r.headers


# T4: notifications are accepted with 202 and no body
def test_notification_returns_202(base_url):
    r = rpc(base_url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    assert r.content == b""


def call_tool(url: str, name: str, arguments: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    return rpc(url, body).json()["result"]


def test_tools_list(base_url):
    tools = rpc(base_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    assert {"set_location", "get_location", "list_lights", "set_light"} <= {t["name"] for t in tools}
    assert all(t.get("description") for t in tools)


def test_tool_call_over_http(base_url):
    result = call_tool(base_url, "set_light", {"light_id": "porch", "action": "on"})
    assert result["isError"] is False
    assert result["structuredContent"]["light"] == {"id": "porch", "name": "Porch", "is_on": True}


def test_expected_tool_error_reaches_agent(base_url):
    result = call_tool(base_url, "set_light", {"light_id": "garage", "action": "on"})
    assert result["isError"] is True
    assert "Valid lights" in result["content"][0]["text"]


def test_invalid_arguments_rejected(base_url):
    result = call_tool(base_url, "set_light", {"light_id": "porch", "action": "dim"})
    assert result["isError"] is True


# T5: GET returns an SSE stream (or 405); never a JSON-RPC response
def test_get_opens_sse_or_405(base_url):
    headers = {
        "Accept": "text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Authorization": f"Bearer {TOKEN}",
    }
    with httpx.stream("GET", base_url, headers=headers, timeout=5) as r:
        assert r.status_code == 405 or (
            r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        )


# T6: invalid Origin -> 403 (DNS rebinding protection)
def test_invalid_origin_rejected(base_url):
    r = post(base_url, INITIALIZE, Origin="http://evil.example")
    assert r.status_code == 403


def test_local_origin_allowed(base_url):
    r = post(base_url, INITIALIZE, Origin="http://localhost:6274")
    assert r.status_code == 200


def test_invalid_host_rejected(base_url):
    r = post(base_url, INITIALIZE, Host="evil.example")
    assert r.status_code == 421


# T7: unsupported MCP-Protocol-Version -> 400
def test_unsupported_protocol_version_rejected(base_url):
    r = post(base_url, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, **{"MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 400


# T9: authentication on all connections
def test_missing_token_rejected(base_url):
    r = httpx.post(base_url, json=INITIALIZE, headers={k: v for k, v in HEADERS.items() if k != "Authorization"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_wrong_token_rejected(base_url):
    r = post(base_url, INITIALIZE, Authorization="Bearer not-the-token")
    assert r.status_code == 401


def test_get_without_token_rejected(base_url):
    r = httpx.get(base_url, headers={"Accept": "text/event-stream"}, timeout=5)
    assert r.status_code == 401


def test_refuses_public_bind_without_token(monkeypatch):
    monkeypatch.delenv("ON_TIME_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="without ON_TIME_API_TOKEN"):
        create_app("0.0.0.0", 8000, api_token="")


# End to end with the official MCP Python client (initialize handshake = spec 2025-11-25)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_official_client_end_to_end(base_url):
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    async with http, Client(streamable_http_client(base_url, http_client=http), mode="legacy") as client:
        assert client.protocol_version == PROTOCOL_VERSION
        assert client.server_info.name == "on-time"

        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {
            "set_location", "get_location", "list_lights", "set_light",
            "add_routine", "list_routines", "remove_routine",
            "get_schedule", "explain_light_time", "plan_dst_transition",
            "get_checkins", "respond_to_checkin", "set_demo_clock",
        }  # fmt: skip

        loc = await client.call_tool("set_location", {"city": "Oswego", "region": "NY"})
        assert loc.structured_content["location"]["timezone"] == "America/New_York"
        await client.call_tool("add_routine", {"name": "Dusk", "light_ids": ["porch"], "action": "on", "solar_event": "sunset"})
        schedule = await client.call_tool("get_schedule", {"day": "2026-10-08"})
        [event] = schedule.structured_content["events"]
        assert event["factors"]["base"]["detail"].startswith("sunset 18:")

        bad = await client.call_tool("set_light", {"light_id": "garage", "action": "on"})
        assert bad.is_error


# T8: binds to localhost by default
def test_default_host_is_localhost():
    import inspect

    from on_time import server

    assert inspect.signature(server.create_app).parameters["host"].default == "127.0.0.1"
