from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from on_time.models import (
    Adjustment,
    CheckIn,
    CheckInKind,
    ClockAnchor,
    LightAction,
    Location,
    LocationSource,
    Routine,
    SolarAnchor,
)

NY = ZoneInfo("America/New_York")


def test_location_rejects_unknown_timezone():
    with pytest.raises(ValidationError):
        Location(name="X", latitude=0, longitude=0, timezone="Mars/Olympus", source=LocationSource.USER)


def test_location_rejects_out_of_range_coordinates():
    with pytest.raises(ValidationError):
        Location(name="X", latitude=91, longitude=0, timezone="UTC", source=LocationSource.USER)


def test_routine_anchor_round_trips_through_json():
    clock = Routine(name="Wake", light_ids=["bedroom"], action=LightAction.ON, anchor=ClockAnchor(at=time(6, 30)))
    solar = Routine(
        name="Porch",
        light_ids=["porch"],
        action=LightAction.ON,
        anchor=SolarAnchor(event="sunset", offset_minutes=-20),
    )
    for routine in (clock, solar):
        assert Routine.model_validate_json(routine.model_dump_json()) == routine
    assert isinstance(Routine.model_validate_json(solar.model_dump_json()).anchor, SolarAnchor)


def test_routine_needs_a_light_and_valid_days():
    with pytest.raises(ValidationError):
        Routine(name="X", light_ids=[], action=LightAction.ON, anchor=ClockAnchor(at=time(7)))
    with pytest.raises(ValidationError):
        Routine(name="X", light_ids=["a"], action=LightAction.ON, anchor=ClockAnchor(at=time(7)), days=[7])


def test_routine_days_are_normalised_and_checked():
    weekdays = Routine(
        name="X", light_ids=["a"], action=LightAction.ON, anchor=ClockAnchor(at=time(7)), days=[4, 0, 0, 2]
    )
    assert weekdays.days == [0, 2, 4]
    assert weekdays.runs_on(datetime(2026, 10, 5, tzinfo=NY))  # Monday
    assert not weekdays.runs_on(datetime(2026, 10, 4, tzinfo=NY))  # Sunday


def test_adjustment_delta_minutes():
    adj = Adjustment(
        light_id="living_room",
        action=LightAction.ON,
        at=datetime(2026, 10, 5, 18, 10, tzinfo=NY),
        scheduled_at=datetime(2026, 10, 5, 18, 30, tzinfo=NY),
    )
    assert adj.delta_minutes == -20
    assert Adjustment(light_id="a", action=LightAction.OFF, at=datetime(2026, 10, 5, tzinfo=NY)).delta_minutes is None


def test_adjustment_requires_timezone_aware_time():
    with pytest.raises(ValidationError):
        Adjustment(light_id="a", action=LightAction.ON, at=datetime(2026, 10, 5, 18, 0))


def test_checkin_defaults_to_pending():
    ci = CheckIn(kind=CheckInKind.DST_GLIDE, question="Ease lights for DST?", created_at=datetime(2026, 10, 27, tzinfo=NY))
    assert ci.status == "pending"
    assert ci.id.startswith("ci_")
