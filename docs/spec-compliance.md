# MCP Spec Compliance (2025-11-25, Streamable HTTP)

The Alexa+ track requires a self-hosted MCP server that follows MCP spec **2025-11-25** over
**Streamable HTTP**. Each requirement below is checked by an automated test in
[`tests/test_transport.py`](../tests/test_transport.py), against a real running server.

Run the checks: `uv run python -m pytest tests/test_transport.py tests/test_cards.py -q`

## Transport requirements
"Must" and "should" are the spec's own levels.

**T1 · One MCP endpoint that accepts POST and GET (`/mcp`).** Must.
Built on the official MCP Python SDK 2.2.0 and its Streamable HTTP transport.
*Test:* `test_initialize_negotiates_2025_11_25`

**T2 · The handshake agrees on protocol version `2025-11-25`.** Must (a track rule).
The SDK answers `initialize`, and the tests confirm the version.
*Tests:* `test_initialize_negotiates_2025_11_25`, `test_official_client_end_to_end`

**T3 · A POSTed request gets back JSON (`application/json`) or a stream (`text/event-stream`).** Must.
The server is stateless and replies with JSON.
*Test:* `test_initialize_negotiates_2025_11_25`

**T4 · A POSTed notification or response gets `202 Accepted` with no body.** Must.
Handled by the SDK and confirmed by the tests.
*Test:* `test_notification_returns_202`

**T5 · GET either opens a server-sent event stream or returns `405`.** Must.
It opens a stream (the SDK default); On Time never pushes messages on it.
*Test:* `test_get_opens_sse_or_405`

**T6 · Requests from an unknown web origin are rejected with `403`.** Must (protects against DNS
rebinding).
Only known hosts and origins are allowed; an unknown host gets `421`.
*Tests:* `test_invalid_origin_rejected`, `test_local_origin_allowed`, `test_invalid_host_rejected`

**T7 · An unsupported `MCP-Protocol-Version` header gets `400`.** Must.
Handled by the SDK and confirmed by the tests.
*Test:* `test_unsupported_protocol_version_rejected`

**T8 · Listen only on this computer (`127.0.0.1`) when running locally.** Should.
That's the default.
*Test:* `test_default_host_is_localhost`

**T9 · Require authentication on every connection.** Should.
A bearer token (`ON_TIME_API_TOKEN`) is required on `/mcp` and `/agent` and compared in constant
time. A missing or wrong token gets `401` with a `WWW-Authenticate` header. Without a token, the
server refuses to listen beyond this computer.
*Tests:* `test_missing_token_rejected`, `test_wrong_token_rejected`, `test_get_without_token_rejected`,
`test_refuses_public_bind_without_token`

**T10 · Sessions (`MCP-Session-Id`).** May.
Not used: the server is stateless, and the home's memory lives in the database.
*Test:* `test_stateless_no_session_id`

**T11 · Resuming a dropped stream (`Last-Event-ID`).** May.
Not needed: every response is a single JSON reply. No test.

## Works with other clients
- **The official MCP Python client.** `test_official_client_end_to_end` connects with the SDK's
  `Client` over Streamable HTTP (with the bearer token), agrees on 2025-11-25, lists all 13 tools,
  and calls them.
- **MCP Inspector v2.9.0.** Connected as "MCP 2025-11-25" and called tools (see friction log FL-007).
- **The browser.** The Simulated On Time for Alexa+ page is its own MCP client
  ([`web/mcp-client.js`](../src/on_time/web/mcp-client.js)). It sends `initialize`, then
  `notifications/initialized`, `tools/list`, and `tools/call`, all shown live in the page's protocol panel.

## On-screen cards (MCP Apps extension, 2026-01-26)
The cards are an optional extension on top of the 2025-11-25 core. The handshake stays on
2025-11-25, and hosts find each tool's card through `_meta.ui.resourceUri`. Checked in
[`tests/test_cards.py`](../tests/test_cards.py).

- **Card resources use the `ui://` scheme and the type `text/html;profile=mcp-app`.**
  `ui://on-time/checkin-card` and `ui://on-time/schedule-timeline` are registered with the SDK's
  `Apps` extension.
- **Tools point to their card with `_meta.ui.resourceUri`.** `get_checkins` shows the check-in card;
  `get_schedule` and `plan_dst_transition` show the timeline.
- **Cards work under the strict default content security policy.** Each card is one self-contained
  HTML file, with its styles and script inline and no network requests.
- **Cards follow the start-up sequence:** `ui/initialize`, then `ui/notifications/initialized`,
  then the tool input and result. See [`web/cards/bridge.js`](../src/on_time/web/cards/bridge.js) (the
  card side) and [`web/apps-host.js`](../src/on_time/web/apps-host.js) (the host side).
- **A web host shows cards through a sandbox page on a different web address.**
  [`web/sandbox.html`](../src/on_time/web/sandbox.html) is loaded from the other local address, with
  the security policy set in the page.
- **Cards call server tools through the host, which checks that each tool is allowed.** Card buttons
  call `respond_to_checkin` and `get_checkins` with `tools/call`.
- **Cards tell the assistant what happened** with `ui/update-model-context` after each answer.
- **Cards report their size** with `ui/notifications/size-changed` whenever it changes.
- **Hosts without MCP Apps still work.** Every tool result keeps its full text and structured data.
