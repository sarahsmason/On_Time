"""On_Time MCP server (Streamable HTTP, MCP spec 2025-11-25).

Tools are thin wrappers over `Home`; expected failures become `ToolError` so the agent reads them.
"""

import logging
import os
from collections.abc import Callable
from datetime import date, datetime, time
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp

from on_time import agent, cards
from on_time.auth import LOOPBACK_HOSTS, BearerTokenMiddleware
from on_time.clock import Clock
from on_time.home import Home, HomeError
from on_time.models import ClockAnchor, LightAction, LocationSource, SolarAnchor
from on_time.storage import Repository, SqliteRepository

mcp = MCPServer(
    name="on-time",
    title="On Time",
    description="Adapts home lighting schedules to habits, seasons, and Daylight Saving Time.",
    instructions=(
        "On Time manages the user's light routines (on/off only). Each routine's time combines the clock or "
        "sunrise/sunset at the home location, a gradual Daylight Saving Time shift, and adjustments the user "
        "confirmed in check-ins. Set the home location before anything that depends on sunrise or sunset."
    ),
    version="0.1.0",
    extensions=[cards.build_apps()],  # MCP Apps: on-screen check-in and timeline cards
)

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

_home: Home | None = None


def configure(repo: Repository) -> Home:
    """Wire the tools to a repository (tests pass an in-memory one)."""
    global _home
    _home = Home(repo, Clock(repo))
    _home.ensure_default_lights()
    return _home


def home() -> Home:
    if _home is None:
        return configure(SqliteRepository(os.environ.get("ON_TIME_DB", "on_time.db")))
    return _home


def user_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn expected HomeErrors into ToolErrors the agent can read and relay."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HomeError as e:
            raise ToolError(str(e)) from e

    return wrapper


# -- location ------------------------------------------------------------------


@mcp.tool(title="Set home location", annotations=WRITE)
@user_errors
def set_location(
    city: Annotated[str | None, Field(description="City name, e.g. 'Oswego'. Convert ZIP codes to a city first.")] = None,
    region: Annotated[str | None, Field(description="US state, country, or Australian state to disambiguate, e.g. 'NY', 'UK', 'Australia', 'NSW'")] = None,
    latitude: Annotated[float | None, Field(ge=-90, le=90, description="Use with longitude (e.g. device location)")] = None,
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None,
    place_name: Annotated[str | None, Field(description="Display name when setting by coordinates")] = None,
    timezone: Annotated[str | None, Field(description="IANA timezone if known; otherwise derived")] = None,
    source: Annotated[
        Literal["user", "device"], Field(description="'device' if the location came from the Alexa device")
    ] = "user",
) -> dict[str, Any]:
    """Set where the home is. Sunrise/sunset times and the timezone (for Daylight Saving Time) come from this.

    Give either a city (optionally with region) or latitude+longitude. If a city name is ambiguous,
    nothing is saved and the candidates are returned: ask the user which one they meant.
    """
    h = home()
    src = LocationSource(source)
    if latitude is not None and longitude is not None:
        location = h.set_location_from_coordinates(latitude, longitude, src, name=place_name, timezone=timezone)
    elif city:
        places = h.find_city(city, region)
        if len(places) > 1:
            return {
                "status": "needs_clarification",
                "question": f"Which {city} did you mean?",
                "candidates": [p.label for p in places],
            }
        location = h.set_location_from_place(places[0], src)
    else:
        raise ToolError("Give a city name, or both latitude and longitude.")
    return {"status": "set", "location": location.model_dump(mode="json")}


@mcp.tool(title="Get home location", annotations=READ_ONLY)
def get_location() -> dict[str, Any]:
    """Return the saved home location, its timezone, and the current local time there (or say it isn't set yet)."""
    h = home()
    location = h.get_location()
    if location is None:
        return {"status": "not_set", "hint": "Ask the user where they live, or use the device location."}
    now = h.clock.now(location.tz).replace(microsecond=0)
    return {
        "status": "set",
        "location": location.model_dump(mode="json"),
        "now": now.isoformat(),
        "demo_clock": h.clock.is_overridden,
    }


