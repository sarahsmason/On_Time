from datetime import date
from statistics import median

from on_time.clock import Clock
from on_time.home import Home
from on_time.seed import seed
from on_time.storage import SqliteRepository

END = date(2026, 10, 8)


def test_seed_builds_home_and_history(fresh_home):
    summary = seed(fresh_home, END)
    assert summary.location == "Oswego, NY"
    assert summary.routines == 4
    assert (summary.first_day, summary.last_day) == (date(2026, 9, 9), END)
    assert summary.adjustments == len(fresh_home.repo.list_adjustments())
    assert 40 <= summary.adjustments <= 70
    assert not fresh_home.clock.is_overridden  # demo clock reset afterwards


def test_seed_is_deterministic(fresh_home):
    def history() -> list:
        repo = SqliteRepository()
        home = Home(repo, Clock(repo))
        home.ensure_default_lights()
        seed(home, END)
        return [(a.light_id, a.at, a.routine_id) for a in repo.list_adjustments()]

    assert history() == history()


def test_living_room_pattern_is_about_20_minutes_early_on_weekdays(fresh_home):
    seed(fresh_home, END)
    living = fresh_home.repo.list_adjustments(light_id="living_room")
    assert all(a.routine_id == "rt_evening" for a in living)
    assert all(a.at.weekday() < 5 for a in living)
    deltas = [a.delta_minutes for a in living]
    assert all(-25 <= d <= -15 for d in deltas)
    assert -22 <= median(deltas) <= -18


def test_porch_pattern_drifts_earlier_with_sunset(fresh_home):
    seed(fresh_home, END)
    porch = fresh_home.repo.list_adjustments(light_id="porch")
    assert all(a.routine_id == "rt_porch" for a in porch)
    early = [a.delta_minutes for a in porch if a.at.day >= 9 and a.at.month == 9][:5]
    late = [a.delta_minutes for a in porch if a.at.month == 10][-5:]
    assert median(late) < median(early) - 15  # sunset moved earlier, the 19:00 routine didn't


def test_seed_any_city_scottsdale_has_no_dst(fresh_home):
    from on_time import server

    summary = seed(fresh_home, END, city="Scottsdale", region="AZ")
    assert summary.location == "Scottsdale, AZ"
    assert server.get_location()["location"]["timezone"] == "America/Phoenix"
    plan = server.plan_dst_transition()
    assert plan["next_change"] is None
    assert plan["note"].startswith("Scottsdale, AZ doesn't observe Daylight Saving Time")
    # The porch history follows Scottsdale's own sunsets, so the follow-the-sun habit still shows up.
    porch = fresh_home.repo.list_adjustments(light_id="porch")
    assert porch and all(a.routine_id == "rt_porch" for a in porch)


def test_kitchen_noise_is_unmatched(fresh_home):
    seed(fresh_home, END)
    assert all(a.routine_id is None for a in fresh_home.repo.list_adjustments(light_id="kitchen"))
