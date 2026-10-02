"""Streamable HTTP compliance tests (MCP spec 2025-11-25). IDs match docs/plan.md T1-T11."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from on_time.server import create_app

PROTOCOL_VERSION = "2025-11-25"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
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
    server = uvicorn.Server(uvicorn.Config(create_app("127.0.0.1", port), host="127.0.0.1", port=port, log_level="warning"))
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


def test_tools_list_and_call(base_url):
    tools = rpc(base_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["ping_time"]

    r = rpc(base_url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ping_time", "arguments": {}}})
    result = r.json()["result"]
    assert result["isError"] is False
    assert set(result["structuredContent"]) == {"timezone", "local_time", "utc_offset", "is_dst"}


# T5: GET returns an SSE stream (or 405); never a JSON-RPC response
def test_get_opens_sse_or_405(base_url):
    headers = {"Accept": "text/event-stream", "MCP-Protocol-Version": PROTOCOL_VERSION}
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


# T8: binds to localhost by default
def test_default_host_is_localhost():
    import inspect

    from on_time import server

    assert inspect.signature(server.create_app).parameters["host"].default == "127.0.0.1"