# -- lights --------------------------------------------------------------------


@mcp.tool(title="List lights", annotations=READ_ONLY)
def list_lights() -> dict[str, Any]:
    """List the home's lights and whether each is on."""
    return _with_checkins({"lights": [light.model_dump() for light in home().list_lights()]})


@mcp.tool(title="Turn a light on or off", annotations=WRITE)
@user_errors
def set_light(
    light_id: Annotated[str, Field(description="Light id from list_lights, e.g. 'living_room'")],
    action: Annotated[Literal["on", "off"], Field(description="Turn the light on or off")],
) -> dict[str, Any]:
    """Turn a light on or off right now, at the user's request.

    Every manual change is remembered: repeated changes around the same time feed the check-ins
    that propose adjusting a routine.
    """
    light, adjustment = home().set_light(light_id, LightAction(action))
    return _with_checkins({"light": light.model_dump(), "logged_at": adjustment.at.isoformat()})


# -- routines ------------------------------------------------------------------


@mcp.tool(title="Add a light routine", annotations=WRITE)
@user_errors
def add_routine(
    name: Annotated[str, Field(description="Short name, e.g. 'Porch at dusk'")],
    light_ids: Annotated[list[str], Field(min_length=1, description="Light ids from list_lights")],
    action: Annotated[Literal["on", "off"], Field(description="Turn the lights on or off")],
    at: Annotated[str | None, Field(description="Fixed clock time 'HH:MM' (24h). Use this OR solar_event.")] = None,
    solar_event: Annotated[
        Literal["sunrise", "sunset"] | None, Field(description="Follow the sun instead of the clock")
    ] = None,
    offset_minutes: Annotated[
        int, Field(ge=-240, le=240, description="With solar_event: minutes after (+) or before (-) it")
    ] = 0,
    days: Annotated[
        list[int] | None,
        Field(description="Weekdays it runs, Monday=0 ... Sunday=6. Omit for every day. Weekdays = [0,1,2,3,4]."),
    ] = None,
) -> dict[str, Any]:
    """Create a routine that turns lights on or off at a clock time, or relative to sunrise/sunset at home."""
    if (at is None) == (solar_event is None):
        raise ToolError("Give exactly one of `at` (clock time) or `solar_event` (sunrise/sunset).")
    if at is not None:
        try:
            anchor: Any = ClockAnchor(at=time.fromisoformat(at))
        except ValueError as e:
            raise ToolError(f"`at` must be a 24-hour time like '06:30', got {at!r}.") from e
    else:
        anchor = SolarAnchor(event=solar_event, offset_minutes=offset_minutes)
    routine = home().add_routine(name, light_ids, LightAction(action), anchor, days)
    return {"routine": routine.model_dump(mode="json")}


@mcp.tool(title="List light routines", annotations=READ_ONLY)
def list_routines() -> dict[str, Any]:
    """List light routines: their settings, plus `today` (the actual time today, with every factor applied).

    For "when" or "why" questions, answer from `today` (or get_schedule / explain_light_time), not the settings.
    """
    h = home()
    today: dict[str, dict] = {}
    if h.get_location() is not None:
        events, _ = h.schedule_for(h.today())
        today = {e.routine.id: {"at": e.at.isoformat(), "explanation": e.explain()} for e in events}
    return {
        "routines": [
            {**r.model_dump(mode="json"), "today": today.get(r.id, "not scheduled today")} for r in h.list_routines()
        ]
    }


@mcp.tool(title="Remove a light routine", annotations=ToolAnnotations(destructive_hint=True, open_world_hint=False))
@user_errors
def remove_routine(routine_id: Annotated[str, Field(description="Routine id from list_routines")]) -> dict[str, Any]:
    """Delete a light routine."""
    return {"removed": home().remove_routine(routine_id).model_dump(mode="json")}


