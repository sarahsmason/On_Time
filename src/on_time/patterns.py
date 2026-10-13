"""Find check-ins worth asking about. Pure functions: routines + manual changes in, CheckIns out.

Three detectors (see the "How each light time is calculated" section of the README):
  * learned offset: the user keeps overriding a routine by about the same amount
  * anchor switch:  the user's times track sunrise/sunset better than the routine's clock time
  * DST glide:      a clock change is coming up; offer to ease clock-based routines through it
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from statistics import median, pstdev

from on_time.models import Adjustment, CheckIn, CheckInKind, ClockAnchor, Location, Routine, SolarAnchor
from on_time.schedule import GLIDE_DAYS, DstTransition, ScheduleError, solar_time

LOOKBACK_DAYS = 21
MIN_SUPPORT = 4  # days with a matching manual change
MIN_RATIO = 0.6  # ... out of the days the routine ran
MIN_SHIFT_MINUTES = 10  # smaller habits aren't worth a question
MAX_SPREAD_MINUTES = 6  # standard deviation: the habit must be consistent
ROUND_TO = 5

ANCHOR_MIN_SUPPORT = 6
ANCHOR_MAX_SPREAD = 8  # user times vs the sun must be tight...
ANCHOR_SPREAD_GAIN = 2.0  # ...and at least this much tighter than vs the clock

DST_ASK_DAYS_BEFORE = 7  # start asking a week before the change


def _round(minutes: float) -> int:
    return int(ROUND_TO * round(minutes / ROUND_TO))


def friendly_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def _relative(minutes: int, event: str) -> str:
    if minutes == 0:
        return f"at {event}"
    return f"{abs(minutes)} minutes {'before' if minutes < 0 else 'after'} {event}"


def _light_names(routine: Routine) -> str:
    return _and_list([lid.replace("_", " ") for lid in routine.light_ids])


def _and_list(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _days_phrase(routine: Routine, at: time | None) -> str:
    part = "mornings" if at is not None and at.hour < 12 else "evenings" if at is not None and at.hour >= 17 else "days"
    if routine.days == [0, 1, 2, 3, 4]:
        return f"weekday {part}"
    if routine.days == [5, 6]:
        return f"weekend {part}"
    return part if len(routine.days) == 7 else f"scheduled {part}"


def _eligible_days(routine: Routine, start: date, end: date) -> int:
    days = (end - start).days + 1
    return sum(1 for i in range(max(days, 0)) if (start + timedelta(days=i)).weekday() in routine.days)


def _one_per_day(adjustments: Sequence[Adjustment]) -> list[Adjustment]:
    """Keep the first manual change per day, so a fiddly evening counts once."""
    seen: dict[date, Adjustment] = {}
    for a in sorted(adjustments, key=lambda a: a.at):
        seen.setdefault(a.at.date(), a)
    return list(seen.values())


# -- learned offset ----------------------------------------------------------------------


def learned_offset_checkin(
    routine: Routine, adjustments: Sequence[Adjustment], window_start: date, today: date, now: datetime
) -> CheckIn | None:
    matched = _one_per_day(
        [a for a in adjustments if a.routine_id == routine.id and a.action == routine.action and a.delta_minutes is not None]
    )
    eligible = _eligible_days(routine, window_start, today)
    if len(matched) < MIN_SUPPORT or eligible == 0 or len(matched) / eligible < MIN_RATIO:
        return None
    deltas = [a.delta_minutes for a in matched]  # type: ignore[misc]
    typical = median(deltas)
    if abs(typical) < MIN_SHIFT_MINUTES or pstdev(deltas) > MAX_SPREAD_MINUTES:
        return None

    shift = _round(typical)
    new_offset = routine.learned_offset_minutes + shift
    direction = "early" if shift < 0 else "late"
    lights = _light_names(routine)
    if isinstance(routine.anchor, ClockAnchor):
        base = datetime.combine(today, routine.anchor.at)
        target = friendly_time((base + timedelta(minutes=new_offset)).time())
        days = _days_phrase(routine, routine.anchor.at)
    else:
        target = _relative(routine.anchor.offset_minutes + new_offset, routine.anchor.event)
        days = _days_phrase(routine, None)

    return CheckIn(
        kind=CheckInKind.LEARNED_OFFSET,
        routine_id=routine.id,
        question=(
            f"You've turned the {lights} {routine.action.value} about {abs(shift)} minutes {direction} on "
            f"{len(matched)} of the last {eligible} {days}. "
            f"Want me to move your '{routine.name}' routine to {target}?"
        ),
        evidence=[
            f"{len(matched)} of {eligible} scheduled days since {window_start:%b %d} had a manual change",
            f"Typical change: {abs(typical):.0f} min {direction} (spread ±{pstdev(deltas):.0f} min)",
        ],
        proposal={"learned_offset_minutes": new_offset, "new_time": target},
        created_at=now,
    )


# -- anchor switch (clock -> sunrise/sunset) ------------------------------------------------


def anchor_switch_checkin(
    routine: Routine, adjustments: Sequence[Adjustment], location: Location, now: datetime
) -> CheckIn | None:
    if not isinstance(routine.anchor, ClockAnchor):
        return None
    matched = _one_per_day([a for a in adjustments if a.routine_id == routine.id and a.action == routine.action])
    if len(matched) < ANCHOR_MIN_SUPPORT:
        return None

    clock_minutes = [a.at.hour * 60 + a.at.minute for a in matched]
    clock_spread = pstdev(clock_minutes)
    best: tuple[float, str, list[float]] | None = None
    for event in ("sunset", "sunrise"):
        try:
            rel = [(a.at - solar_time(event, a.at.date(), location)).total_seconds() / 60 for a in matched]
        except ScheduleError:
            continue
        spread = pstdev(rel)
        if best is None or spread < best[0]:
            best = (spread, event, rel)
    if best is None:
        return None
    spread, event, rel = best
    if spread > ANCHOR_MAX_SPREAD or clock_spread < ANCHOR_SPREAD_GAIN * spread + 4:
        return None

    offset = max(-240, min(240, _round(median(rel))))
    earliest, latest = min(clock_minutes), max(clock_minutes)
    as_time = lambda m: friendly_time(time(m // 60, m % 60))  # noqa: E731
    lights = _light_names(routine)
    return CheckIn(
        kind=CheckInKind.ANCHOR_SWITCH,
        routine_id=routine.id,
        question=(
            f"Your {lights} times follow {event}, not the clock: you've usually turned it "
            f"{routine.action.value} {_relative(offset, event)}, while the routine stays at "
            f"{friendly_time(routine.anchor.at)}. Should it follow {event} instead?"
        ),
        evidence=[
            f"{len(matched)} manual changes since {min(a.at for a in matched):%b %d}",
            f"Relative to {event}: {_relative(offset, event)}, within ±{spread:.0f} min",
            f"By the clock they drifted from {as_time(latest)} to {as_time(earliest)}",
        ],
        proposal={
            "anchor": {"kind": "solar", "event": event, "offset_minutes": offset},
            "new_time": _relative(offset, event),
        },
        created_at=now,
    )


# -- Daylight Saving glide -----------------------------------------------------------------


def dst_glide_checkin(routines: Sequence[Routine], change: DstTransition | None, now: datetime) -> CheckIn | None:
    if change is None:
        return None
    plan = change.glide_plan()
    first_glide_day = plan[0][0]
    today = now.astimezone(change.tz).date()
    if not (change.day - timedelta(days=DST_ASK_DAYS_BEFORE) <= today <= first_glide_day):
        return None
    gliding = [r for r in routines if r.enabled and r.dst_glide and isinstance(r.anchor, ClockAnchor)]
    if not gliding:
        return None

    later = change.shift_minutes > 0
    clocks = "go back" if later else "go forward"
    names = _and_list([f"'{r.name}'" for r in sorted(gliding, key=lambda r: r.name)])
    step = abs(plan[0][1])
    return CheckIn(
        kind=CheckInKind.DST_GLIDE,
        question=(
            f"Clocks {clocks} an hour on {change.local_at:%A, %B} {change.day.day}. Want me to ease {names} "
            f"{step} minutes {'later' if later else 'earlier'} each day starting {first_glide_day:%A}, "
            f"so it isn't a one-hour jump?"
        ),
        evidence=[
            f"Daylight Saving Time {'ends' if later else 'starts'} {change.day:%b} {change.day.day} in {change.tz.key}",
            *(f"{d:%a %b} {d.day}: {m:+d} min" for d, m in plan),
            f"{GLIDE_DAYS} days × {step} min = the full hour by the eve of the change",
        ],
        proposal={
            "transition_at": change.at.isoformat(),
            "routine_ids": [r.id for r in gliding],
            "plan": [[d.isoformat(), m] for d, m in plan],
        },
        created_at=now,
    )


def find_checkins(
    routines: Sequence[Routine],
    adjustments: Sequence[Adjustment],
    location: Location,
    now: datetime,
    change: DstTransition | None,
    last_changed: dict[str, datetime],
) -> list[CheckIn]:
    """Every check-in currently supported by the evidence (deduping/muting is the caller's job)."""
    today = now.date()
    lookback_start = today - timedelta(days=LOOKBACK_DAYS - 1)
    history_start = min((a.at.date() for a in adjustments), default=today)
    found: list[CheckIn] = []
    for routine in routines:
        if not routine.enabled:
            continue
        # Only evidence gathered since the routine last changed counts.
        since = max(lookback_start, history_start)
        if routine.id in last_changed:
            since = max(since, last_changed[routine.id].date() + timedelta(days=1))
        recent = [a for a in adjustments if since <= a.at.date() <= today]
        switch = anchor_switch_checkin(routine, recent, location, now)
        if switch:
            found.append(switch)
            continue  # a better anchor beats a fixed offset on a drifting one
        offset = learned_offset_checkin(routine, recent, since, today, now)
        if offset:
            found.append(offset)
    glide = dst_glide_checkin(routines, change, now)
    if glide:
        found.append(glide)
    return found
