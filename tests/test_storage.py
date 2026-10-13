from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from on_time.models import (
    Adjustment,
    CheckIn,
    CheckInKind,
    CheckInStatus,
    ClockAnchor,
    Light,
    LightAction,
    Location,
    LocationSource,
    Routine,
)
from on_time.storage import SqliteRepository

NY = ZoneInfo("America/New_York")


@pytest.fixture
def repo():
    r = SqliteRepository()
    yield r
    r.close()


def test_location_round_trip(repo):
    assert repo.get_location() is None
    loc = Location(name="Oswego, NY", latitude=43.455, longitude=-76.51, timezone="America/New_York", source=LocationSource.USER)
    repo.set_location(loc)
    assert repo.get_location() == loc


def test_lights_upsert_and_list(repo):
    repo.upsert_light(Light(id="porch", name="Porch"))
    repo.upsert_light(Light(id="bedroom", name="Bedroom"))
    repo.upsert_light(Light(id="porch", name="Porch", is_on=True))
    assert [l.id for l in repo.list_lights()] == ["bedroom", "porch"]
    assert repo.get_light("porch").is_on is True
    assert repo.get_light("garage") is None


def test_routines_crud(repo):
    rt = Routine(name="Wake", light_ids=["bedroom"], action=LightAction.ON, anchor=ClockAnchor(at=time(6, 30)))
    repo.upsert_routine(rt)
    assert repo.get_routine(rt.id) == rt
    repo.upsert_routine(rt.model_copy(update={"learned_offset_minutes": 15}))
    assert repo.get_routine(rt.id).learned_offset_minutes == 15
    assert repo.delete_routine(rt.id) is True
    assert repo.delete_routine(rt.id) is False
    assert repo.list_routines() == []


def test_adjustments_filter_by_light_routine_and_time(repo):
    base = datetime(2026, 10, 1, 18, 0, tzinfo=NY)
    for day in range(5):
        repo.add_adjustment(
            Adjustment(light_id="living_room", action=LightAction.ON, at=base + timedelta(days=day), routine_id="rt_eve")
        )
    repo.add_adjustment(Adjustment(light_id="porch", action=LightAction.ON, at=base))

    assert len(repo.list_adjustments()) == 6
    assert len(repo.list_adjustments(light_id="living_room")) == 5
    assert len(repo.list_adjustments(routine_id="rt_eve")) == 5
    recent = repo.list_adjustments(light_id="living_room", since=base + timedelta(days=3))
    assert [a.at.day for a in recent] == [4, 5]


def test_adjustments_since_compares_across_timezones(repo):
    repo.add_adjustment(Adjustment(light_id="a", action=LightAction.ON, at=datetime(2026, 10, 1, 22, 0, tzinfo=NY)))
    # 22:00 New York == 02:00 UTC next day
    assert len(repo.list_adjustments(since=datetime(2026, 10, 2, 1, 59, tzinfo=ZoneInfo("UTC")))) == 1
    assert len(repo.list_adjustments(since=datetime(2026, 10, 2, 2, 1, tzinfo=ZoneInfo("UTC")))) == 0


def test_checkins_by_status(repo):
    now = datetime(2026, 10, 5, 9, 0, tzinfo=NY)
    pending = CheckIn(kind=CheckInKind.LEARNED_OFFSET, question="Move it?", created_at=now)
    repo.upsert_checkin(pending)
    repo.upsert_checkin(CheckIn(kind=CheckInKind.DST_GLIDE, question="Glide?", created_at=now, status=CheckInStatus.DECLINED))

    assert [c.id for c in repo.list_checkins(status=CheckInStatus.PENDING)] == [pending.id]
    assert len(repo.list_checkins()) == 2

    repo.upsert_checkin(pending.model_copy(update={"status": CheckInStatus.ACCEPTED}))
    assert repo.list_checkins(status=CheckInStatus.PENDING) == []
    assert repo.get_checkin(pending.id).status == CheckInStatus.ACCEPTED


def test_settings(repo):
    assert repo.get_setting("x") is None
    repo.set_setting("x", "1")
    assert repo.get_setting("x") == "1"
    repo.set_setting("x", None)
    assert repo.get_setting("x") is None


def test_data_persists_in_file(tmp_path):
    path = tmp_path / "on_time.db"
    first = SqliteRepository(path)
    first.upsert_light(Light(id="porch", name="Porch"))
    first.close()
    second = SqliteRepository(path)
    assert second.get_light("porch").name == "Porch"
    second.close()