# -- schedule ------------------------------------------------------------------


def _parse_day(value: str | None) -> date:
    if value is None:
        return home().today()
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ToolError(f"Dates look like '2026-10-31', got {value!r}.") from e


@mcp.tool(title="Get the light schedule", annotations=READ_ONLY, meta=cards.card_meta("get_schedule"))
@user_errors
def get_schedule(
    day: Annotated[str | None, Field(description="Date 'YYYY-MM-DD' in the home timezone; default today")] = None,
) -> dict[str, Any]:
    """When each light routine runs on a day, with the factors behind each time."""
    d = _parse_day(day)
    events, problems = home().schedule_for(d)
    return _with_checkins({"day": d.isoformat(), "events": [e.to_dict() for e in events], "problems": problems})


@mcp.tool(title="Explain a light's time", annotations=READ_ONLY)
@user_errors
def explain_light_time(
    routine_id: Annotated[str, Field(description="Routine id from list_routines or get_schedule")],
    day: Annotated[str | None, Field(description="Date 'YYYY-MM-DD'; default today")] = None,
) -> dict[str, Any]:
    """Explain why a routine runs when it does: clock or sunrise/sunset, DST glide, and confirmed adjustments."""
    return home().explain(routine_id, _parse_day(day)).to_dict()


@mcp.tool(
    title="Plan for the next Daylight Saving Time change",
    annotations=READ_ONLY,
    meta=cards.card_meta("plan_dst_transition"),
)
@user_errors
def plan_dst_transition() -> dict[str, Any]:
    """Find the next Daylight Saving Time change at home and preview the gradual shift for clock-based routines.

    Preview only: the shift is applied after the user agrees in a check-in.
    """
    h = home()
    tz, change = h.next_dst_change()
    if change is None:
        place = h.get_location().name
        return {
            "timezone": tz.key,
            "next_change": None,
            "note": f"{place} doesn't observe Daylight Saving Time: its clocks don't change, so there's nothing to ease.",
        }
    clocks = "go back 1 hour" if change.direction == "fall_back" else "go forward 1 hour"
    routines = h.list_routines()
    gliding = [r for r in routines if r.dst_glide and isinstance(r.anchor, ClockAnchor)]
    following_sun = [r for r in routines if isinstance(r.anchor, SolarAnchor)]
    plan = change.glide_plan()
    later = change.shift_minutes > 0
    steps = ", ".join(f"{d:%a} {m:+d} min" for d, m in plan)
    return _with_checkins({
        "glide_summary": (
            f"Clock-based routines move {abs(plan[0][1])} minutes {'LATER' if later else 'EARLIER'} each day "
            f"({steps}), so on {change.day:%A} they land on the new clock time. Applied only if the user says yes "
            "to the Daylight Saving check-in."
        ),
        "timezone": tz.key,
        "next_change": {
            "at": change.local_at.isoformat(),
            "direction": change.direction,
            "summary": f"Clocks {clocks} on {change.local_at:%A, %B} {change.day.day}",
        },
        "glide_preview": [{"day": d.isoformat(), "shift_minutes": m} for d, m in change.glide_plan()],
        "routines_that_would_glide": [{"id": r.id, "name": r.name} for r in gliding],
        "routines_following_the_sun": [
            {"id": r.id, "name": r.name, "note": "Follows the sun, so its clock time jumps with the change"}
            for r in following_sun
        ],
    }, prefer="dst_glide")


# -- check-ins -----------------------------------------------------------------


