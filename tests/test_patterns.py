"""Phase 2: pattern detection, DST glide check-ins, and the glide applied to the schedule."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from on_time import server
from on_time.models import Adjustment, CheckIn, ClockAnchor, LightAction, Location, Routine
from on_time.patterns import anchor_switch_checkin, dst_glide_checkin, learned_offset_checkin
from on_time.schedule import next_dst_transition
from on_time.seed import seed

NY = ZoneInfo("America/New_York")
OSWEGO = Location(name="Oswego, NY", latitude=43.4553, longitude=-76.5105, timezone="America/New_York", source="user")
NOW = datetime(2026, 10, 8, 20, 0, tzinfo=NY)
EVENING = Routine(
    id="rt_evening", name="Weekday evening", light_ids=["living_room"], action=LightAction.ON,
    anchor=ClockAnchor(at=time(18, 30)), days=[0, 1, 2, 3, 4],
)  # fmt: skip


def history(deltas: dict[date, int], routine: Routine = EVENING) -> list[Adjustment]:
    out = []
    for day, delta in deltas.items():
        scheduled = datetime.combine(day, routine.anchor.at, tzinfo=NY)
        out.append(
            Adjustment(
                light_id=routine.light_ids[0], action=routine.action, routine_id=routine.id,
                scheduled_at=scheduled, at=scheduled + timedelta(minutes=delta),
            )  # fmt: skip
        )
    return out


def weekdays(n: int, end: date = date(2026, 10, 8)) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


# -- learned offset ------------------------------------------------------------------------


def test_consistent_habit_becomes_a_checkin():
    days = weekdays(10)
    ci = learned_offset_checkin(EVENING, history({d: -20 + (i % 3) for i, d in enumerate(days)}), days[0], NOW.date(), NOW)
    assert ci is not None
    assert ci.proposal == {"learned_offset_minutes": -20, "new_time": "6:10 PM"}
    assert "about 20 minutes early on 10 of the last 10 weekday evenings" in ci.question


@pytest.mark.parametrize(
    ("description", "deltas"),
    [
        ("too small to matter", [-5] * 10),
        ("inconsistent", [-40, 5, -30, 10, -20, 0, -35, 15, -25, -5]),
        ("too few days", [-20] * 3),
    ],
)
def test_no_checkin_without_strong_evidence(description, deltas):
    days = weekdays(10)
    adjustments = history(dict(zip(days, deltas)))
    assert learned_offset_checkin(EVENING, adjustments, days[0], NOW.date(), NOW) is None, description


def test_occasional_habit_below_ratio_is_ignored():
    days = weekdays(10)
    adjustments = history({d: -20 for d in days[:5]})  # 5 of 10 weekdays
    assert learned_offset_checkin(EVENING, adjustments, days[0], NOW.date(), NOW) is None


def test_proposal_builds_on_existing_learned_offset():
    routine = EVENING.model_copy(update={"learned_offset_minutes": -10})
    days = weekdays(8)
    ci = learned_offset_checkin(routine, history({d: -15 for d in days}, routine), days[0], NOW.date(), NOW)
    assert ci.proposal["learned_offset_minutes"] == -25  # -10 already confirmed, -15 more
    assert ci.proposal["new_time"] == "6:05 PM"


# -- anchor switch ---------------------------------------------------------------------------


def test_clock_routine_tracking_sunset_suggests_following_sunset():
    from on_time.schedule import solar_time

    porch = Routine(id="rt_porch", name="Porch", light_ids=["porch"], action=LightAction.ON, anchor=ClockAnchor(at=time(19, 0)))
    adjustments = []
    for i in range(20):
        day = date(2026, 9, 15) + timedelta(days=i)
        at = solar_time("sunset", day, OSWEGO) - timedelta(minutes=12)
        adjustments.append(Adjustment(light_id="porch", action=LightAction.ON, routine_id="rt_porch", at=at))
    ci = anchor_switch_checkin(porch, adjustments, OSWEGO, NOW)
    assert ci.proposal["anchor"] == {"kind": "solar", "event": "sunset", "offset_minutes": -10}
    assert "follow sunset instead" in ci.question


def test_fixed_clock_habit_does_not_suggest_sunset():
    days = weekdays(15)
    assert anchor_switch_checkin(EVENING, history({d: -20 for d in days}), OSWEGO, NOW) is None


# -- DST glide check-in ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "asks"),
    [(date(2026, 10, 24), False), (date(2026, 10, 25), True), (date(2026, 10, 28), True), (date(2026, 10, 29), False)],
)
def test_dst_checkin_window(today, asks):
    now = datetime.combine(today, time(9), tzinfo=NY)
    change = next_dst_transition(NY, now)
    assert (dst_glide_checkin([EVENING], change, now) is not None) is asks


def test_dst_checkin_wording_and_plan():
    now = datetime(2026, 10, 26, 9, 0, tzinfo=NY)
    ci = dst_glide_checkin([EVENING], next_dst_transition(NY, now), now)
    assert ci.question.startswith("Clocks go back an hour on Sunday, November 1.")
    assert "15 minutes later each day starting Wednesday" in ci.question
    assert ci.proposal["plan"] == [["2026-10-28", 15], ["2026-10-29", 30], ["2026-10-30", 45], ["2026-10-31", 60]]
    assert ci.proposal["routine_ids"] == ["rt_evening"]


def test_dst_checkin_skips_solar_routines_and_no_dst_zones():
    from on_time.models import SolarAnchor

    dusk = Routine(name="Dusk", light_ids=["porch"], action=LightAction.ON, anchor=SolarAnchor(event="sunset"))
    now = datetime(2026, 10, 26, 9, 0, tzinfo=NY)
    assert dst_glide_checkin([dusk], next_dst_transition(NY, now), now) is None
    assert dst_glide_checkin([EVENING], None, now) is None


# -- end to end through the tools, on the seeded home ----------------------------------------


@pytest.fixture
def seeded(fresh_home):
    seed(fresh_home, date(2026, 10, 8))
    fresh_home.clock.travel_to(NOW)
    return fresh_home


def test_seeded_home_raises_exactly_the_two_habit_checkins(seeded):
    assert server.list_lights()["pending_checkins"] == 2
    checkins = {c["routine_id"]: c for c in server.get_checkins()["checkins"]}
    assert set(checkins) == {"rt_evening", "rt_porch"}
    assert checkins["rt_evening"]["kind"] == "learned_offset"
    assert checkins["rt_porch"]["kind"] == "anchor_switch"


def test_accepting_checkins_changes_the_schedule_and_they_stay_answered(seeded):
    checkins = {c["routine_id"]: c for c in server.get_checkins()["checkins"]}
    server.respond_to_checkin(checkin_id=checkins["rt_evening"]["id"], response="adjust", minutes=-15)
    server.respond_to_checkin(checkin_id=checkins["rt_porch"]["id"], response="accept")

    events = {e["routine_id"]: e for e in server.get_schedule(day="2026-10-09")["events"]}
    assert events["rt_evening"]["at"][11:16] == "18:15"
    assert events["rt_porch"]["factors"]["base"]["detail"].startswith("sunset 18:")
    assert events["rt_porch"]["factors"]["base"]["detail"].endswith("− 10 min")

    # Old evidence predates the change, so nothing is re-asked.
    assert server.get_checkins() == {"checkins": []}


def test_declined_checkin_is_not_re_raised(seeded):
    [evening] = [c for c in server.get_checkins()["checkins"] if c["routine_id"] == "rt_evening"]
    server.respond_to_checkin(checkin_id=evening["id"], response="decline")
    assert "rt_evening" not in {c["routine_id"] for c in server.get_checkins()["checkins"]}


def test_dst_plan_surfaces_the_glide_checkin_first(seeded):
    seeded.clock.travel_to(datetime(2026, 10, 26, 9, 0, tzinfo=NY))
    seeded.raise_checkin(  # a habit check-in also waiting, so the preference matters
        CheckIn(kind="learned_offset", routine_id="rt_evening", question="Move it?", created_at=datetime(2026, 10, 1, tzinfo=NY))
    )
    plan = server.plan_dst_transition()
    assert plan["pending_checkins"] >= 2
    assert plan["next_checkin"]["kind"] == "dst_glide"
    assert plan["next_checkin"]["question"].startswith("Clocks go back an hour on Sunday, November 1.")


def test_dst_glide_flow_end_to_end(seeded):
    seeded.clock.travel_to(datetime(2026, 10, 26, 9, 0, tzinfo=NY))
    [glide] = [c for c in server.get_checkins()["checkins"] if c["kind"] == "dst_glide"]
    assert "Clocks go back an hour on Sunday, November 1" in glide["question"]
    server.respond_to_checkin(checkin_id=glide["id"], response="accept", note="yes, ease me in")

    def wake(day: str) -> dict:
        return next(e for e in server.get_schedule(day=day)["events"] if e["routine_id"] == "rt_wake")

    assert wake("2026-10-27")["at"][11:16] == "06:30"  # before the glide
    assert wake("2026-10-28")["at"][11:16] == "06:45"
    assert wake("2026-10-29")["at"][11:16] == "07:00"
    assert wake("2026-10-30")["at"][11:16] == "07:15"
    assert wake("2026-10-29")["factors"]["dst_glide_minutes"] == 30
    assert "+30 min Daylight Saving glide" in wake("2026-10-29")["explanation"]
    # Monday after the change: back to 06:30 on the new clock, which is 07:30 on the old one.
    monday = wake("2026-11-02")
    assert monday["at"] == "2026-11-02T06:30:00-05:00"
    # A yes for this clock change is final: it isn't asked again.
    assert all(c["kind"] != "dst_glide" for c in server.get_checkins()["checkins"])
