import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from on_time import server
from on_time.home import ACTIVE_GLIDE_KEY
from on_time.models import CheckIn, CheckInKind

NY = ZoneInfo("America/New_York")
START = datetime(2026, 10, 7, 9, 0, tzinfo=NY)


@pytest.fixture
def home(fresh_home):
    server.set_location(city="Oswego", region="NY")
    fresh_home.clock.travel_to(START)
    return fresh_home


@pytest.fixture
def evening(home):
    return server.add_routine(name="Evening", light_ids=["living_room"], action="on", at="18:30")["routine"]


def offset_checkin(routine_id: str, minutes: int = -20) -> CheckIn:
    return CheckIn(
        kind=CheckInKind.LEARNED_OFFSET,
        routine_id=routine_id,
        question="You've been turning the living room on about 20 minutes early. Move the routine to 6:10?",
        evidence=["5 of the last 7 evenings, 15-25 min before 18:30"],
        proposal={"learned_offset_minutes": minutes},
        created_at=START,
    )


def test_no_checkins_initially(home):
    assert server.get_checkins() == {"checkins": []}
    assert "pending_checkins" not in server.list_lights()


def test_pending_checkin_is_surfaced_in_everyday_results(home, evening):
    home.raise_checkin(offset_checkin(evening["id"]))
    lights = server.list_lights()
    assert lights["pending_checkins"] == 1
    assert lights["next_checkin"]["kind"] == "learned_offset"
    assert lights["next_checkin"]["question"].startswith("You've been turning the living room on")
    assert "respond_to_checkin" in lights["checkin_hint"]
    assert server.get_schedule()["pending_checkins"] == 1
    [ci] = server.get_checkins()["checkins"]
    assert ci["evidence"] == ["5 of the last 7 evenings, 15-25 min before 18:30"]


def test_accept_applies_learned_offset(home, evening):
    ci = home.raise_checkin(offset_checkin(evening["id"]))
    result = server.respond_to_checkin(checkin_id=ci.id, response="accept", note="yes please")
    assert result["status"] == "accepted"
    assert result["routine"]["learned_offset_minutes"] == -20
    assert result["summary"] == "Evening now comes on at 6:10 PM (clock 18:30; -20 min you confirmed)."

    [event] = server.get_schedule()["events"]
    assert event["at"][11:16] == "18:10"
    assert event["explanation"] == "living_room on at 18:10 (clock 18:30; -20 min you confirmed)"
    assert server.get_checkins() == {"checkins": []}


def test_adjust_uses_the_users_minutes(home, evening):
    ci = home.raise_checkin(offset_checkin(evening["id"]))
    result = server.respond_to_checkin(checkin_id=ci.id, response="adjust", minutes=-10)
    assert result["status"] == "adjusted"
    assert result["routine"]["learned_offset_minutes"] == -10


def test_adjust_needs_minutes(home, evening):
    ci = home.raise_checkin(offset_checkin(evening["id"]))
    with pytest.raises(ToolError, match="how many minutes"):
        server.respond_to_checkin(checkin_id=ci.id, response="adjust")


def test_decline_changes_nothing_and_mutes_for_14_days(home, evening):
    ci = home.raise_checkin(offset_checkin(evening["id"]))
    result = server.respond_to_checkin(checkin_id=ci.id, response="decline")
    assert result == {
        "checkin_id": ci.id,
        "status": "declined",
        "summary": "Nothing changed. On Time won't ask about this again until October 21.",
        "not_asked_again_until": "2026-10-21",
    }
    assert server.list_routines()["routines"][0]["learned_offset_minutes"] == 0

    assert home.raise_checkin(offset_checkin(evening["id"])) is None  # muted
    home.clock.travel_to(START + timedelta(days=15))
    assert home.raise_checkin(offset_checkin(evening["id"])) is not None


def test_duplicate_pending_checkin_not_raised_twice(home, evening):
    assert home.raise_checkin(offset_checkin(evening["id"])) is not None
    assert home.raise_checkin(offset_checkin(evening["id"], minutes=-25)) is None
    assert len(server.get_checkins()["checkins"]) == 1


def test_answering_twice_is_an_error(home, evening):
    ci = home.raise_checkin(offset_checkin(evening["id"]))
    server.respond_to_checkin(checkin_id=ci.id, response="accept")
    with pytest.raises(ToolError, match="already answered"):
        server.respond_to_checkin(checkin_id=ci.id, response="decline")


def test_unknown_checkin(home):
    with pytest.raises(ToolError, match="Unknown check-in"):
        server.respond_to_checkin(checkin_id="ci_nope", response="accept")


def test_anchor_switch_to_follow_sunset(home):
    porch = server.add_routine(name="Porch", light_ids=["porch"], action="on", at="18:30")["routine"]
    ci = home.raise_checkin(
        CheckIn(
            kind=CheckInKind.ANCHOR_SWITCH,
            routine_id=porch["id"],
            question="Your porch on-times follow sunset. Should the porch light follow sunset instead of 6:30?",
            proposal={"anchor": {"kind": "solar", "event": "sunset", "offset_minutes": -10}},
            created_at=START,
        )
    )
    server.respond_to_checkin(checkin_id=ci.id, response="adjust", minutes=-15)
    explained = server.explain_light_time(routine_id=porch["id"], day="2026-10-07")
    assert explained["factors"]["base"]["detail"].startswith("sunset 18:3")
    assert explained["factors"]["base"]["detail"].endswith("− 15 min")


def test_dst_glide_accept_stores_plan_and_cannot_be_adjusted(home, evening):
    proposal = {"transition_at": "2026-11-01T06:00:00+00:00", "routine_ids": [evening["id"]]}

    def glide_checkin():
        return CheckIn(kind=CheckInKind.DST_GLIDE, question="Ease lights?", proposal=proposal, created_at=START)

    first = home.raise_checkin(glide_checkin())
    with pytest.raises(ToolError, match="not adjusted"):
        server.respond_to_checkin(checkin_id=first.id, response="adjust", minutes=10)
    server.respond_to_checkin(checkin_id=first.id, response="accept")
    assert json.loads(home.repo.get_setting(ACTIVE_GLIDE_KEY)) == proposal


def test_invalid_adjustment_is_readable(home, evening):
    ci = home.raise_checkin(offset_checkin(evening["id"]))
    with pytest.raises(ToolError):
        server.respond_to_checkin(checkin_id=ci.id, response="adjust", minutes=500)


# -- demo clock -------------------------------------------------------------------------


def test_demo_clock_disabled_by_default(home, monkeypatch):
    monkeypatch.delenv("ON_TIME_DEMO", raising=False)
    with pytest.raises(ToolError, match="demo mode"):
        server.set_demo_clock(at="2026-10-31T19:00")


def test_demo_clock_travel_and_reset(home, monkeypatch):
    monkeypatch.setenv("ON_TIME_DEMO", "1")
    result = server.set_demo_clock(at="2026-10-31T19:00")
    assert result["now"].startswith("2026-10-31T19:00")
    assert result["now"].endswith("-04:00")
    assert server.get_schedule()["day"] == "2026-10-31"

    assert server.set_demo_clock(reset=True)["demo_clock"] is False


def test_demo_clock_rejects_bad_input(home, monkeypatch):
    monkeypatch.setenv("ON_TIME_DEMO", "1")
    with pytest.raises(ToolError, match="2026-10-31T19:00"):
        server.set_demo_clock(at="Halloween")
    with pytest.raises(ToolError, match="reset"):
        server.set_demo_clock()