def _with_checkins(result: dict[str, Any], prefer: str | None = None) -> dict[str, Any]:
    """Servers can't start conversations, so everyday results carry the next check-in for the agent to ask.

    `prefer` puts a kind first (the DST plan surfaces the Daylight Saving check-in).
    """
    h = home()
    h.refresh_checkins()
    pending = sorted(h.pending_checkins(), key=lambda c: c.kind.value != prefer)
    if pending:
        first = pending[0]
        result["pending_checkins"] = len(pending)
        result["next_checkin"] = {"id": first.id, "kind": first.kind.value, "question": first.question}
        result["checkin_hint"] = (
            "Ask next_checkin now, in your own words. Pass the user's answer to respond_to_checkin with its id."
        )
    return result


@mcp.tool(title="Get pending check-ins", annotations=READ_ONLY, meta=cards.card_meta("get_checkins"))
def get_checkins() -> dict[str, Any]:
    """Questions On Time wants to ask the user, each with its evidence and the change it proposes.

    Ask one at a time, conversationally, and pass the answer to respond_to_checkin.
    """
    home().refresh_checkins()
    return {
        "checkins": [
            {
                "id": c.id,
                "kind": c.kind.value,
                "question": c.question,
                "evidence": c.evidence,
                "proposal": c.proposal,
                "routine_id": c.routine_id,
            }
            for c in home().pending_checkins()
        ]
    }


@mcp.tool(title="Answer a check-in", annotations=WRITE)
@user_errors
def respond_to_checkin(
    checkin_id: Annotated[str, Field(description="Check-in id from get_checkins")],
    response: Annotated[
        Literal["accept", "adjust", "decline"],
        Field(description="accept the proposal, adjust it with your own minutes, or decline (not asked again for 14 days)"),
    ],
    minutes: Annotated[
        int | None, Field(ge=-180, le=180, description="With 'adjust': the user's value in minutes (negative = earlier)")
    ] = None,
    note: Annotated[str | None, Field(description="What the user said, for the record")] = None,
) -> dict[str, Any]:
    """Record the user's answer to a check-in. Accepting or adjusting changes the routine; declining changes nothing.

    The result's `summary` states the outcome exactly; repeat it rather than calculating times.
    """
    h = home()
    checkin = h.respond_to_checkin(checkin_id, response, minutes, note)
    result: dict[str, Any] = {"checkin_id": checkin.id, "status": checkin.status.value}
    if checkin.status == "declined":
        result["summary"] = f"Nothing changed. On Time won't ask about this again until {checkin.muted_until:%B} {checkin.muted_until.day}."
        result["not_asked_again_until"] = checkin.muted_until.date().isoformat()
    elif checkin.routine_id:
        routine = h.get_routine(checkin.routine_id)
        result["routine"] = routine.model_dump(mode="json")
        result["summary"] = _routine_summary(routine)
    else:  # Daylight Saving glide
        steps = ", ".join(f"{d.removeprefix('2026-')} {m:+d} min" for d, m in checkin.proposal.get("plan", []))
        result["summary"] = f"Daylight Saving glide accepted: clock-based routines shift {steps}, then land on the new clock."
    return _with_checkins(result)


def _routine_summary(routine: Any) -> str:
    """'Weekday evening now comes on at 6:10 PM (clock 18:30; -20 min you confirmed).'"""
    event = home().explain(routine.id, home().today())
    verb = "comes on" if routine.action.value == "on" else "turns off"
    when = event.at.strftime("%I:%M %p").lstrip("0")
    reason = event.explain().split("(", 1)[-1].rstrip(")")
    if isinstance(routine.anchor, SolarAnchor):
        return f"{routine.name} now follows {routine.anchor.event}: today it {verb} at {when} ({reason})."
    return f"{routine.name} now {verb} at {when} ({reason})."


# -- demo ----------------------------------------------------------------------


def demo_mode() -> bool:
    return os.environ.get("ON_TIME_DEMO", "").lower() in ("1", "true", "yes")


