"""Seed a demo home (Oswego, NY by default; any city with --city/--region), four routines, and
~30 days of realistic manual light changes.

The history contains two clear patterns for the check-in detectors (Phase 2):
  * Living room: turned on ~20 min before its 18:30 routine on most weekday evenings.
  * Porch: turned on ~10 min before sunset, so it drifts earlier against its fixed 19:00 routine.
plus a little unrelated noise. Changes go through `Home.set_light`, the same path as voice
requests, so each one is matched to the routine it deviated from.

    uv run on-time-seed                    # 30 days ending now, into ON_TIME_DB (default on_time.db)
    uv run on-time-seed --end 2026-10-24   # history ending on a chosen day (good for DST demos)
    uv run on-time-seed --city Scottsdale --region AZ   # a home in Arizona (no Daylight Saving Time)
    uv run on-time-seed --city London --region UK       # a home in London (also: --city Sydney --region Australia)
"""

import argparse
import os
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from on_time.clock import Clock
from on_time.home import Home
from on_time.models import ClockAnchor, LightAction, LocationSource, Routine
from on_time.schedule import solar_time
from on_time.storage import SqliteRepository

WEEKDAYS = [0, 1, 2, 3, 4]


@dataclass
class SeedSummary:
    location: str
    routines: int
    adjustments: int
    first_day: date
    last_day: date


def seed(
    home: Home, end: date, days: int = 30, rng_seed: int = 42, city: str = "Oswego", region: str | None = "NY"
) -> SeedSummary:
    rng = random.Random(rng_seed)
    place = home.find_city(city, region)[0]  # most populous match
    location = home.set_location_from_place(place, LocationSource.USER)
    tz = location.tz

    for routine in home.list_routines():
        home.repo.delete_routine(routine.id)
    routines = [
        Routine(id="rt_wake", name="Wake up", light_ids=["bedroom"], action=LightAction.ON,
                anchor=ClockAnchor(at=time(6, 30)), days=WEEKDAYS),
        Routine(id="rt_evening", name="Weekday evening", light_ids=["living_room"], action=LightAction.ON,
                anchor=ClockAnchor(at=time(18, 30)), days=WEEKDAYS),
        Routine(id="rt_porch", name="Porch evening", light_ids=["porch"], action=LightAction.ON,
                anchor=ClockAnchor(at=time(19, 0))),
        Routine(id="rt_lights_out", name="Lights out", light_ids=["living_room", "kitchen"], action=LightAction.OFF,
                anchor=ClockAnchor(at=time(23, 0))),
    ]  # fmt: skip
    for routine in routines:
        home.repo.upsert_routine(routine)

    def change(when: datetime, light_id: str, action: LightAction) -> None:
        home.clock.travel_to(when)
        home.set_light(light_id, action)

    count = 0
    first = end - timedelta(days=days - 1)
    for offset in range(days):
        day = first + timedelta(days=offset)
        at = lambda h, m: datetime.combine(day, time(h, m), tzinfo=tz)  # noqa: E731

        # Pattern 1: living room on ~20 min early, most weekday evenings.
        if day.weekday() in WEEKDAYS and rng.random() < 0.85:
            change(at(18, 30) - timedelta(minutes=rng.randint(15, 25)), "living_room", LightAction.ON)
            count += 1

        # Pattern 2: porch on ~10 min before sunset (sunset moves; the 19:00 routine doesn't).
        if rng.random() < 0.8:
            sunset = solar_time("sunset", day, location)
            change(sunset - timedelta(minutes=rng.randint(5, 15)), "porch", LightAction.ON)
            count += 1

        # Noise: an occasional kitchen light in the afternoon.
        if rng.random() < 0.3:
            change(at(rng.randint(12, 16), rng.randint(0, 59)), "kitchen", LightAction.ON)
            count += 1

    home.clock.reset()
    return SeedSummary(location.name, len(routines), count, first, end)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed On_Time with a demo home and 30 days of light history.")
    parser.add_argument("--db", default=os.environ.get("ON_TIME_DB", "on_time.db"), help="SQLite file")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last day of history (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--fresh", action="store_true", help="Delete the database file first")
    parser.add_argument("--city", default="Oswego", help="Home city, e.g. Scottsdale")
    parser.add_argument("--region", default=None, help="US state or country, e.g. AZ, UK, Australia (default NY for Oswego)")
    args = parser.parse_args()
    region = args.region or ("NY" if args.city == "Oswego" else None)

    if args.fresh and Path(args.db).exists():
        Path(args.db).unlink()
    repo = SqliteRepository(args.db)
    home = Home(repo, Clock(repo))
    home.ensure_default_lights()
    end = args.end or datetime.now().date()
    summary = seed(home, end, args.days, city=args.city, region=region)
    repo.close()
    print(
        f"Seeded {args.db}: {summary.location}, {summary.routines} routines, "
        f"{summary.adjustments} light changes from {summary.first_day} to {summary.last_day}."
    )


if __name__ == "__main__":  # `python -m on_time.seed`, same as `on-time-seed`
    main()
