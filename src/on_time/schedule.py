"""Compute when each light routine runs. See the "How each light time is calculated" section of the README.

effective_time = base_time (clock, or sunrise/sunset at the home location)
               + dst_glide        (gradual shift before a DST change, once accepted in a check-in)
               + learned_offset   (confirmed in a check-in)
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import Observer, SunDirection
from astral.sun import time_at_elevation

from on_time.models import ClockAnchor, Location, Routine, SolarAnchor


class ScheduleError(ValueError):
    """A routine can't be scheduled (e.g. no location for a sunset routine, or polar day)."""


@dataclass(frozen=True)
class ScheduledEvent:
    routine: Routine
    at: datetime  # effective time, aware, home timezone
    base_at: datetime
    base_detail: str  # e.g. "clock 06:30" or "sunset 18:39 − 20 min"
    dst_glide_minutes: int
    learned_offset_minutes: int

    def explain(self) -> str:
        parts = [self.base_detail]
        if self.dst_glide_minutes:
            parts.append(f"{self.dst_glide_minutes:+d} min Daylight Saving glide")
        if self.learned_offset_minutes:
            parts.append(f"{self.learned_offset_minutes:+d} min you confirmed")
        lights = ", ".join(self.routine.light_ids)
        return f"{lights} {self.routine.action.value} at {self.at:%H:%M} ({'; '.join(parts)})"

    def to_dict(self) -> dict:
        return {
            "routine_id": self.routine.id,
            "name": self.routine.name,
            "light_ids": self.routine.light_ids,
            "action": self.routine.action.value,
            "at": self.at.isoformat(),
            "factors": {
                "base": {"at": self.base_at.isoformat(), "detail": self.base_detail},
                "dst_glide_minutes": self.dst_glide_minutes,
                "learned_offset_minutes": self.learned_offset_minutes,
            },
            "explanation": self.explain(),
        }


# Standard sunrise/sunset: sun's upper limb on the horizon, with atmospheric refraction
# (NOAA convention). astral's own sunrise()/sunset() run ~2 minutes off this for us.
SUN_ELEVATION_AT_HORIZON = -0.833


def solar_time(event: str, day: date, location: Location) -> datetime:
    observer = Observer(latitude=location.latitude, longitude=location.longitude)
    direction = SunDirection.RISING if event == "sunrise" else SunDirection.SETTING
    try:
        return time_at_elevation(observer, SUN_ELEVATION_AT_HORIZON, day, direction, tzinfo=location.tz)
    except ValueError as e:  # the sun doesn't rise or set that day (polar regions)
        raise ScheduleError(f"There is no {event} at {location.name} on {day}.") from e


def base_time(routine: Routine, day: date, location: Location | None, tz: ZoneInfo) -> tuple[datetime, str]:
    anchor = routine.anchor
    if isinstance(anchor, ClockAnchor):
        return datetime.combine(day, anchor.at, tzinfo=tz), f"clock {anchor.at:%H:%M}"
    assert isinstance(anchor, SolarAnchor)
    if location is None:
        raise ScheduleError(f"Routine {routine.name!r} follows {anchor.event}; set the home location first.")
    sun_at = solar_time(anchor.event, day, location)
    detail = f"{anchor.event} {sun_at:%H:%M}"
    if anchor.offset_minutes:
        sign = "+" if anchor.offset_minutes > 0 else "−"
        detail += f" {sign} {abs(anchor.offset_minutes)} min"
    return _shift(sun_at, anchor.offset_minutes, tz), detail


