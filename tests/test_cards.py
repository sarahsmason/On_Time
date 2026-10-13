"""On-screen cards (MCP Apps, io.modelcontextprotocol/ui, spec 2026-01-26), checked over real HTTP."""

import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from on_time.cards import CHECKIN_CARD, TIMELINE_CARD, TOOL_CARDS
from on_time.server import create_app

HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
APPS = "io.modelcontextprotocol/ui"
MIME = "text/html;profile=mcp-app"


@pytest.fixture(scope="module")
def base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app("127.0.0.1", port, api_token=""), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def rpc(base: str, method: str, params: dict | None = None, initialize_caps: dict | None = None) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}
    headers = {**HEADERS, **({} if method == "initialize" else {"MCP-Protocol-Version": "2025-11-25"})}
    return httpx.post(f"{base}/mcp", json=body, headers=headers, timeout=5).json()


def test_apps_extension_is_registered_and_handshake_stays_2025_11_25_valid(base):
    from on_time import server

    # Registered with the SDK (advertised under `capabilities.extensions` on 2026-07-28 connections) ...
    assert APPS in server.mcp._lowlevel_server.extensions
    # ... while a 2025-11-25 handshake keeps that schema (no `extensions` field there); hosts find
    # the cards through each tool's `_meta.ui.resourceUri`, which 2025-11-25 does carry.
    result = rpc(base, "initialize", {
        "protocolVersion": "2025-11-25",
        "capabilities": {"extensions": {APPS: {"mimeTypes": [MIME]}}},
        "clientInfo": {"name": "cards-test", "version": "0"},
    })["result"]  # fmt: skip
    assert result["protocolVersion"] == "2025-11-25"


def test_tools_point_at_their_cards(base):
    tools = {t["name"]: t for t in rpc(base, "tools/list")["result"]["tools"]}
    for name, uri in TOOL_CARDS.items():
        assert tools[name]["_meta"]["ui"]["resourceUri"] == uri
    assert "ui" not in (tools["set_light"].get("_meta") or {})  # only the card tools carry one


def test_card_resources_are_listed_with_the_apps_mime_type(base):
    resources = {r["uri"]: r for r in rpc(base, "resources/list")["result"]["resources"]}
    for uri in (CHECKIN_CARD, TIMELINE_CARD):
        assert resources[uri]["mimeType"] == MIME


@pytest.mark.parametrize("uri", [CHECKIN_CARD, TIMELINE_CARD])
def test_card_html_is_self_contained(base, uri):
    [content] = rpc(base, "resources/read", {"uri": uri})["result"]["contents"]
    html = content["text"]
    assert content["mimeType"] == MIME
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "/*ON_TIME_STYLE*/" not in html and "/*ON_TIME_BRIDGE*/" not in html  # inlined at build time
    assert '"ui/initialize"' in html and "2026-01-26" in html
    # Runs under the spec's restrictive default CSP: nothing may load from the network.
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
    assert "fetch(" not in html


def test_cards_render_the_tools_they_belong_to(base):
    [checkin] = rpc(base, "resources/read", {"uri": CHECKIN_CARD})["result"]["contents"]
    assert '"respond_to_checkin"' in checkin["text"] and '"get_checkins"' in checkin["text"]
    [timeline] = rpc(base, "resources/read", {"uri": TIMELINE_CARD})["result"]["contents"]
    assert "glide_preview" in timeline["text"] and "events" in timeline["text"]


@pytest.mark.parametrize("name", ["sandbox.html", "apps-host.js"])
def test_host_files_are_served(base, name):
    r = httpx.get(f"{base}/web/{name}", timeout=5)
    assert r.status_code == 200


def test_sandbox_is_reachable_from_the_other_local_origin(base):
    # The host page on 127.0.0.1 loads the sandbox from localhost (a different origin, per the spec).
    other = base.replace("127.0.0.1", "localhost")
    r = httpx.get(f"{other}/web/sandbox.html?host={base}", timeout=5)
    assert r.status_code == 200
    assert '"ui/notifications/sandbox-"' in r.text and '"proxy-ready"' in r.text
