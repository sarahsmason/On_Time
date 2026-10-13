from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from on_time.models import ClockAnchor, LightAction, Location, LocationSource, Routine, SolarAnchor
from on_time.schedule import (
    ScheduleError,
    next_dst_transition,
    schedule_day,
    schedule_routine,
    solar_time,
)

NY = ZoneInfo("America/New_York")
OSWEGO = Location(name="Oswego, NY", latitude=43.4553, longitude=-76.5105, timezone="America/New_York", source="user")

# Reference values from api.sunrise-sunset.org for Oswego, NY (fetched 2026-10-05).
REFERENCE = {
    ("sunset", date(2026, 10, 6)): datetime(2026, 10, 6, 18, 39, 58, tzinfo=NY),
    ("sunrise", date(2026, 10, 31)): datetime(2026, 10, 31, 7, 38, 58, tzinfo=NY),
    ("sunset", date(2026, 10, 31)): datetime(2026, 10, 31, 18, 0, 18, tzinfo=NY),
    ("sunrise", date(2026, 11, 1)): datetime(2026, 11, 1, 6, 40, 16, tzinfo=NY),
    ("sunset", date(2026, 11, 1)): datetime(2026, 11, 1, 16, 58, 57, tzinfo=NY),
}


def routine(anchor, **kw) -> Routine:
    return Routine(name=kw.pop("name", "R"), light_ids=["porch"], action=LightAction.ON, anchor=anchor, **kw)


@pytest.mark.parametrize(("event", "day"), REFERENCE)
def test_solar_times_match_reference_within_ninety_seconds(event, day):
    assert abs(solar_time(event, day, OSWEGO) - REFERENCE[(event, day)]) < timedelta(seconds=90)


def test_sunset_jumps_an_hour_on_the_clock_across_fall_back():
    before = solar_time("sunset", date(2026, 10, 31), OSWEGO)
    after = solar_time("sunset", date(2026, 11, 1), OSWEGO)
    assert (before.hour, after.hour) == (18, 16)  # 6:00 PM EDT -> 4:58 PM EST


def test_solar_routine_with_offset():
    ev = schedule_routine(routine(SolarAnchor(event="sunset", offset_minutes=-20)), date(2026, 10, 6), OSWEGO, NY)
    assert ev.at.strftime("%H:%M") == "18:20"
    assert ev.base_detail == "sunset 18:40 − 20 min"


def test_clock_routine_keeps_wall_time_on_dst_day():
    ev = schedule_routine(routine(ClockAnchor(at=time(6, 30))), date(2026, 11, 1), OSWEGO, NY)
    assert ev.at == datetime(2026, 11, 1, 6, 30, tzinfo=NY)
    assert ev.at.utcoffset() == timedelta(hours=-5)


def test_learned_offset_applies_and_is_explained():
    ev = schedule_routine(
        routine(ClockAnchor(at=time(18, 30)), learned_offset_minutes=-20), date(2026, 10, 6), OSWEGO, NY
    )
    assert ev.at.strftime("%H:%M") == "18:10"
    assert ev.explain() == "porch on at 18:10 (clock 18:30; -20 min you confirmed)"


def test_schedule_day_filters_weekdays_sorts_and_reports_problems():
    weekday_evening = routine(ClockAnchor(at=time(18, 30)), name="Evening", days=[0, 1, 2, 3, 4])
    morning = routine(ClockAnchor(at=time(6, 30)), name="Morning")
    dusk = routine(SolarAnchor(event="sunset"), name="Dusk")

    events, problems = schedule_day([weekday_evening, morning, dusk], date(2026, 10, 6), OSWEGO, NY)  # Tuesday
    assert [e.routine.name for e in events] == ["Morning", "Evening", "Dusk"]
    assert problems == []

    events, _ = schedule_day([weekday_evening, morning], date(2026, 10, 4), OSWEGO, NY)  # Sunday
    assert [e.routine.name for e in events] == ["Morning"]

    events, problems = schedule_day([dusk, morning], date(2026, 10, 6), None, NY)
    assert [e.routine.name for e in events] == ["Morning"]
    assert "set the home location" in problems[0]


def test_polar_night_is_a_schedule_error():
    tromso = Location(name="Tromsø", latitude=69.65, longitude=18.96, timezone="Europe/Oslo", source="user")
    with pytest.raises(ScheduleError, match="no sunrise"):
        solar_time("sunrise", date(2026, 12, 21), tromso)


# -- DST transitions -------------------------------------------------------------


def test_next_fall_back_2026():
    change = next_dst_transition(NY, datetime(2026, 10, 6, 12, 0, tzinfo=NY))
    assert change.direction == "fall_back"
    assert change.shift_minutes == 60
    assert change.at == datetime(2026, 11, 1, 6, 0, tzinfo=ZoneInfo("UTC"))  # 2:00 EDT -> 1:00 EST
    assert change.day == date(2026, 11, 1)


def test_next_spring_forward_2027():
    change = next_dst_transition(NY, datetime(2026, 11, 2, tzinfo=NY))
    assert change.direction == "spring_forward"
    assert change.shift_minutes == -60
    assert change.at == datetime(2027, 3, 14, 7, 0, tzinfo=ZoneInfo("UTC"))  # 2:00 EST -> 3:00 EDT
    assert change.at.astimezone(NY).hour == 3


def test_no_dst_in_phoenix():
    assert next_dst_transition(ZoneInfo("America/Phoenix"), datetime(2026, 10, 6, tzinfo=ZoneInfo("UTC"))) is None


def test_fall_back_glide_plan_is_four_days_of_fifteen_minutes():
    change = next_dst_transition(NY, datetime(2026, 10, 6, tzinfo=NY))
    assert change.glide_plan() == [
        (date(2026, 10, 28), 15),
        (date(2026, 10, 29), 30),
        (date(2026, 10, 30), 45),
        (date(2026, 10, 31), 60),
    ]


def test_spring_glide_plan_steps_earlier():
    change = next_dst_transition(NY, datetime(2026, 11, 2, tzinfo=NY))
    assert [m for _, m in change.glide_plan()] == [-15, -30, -45, -60]
    assert change.glide_plan()[-1][0] == date(2027, 3, 13)