def _shift(moment: datetime, minutes: int, tz: ZoneInfo) -> datetime:
    """Move by elapsed minutes (in UTC, so it's correct across a DST change)."""
    return (moment.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(tz)


@dataclass(frozen=True)
class Glide:
    """An accepted Daylight Saving glide: per-day shifts for the listed clock-anchored routines."""

    routine_ids: frozenset[str]
    by_day: dict[date, int]

    @classmethod
    def from_proposal(cls, proposal: dict) -> "Glide":
        return cls(
            routine_ids=frozenset(proposal["routine_ids"]),
            by_day={date.fromisoformat(d): int(m) for d, m in proposal["plan"]},
        )

    def minutes(self, routine: Routine, day: date) -> int:
        # Solar routines already follow the sun; gliding them would double-shift.
        if routine.id not in self.routine_ids or not isinstance(routine.anchor, ClockAnchor):
            return 0
        return self.by_day.get(day, 0)


def schedule_routine(
    routine: Routine, day: date, location: Location | None, tz: ZoneInfo, glide: Glide | None = None
) -> ScheduledEvent:
    base_at, detail = base_time(routine, day, location, tz)
    glide_minutes = glide.minutes(routine, day) if glide else 0
    learned = routine.learned_offset_minutes
    at = _shift(base_at, glide_minutes + learned, tz)
    return ScheduledEvent(routine, at, base_at, detail, glide_minutes, learned)


def schedule_day(
    routines: list[Routine], day: date, location: Location | None, tz: ZoneInfo, glide: Glide | None = None
) -> tuple[list[ScheduledEvent], list[str]]:
    """All routines running on `day`, sorted by time, plus any that couldn't be scheduled (with reasons)."""
    events, problems = [], []
    weekday = day.weekday()
    for routine in routines:
        if not routine.enabled or weekday not in routine.days:
            continue
        try:
            events.append(schedule_routine(routine, day, location, tz, glide))
        except ScheduleError as e:
            problems.append(str(e))
    return sorted(events, key=lambda e: e.at), problems


# -- Daylight Saving Time ----------------------------------------------------------

GLIDE_DAYS = 4
GLIDE_STEP_MINUTES = 15


@dataclass(frozen=True)
class DstTransition:
    # Kept in UTC: around fall back the local wall time is ambiguous (1:00 happens twice),
    # and Python never treats an ambiguous local time as equal to another zone's instant.
    at: datetime  # first instant on the new offset, UTC
    tz: ZoneInfo
    direction: str  # "fall_back" or "spring_forward"
    shift_minutes: int  # +60 for fall back (clocks go back), -60 for spring forward

    @property
    def local_at(self) -> datetime:
        return self.at.astimezone(self.tz)

    @property
    def day(self) -> date:
        return self.local_at.date()

    def glide_plan(self) -> list[tuple[date, int]]:
        """Offsets for clock-anchored routines on the GLIDE_DAYS days before the change.

        Fall back: +15, +30, +45, +60 (old clock); by the eve the routine sits where the original
        time will be on the new clock. Spring forward mirrors it with negative steps.
        """
        sign = 1 if self.shift_minutes > 0 else -1
        return [
            (self.day - timedelta(days=GLIDE_DAYS - i), sign * GLIDE_STEP_MINUTES * (i + 1))
            for i in range(GLIDE_DAYS)
        ]


def next_dst_transition(tz: ZoneInfo, after: datetime, horizon_days: int = 400) -> DstTransition | None:
    """The next UTC-offset change in `tz` after `after`, or None if the zone has no DST.

    Arithmetic is done in UTC: adding a timedelta to a zone-aware datetime moves the wall clock,
    which is exactly what goes wrong around a DST change.
    """

    def offset(utc_moment: datetime) -> timedelta:
        return utc_moment.astimezone(tz).utcoffset()  # type: ignore[return-value]

    probe = after.astimezone(UTC)
    previous = offset(probe)
    for _ in range(horizon_days):
        nxt = probe + timedelta(days=1)
        if offset(nxt) != previous:
            lo, hi = probe, nxt  # narrow to the first minute on the new offset
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                if offset(mid) == previous:
                    lo = mid
                else:
                    hi = mid
            hi = hi.replace(second=0, microsecond=0)
            if offset(hi) == previous:
                hi += timedelta(minutes=1)
            delta = int((previous - offset(hi)).total_seconds() // 60)
            return DstTransition(
                at=hi,
                tz=tz,
                direction="fall_back" if delta > 0 else "spring_forward",
                shift_minutes=delta,
            )
        probe = nxt
    return None
