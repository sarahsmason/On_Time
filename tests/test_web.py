"""The web client's static files: served publicly, while /mcp still requires the token."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from on_time.server import create_app

TOKEN = "web-test-token"


@pytest.fixture(scope="module")
def base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app("127.0.0.1", port, api_token=TOKEN), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_index_is_public_and_labelled(base):
    r = httpx.get(f"{base}/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Simulated On Time for Alexa+" in r.text


@pytest.mark.parametrize(("name", "type_"), [("app.js", "text/javascript"), ("mcp-client.js", "text/javascript"),
                                             ("styles.css", "text/css"), ("icon.svg", "image/svg+xml")])  # fmt: skip
def test_assets_served_with_types(base, name, type_):
    r = httpx.get(f"{base}/web/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(type_)


@pytest.mark.parametrize("name", ["missing.js", "..%2Fserver.py", "server.py", "%2E%2E%5Cauth.py"])
def test_unknown_or_traversal_paths_are_404(base, name):
    assert httpx.get(f"{base}/web/{name}").status_code == 404


def test_mcp_still_requires_token(base):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    assert httpx.post(f"{base}/mcp", json=body, headers=headers).status_code == 401


def test_client_speaks_the_required_protocol_version():
    from pathlib import Path

    js = (Path(__file__).parents[1] / "src" / "on_time" / "web" / "mcp-client.js").read_text()
    assert 'PROTOCOL_VERSION = "2025-11-25"' in js
    for method in ("initialize", "notifications/initialized", "tools/list", "tools/call"):
        assert f'"{method}"' in js
