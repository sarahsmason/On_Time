from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from on_time.clock import Clock
from on_time.storage import SqliteRepository

NY = ZoneInfo("America/New_York")


class FakeTime:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def setup():
    repo = SqliteRepository()
    real = FakeTime(datetime(2026, 10, 4, 16, 0, tzinfo=UTC))
    yield Clock(repo, real_now=real), real, repo
    repo.close()


def test_real_time_by_default(setup):
    clock, real, _ = setup
    assert clock.now() == real.value
    assert not clock.is_overridden
    assert clock.now(NY).hour == 12  # 16:00 UTC is 12:00 EDT


def test_travel_to_and_time_keeps_moving(setup):
    clock, real, _ = setup
    target = datetime(2026, 10, 31, 20, 0, tzinfo=NY)  # eve of DST fall-back
    clock.travel_to(target)
    assert clock.is_overridden
    assert clock.now(NY) == target

    real.value += timedelta(minutes=5)
    assert clock.now(NY) == target + timedelta(minutes=5)


def test_override_is_shared_through_repository(setup):
    clock, real, repo = setup
    clock.travel_to(datetime(2026, 11, 2, 7, 0, tzinfo=NY))
    other = Clock(repo, real_now=real)  # e.g. another stateless request
    assert other.now(NY) == datetime(2026, 11, 2, 7, 0, tzinfo=NY)


def test_reset(setup):
    clock, real, _ = setup
    clock.travel_to(datetime(2027, 1, 1, tzinfo=UTC))
    clock.reset()
    assert clock.now() == real.value


def test_travel_requires_aware_datetime(setup):
    clock, _, _ = setup
    with pytest.raises(ValueError):
        clock.travel_to(datetime(2026, 11, 1, 2, 0))
