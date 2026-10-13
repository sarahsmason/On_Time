from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from on_time import server

NY = ZoneInfo("America/New_York")


@pytest.fixture
def oswego(fresh_home):
    server.set_location(city="Oswego", region="NY")
    fresh_home.clock.travel_to(datetime(2026, 10, 6, 9, 0, tzinfo=NY))  # Tuesday morning
    return fresh_home


def test_schedule_needs_location():
    with pytest.raises(ToolError, match="Set the home location first"):
        server.get_schedule()


def test_add_routine_requires_exactly_one_anchor(oswego):
    with pytest.raises(ToolError, match="exactly one"):
        server.add_routine(name="X", light_ids=["porch"], action="on")
    with pytest.raises(ToolError, match="exactly one"):
        server.add_routine(name="X", light_ids=["porch"], action="on", at="18:00", solar_event="sunset")
    with pytest.raises(ToolError, match="24-hour time"):
        server.add_routine(name="X", light_ids=["porch"], action="on", at="6pm")
    with pytest.raises(ToolError, match="Unknown light"):
        server.add_routine(name="X", light_ids=["garage"], action="on", at="18:00")


def test_schedule_today_with_clock_and_solar_routines(oswego):
    server.add_routine(name="Wake", light_ids=["bedroom"], action="on", at="06:30")
    server.add_routine(name="Porch at dusk", light_ids=["porch"], action="on", solar_event="sunset", offset_minutes=-20)
    server.add_routine(name="Weekday evening", light_ids=["living_room"], action="on", at="18:30", days=[0, 1, 2, 3, 4])

    result = server.get_schedule()
    assert result["day"] == "2026-10-06"
    assert [(e["name"], e["at"][11:16]) for e in result["events"]] == [
        ("Wake", "06:30"),
        ("Porch at dusk", "18:20"),
        ("Weekday evening", "18:30"),
    ]
    assert result["problems"] == []

    sunday = server.get_schedule(day="2026-10-04")
    assert "Weekday evening" not in [e["name"] for e in sunday["events"]]


def test_explain_light_time(oswego):
    rt = server.add_routine(name="Porch at dusk", light_ids=["porch"], action="on", solar_event="sunset", offset_minutes=-20)
    explained = server.explain_light_time(routine_id=rt["routine"]["id"], day="2026-10-31")
    assert explained["at"].startswith("2026-10-31T17:40")
    assert explained["factors"]["base"]["detail"] == "sunset 18:00 − 20 min"
    assert explained["factors"]["dst_glide_minutes"] == 0
    assert explained["explanation"] == "porch on at 17:40 (sunset 18:00 − 20 min)"


def test_explain_unknown_routine_lists_routines(oswego):
    server.add_routine(name="Wake", light_ids=["bedroom"], action="on", at="06:30")
    with pytest.raises(ToolError, match=r"Routines: rt_\w+ \(Wake\)"):
        server.explain_light_time(routine_id="nope")


def test_bad_date_is_readable(oswego):
    with pytest.raises(ToolError, match="2026-10-31"):
        server.get_schedule(day="Oct 31")


def test_plan_dst_transition(oswego):
    server.add_routine(name="Wake", light_ids=["bedroom"], action="on", at="06:30")
    server.add_routine(name="Porch at dusk", light_ids=["porch"], action="on", solar_event="sunset")
    plan = server.plan_dst_transition()
    assert plan["next_change"]["direction"] == "fall_back"
    assert plan["next_change"]["summary"] == "Clocks go back 1 hour on Sunday, November 1"
    assert plan["glide_preview"] == [
        {"day": "2026-10-28", "shift_minutes": 15},
        {"day": "2026-10-29", "shift_minutes": 30},
        {"day": "2026-10-30", "shift_minutes": 45},
        {"day": "2026-10-31", "shift_minutes": 60},
    ]
    assert [r["name"] for r in plan["routines_that_would_glide"]] == ["Wake"]
    assert plan["glide_summary"].startswith("Clock-based routines move 15 minutes LATER each day (Wed +15 min")
    assert [r["name"] for r in plan["routines_following_the_sun"]] == ["Porch at dusk"]


def test_plan_dst_in_zone_without_dst(fresh_home):
    server.set_location(city="Phoenix", region="AZ")
    assert server.plan_dst_transition()["next_change"] is None


def test_list_routines_includes_todays_actual_time(oswego):
    server.add_routine(name="Wake", light_ids=["bedroom"], action="on", at="06:30")
    server.add_routine(name="Weekend lamp", light_ids=["kitchen"], action="on", at="08:00", days=[5, 6])
    routines = {r["name"]: r for r in server.list_routines()["routines"]}
    assert routines["Wake"]["today"]["at"].startswith("2026-10-06T06:30")
    assert routines["Wake"]["today"]["explanation"] == "bedroom on at 06:30 (clock 06:30)"
    assert routines["Weekend lamp"]["today"] == "not scheduled today"  # Tuesday


def test_remove_routine(oswego):
    rt = server.add_routine(name="Wake", light_ids=["bedroom"], action="on", at="06:30")["routine"]
    assert server.remove_routine(routine_id=rt["id"])["removed"]["name"] == "Wake"
    assert server.list_routines() == {"routines": []}


# -- manual changes are matched to the routine they deviate from ---------------------


def test_set_light_records_deviation_from_routine(oswego):
    rt = server.add_routine(name="Evening", light_ids=["living_room"], action="on", at="18:30")["routine"]
    oswego.clock.travel_to(datetime(2026, 10, 6, 18, 10, tzinfo=NY))
    server.set_light(light_id="living_room", action="on")

    [adj] = oswego.repo.list_adjustments(light_id="living_room")
    assert adj.routine_id == rt["id"]
    assert adj.scheduled_at == datetime(2026, 10, 6, 18, 30, tzinfo=NY)
    assert adj.delta_minutes == -20


def test_set_light_far_from_any_routine_is_unmatched(oswego):
    server.add_routine(name="Evening", light_ids=["living_room"], action="on", at="18:30")
    oswego.clock.travel_to(datetime(2026, 10, 6, 13, 0, tzinfo=NY))
    server.set_light(light_id="living_room", action="on")
    [adj] = oswego.repo.list_adjustments(light_id="living_room")
    assert adj.routine_id is None and adj.delta_minutes is None


def test_set_light_only_matches_same_action(oswego):
    server.add_routine(name="Evening", light_ids=["living_room"], action="on", at="18:30")
    oswego.clock.travel_to(datetime(2026, 10, 6, 18, 25, tzinfo=NY))
    server.set_light(light_id="living_room", action="off")
    [adj] = oswego.repo.list_adjustments(light_id="living_room")
    assert adj.routine_id is None
