"""On-screen cards (MCP Apps, `io.modelcontextprotocol/ui`, spec 2026-01-26).

Two `ui://` HTML resources that MCP Apps hosts render inside the conversation:
  * CHECKIN_CARD: pending check-ins with evidence; Yes / Adjust / Not now act from the card
  * TIMELINE_CARD: a day's light schedule factor by factor, or the Daylight Saving glide day by day

Tools point at them through `_meta.ui.resourceUri` (see TOOL_CARDS); hosts without MCP Apps keep
getting the same text and structured results. The shared view runtime and styles are inlined so
the cards load nothing from the network (they run under the spec's restrictive default CSP).
"""

from pathlib import Path

from mcp.server.apps import Apps

CARDS_DIR = Path(__file__).parent / "web" / "cards"

CHECKIN_CARD = "ui://on-time/checkin-card"
TIMELINE_CARD = "ui://on-time/schedule-timeline"

# tool name -> the card that renders its result
TOOL_CARDS = {
    "get_checkins": CHECKIN_CARD,
    "get_schedule": TIMELINE_CARD,
    "plan_dst_transition": TIMELINE_CARD,
}


def card_html(name: str) -> str:
    html = (CARDS_DIR / name).read_text(encoding="utf-8")
    html = html.replace("/*ON_TIME_STYLE*/", (CARDS_DIR / "card.css").read_text(encoding="utf-8"))
    return html.replace("/*ON_TIME_BRIDGE*/", (CARDS_DIR / "bridge.js").read_text(encoding="utf-8"))


def card_meta(tool_name: str) -> dict:
    """`_meta` for a tool rendered by a card (empty if it has none)."""
    uri = TOOL_CARDS.get(tool_name)
    return {"ui": {"resourceUri": uri}} if uri else {}


def build_apps() -> Apps:
    apps = Apps()
    apps.add_html_resource(
        CHECKIN_CARD,
        card_html("checkin-card.html"),
        name="On Time check-in card",
        description="Pending check-ins with their evidence; answer Yes, Adjust, or Not now.",
        prefers_border=False,
    )
    apps.add_html_resource(
        TIMELINE_CARD,
        card_html("schedule-timeline.html"),
        name="On Time schedule timeline",
        description="A day's light schedule factor by factor, or the Daylight Saving glide day by day.",
        prefers_border=False,
    )
    return apps
