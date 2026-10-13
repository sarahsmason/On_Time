"""Time source for On_Time, with a demo override ("time travel").

The demo override is stored as an offset from real time in the repository, so it applies to
every request of a stateless server and time keeps moving forward from the chosen moment.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from on_time.storage import Repository

_OFFSET_KEY = "demo_clock_offset_seconds"


def _real_now() -> datetime:
    return datetime.now(UTC)


class Clock:
    def __init__(self, repo: Repository, real_now: Callable[[], datetime] = _real_now) -> None:
        self._repo = repo
        self._real_now = real_now

    @property
    def offset(self) -> timedelta:
        raw = self._repo.get_setting(_OFFSET_KEY)
        return timedelta(seconds=float(raw)) if raw else timedelta(0)

    @property
    def is_overridden(self) -> bool:
        return self.offset != timedelta(0)

    def now(self, tz: ZoneInfo | None = None) -> datetime:
        """Current (possibly demo-shifted) time, aware, in `tz` (default UTC)."""
        return (self._real_now() + self.offset).astimezone(tz or UTC)

    def travel_to(self, moment: datetime) -> None:
        """Make `now()` read as `moment` from this instant on. `moment` must be timezone-aware."""
        if moment.tzinfo is None:
            raise ValueError("moment must be timezone-aware")
        delta = moment - self._real_now()
        self._repo.set_setting(_OFFSET_KEY, str(delta.total_seconds()))

    def reset(self) -> None:
        self._repo.set_setting(_OFFSET_KEY, None)