@mcp.tool(title="Demo: set the clock", annotations=WRITE)
@user_errors
def set_demo_clock(
    at: Annotated[
        str | None, Field(description="Local date-time 'YYYY-MM-DDTHH:MM' in the home timezone, e.g. '2026-10-31T19:00'")
    ] = None,
    reset: Annotated[bool, Field(description="Return to the real time")] = False,
) -> dict[str, Any]:
    """DEMO ONLY: move On Time's clock to another moment (e.g. the week before a DST change). Disabled unless demo mode is on."""
    if not demo_mode():
        raise ToolError("The demo clock is only available when the server runs in demo mode (ON_TIME_DEMO=1).")
    h = home()
    location = h.get_location()
    tz = location.tz if location else None
    if reset:
        h.clock.reset()
    elif at is not None:
        try:
            moment = datetime.fromisoformat(at)
        except ValueError as e:
            raise ToolError(f"`at` must look like '2026-10-31T19:00', got {at!r}.") from e
        if moment.tzinfo is None:
            if tz is None:
                raise ToolError("Set the home location first, or include a UTC offset in `at`.")
            moment = moment.replace(tzinfo=tz)
        h.clock.travel_to(moment)
    else:
        raise ToolError("Give `at`, or reset=true.")
    return {"now": h.clock.now(tz).replace(microsecond=0).isoformat(), "demo_clock": h.clock.is_overridden}


# -- web client (static files; public, no auth; MCP calls from the page carry the token) ----

WEB_DIR = Path(__file__).parent / "web"
WEB_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}


@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def web_index(request: Request) -> Response:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@mcp.custom_route("/web/{name}", methods=["GET"], include_in_schema=False)
async def web_asset(request: Request) -> Response:
    name = request.path_params["name"]
    path = WEB_DIR / name
    # Flat directory, known types only: no path traversal.
    if "/" in name or "\\" in name or path.suffix not in WEB_TYPES or not path.is_file():
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(path, media_type=WEB_TYPES[path.suffix], headers={"Cache-Control": "no-cache"})


# -- conversational agent for the web client (not an MCP tool; the browser makes the tool calls) --

MAX_AGENT_MESSAGES = 40


@mcp.custom_route("/agent", methods=["POST"], include_in_schema=False)
async def agent_route(request: Request) -> Response:
    try:
        body = await request.json()
        messages = body["messages"][-MAX_AGENT_MESSAGES:]
        tools = body.get("tools", [])
        step = agent.next_step(messages, tools, mode=os.environ.get("ON_TIME_AGENT", "mock"))
    except (ValueError, KeyError, TypeError) as e:
        return JSONResponse({"error": f"Bad agent request: {e}"}, status_code=400)
    return JSONResponse(step)


# -- HTTP app ------------------------------------------------------------------


def transport_security(host: str, port: int) -> TransportSecuritySettings:
    """DNS-rebinding protection: only local hosts/origins plus any listed in ON_TIME_ALLOWED_ORIGINS."""
    extra = [o.strip() for o in os.environ.get("ON_TIME_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", f"{host}:{port}"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", *extra],
    )


def create_app(host: str = "127.0.0.1", port: int = 8000, api_token: str | None = None) -> ASGIApp:
    """Build the ASGI app: single /mcp endpoint, stateless, JSON responses, bearer-token auth.

    The token defaults to ON_TIME_API_TOKEN. Without one the server refuses to bind anywhere but
    loopback, so it can't be exposed unauthenticated.
    """
    home()
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(host, port),
        host=host,
    )
    token = api_token if api_token is not None else os.environ.get("ON_TIME_API_TOKEN", "")
    if token:
        return BearerTokenMiddleware(app, token)
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError(f"Refusing to serve on {host} without ON_TIME_API_TOKEN set.")
    logger.warning("ON_TIME_API_TOKEN is not set: authentication is OFF (local development only).")
    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("ON_TIME_HOST", "127.0.0.1")
    port = int(os.environ.get("ON_TIME_PORT", "8000"))
    uvicorn.run(create_app(host, port), host=host, port=port)
