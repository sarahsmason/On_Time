"""On_Time MCP server (Streamable HTTP, MCP spec 2025-11-25).

Phase 0 spike: one tool, stateless transport, Origin/Host validation.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

DEFAULT_TIMEZONE = "America/New_York"

mcp = MCPServer(
    name="on-time",
    title="On Time",
    description="Adapts home lighting schedules to habits, seasons, and Daylight Saving Time.",
    version="0.0.1",
)


@mcp.tool(title="Current time")
def ping_time(timezone: str = DEFAULT_TIMEZONE) -> dict[str, str | bool]:
    """Return the current local time and whether Daylight Saving Time is in effect.

    Args:
        timezone: IANA timezone name, e.g. "America/New_York".
    """
    now = datetime.now(ZoneInfo(timezone))
    return {
        "timezone": timezone,
        "local_time": now.isoformat(timespec="seconds"),
        "utc_offset": now.strftime("%z"),
        "is_dst": bool(now.dst()),
    }


def transport_security(host: str, port: int) -> TransportSecuritySettings:
    """DNS-rebinding protection: only local hosts/origins plus any listed in ON_TIME_ALLOWED_ORIGINS."""
    extra = [o.strip() for o in os.environ.get("ON_TIME_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", f"{host}:{port}"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", *extra],
    )


def create_app(host: str = "127.0.0.1", port: int = 8000):
    """Build the ASGI app: single /mcp endpoint, stateless, JSON responses."""
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(host, port),
        host=host,
    )


def main() -> None:
    import uvicorn

    host = os.environ.get("ON_TIME_HOST", "127.0.0.1")
    port = int(os.environ.get("ON_TIME_PORT", "8000"))
    uvicorn.run(create_app(host, port), host=host, port=port)
