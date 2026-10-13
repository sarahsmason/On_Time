"""Homes in London (clocks change a week before the US) and Sydney (clocks fall back in April and
spring forward in October)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from on_time import agent, geo, server
from on_time.models import Location
from on_time.schedule import next_dst_transition, solar_time
from on_time.seed import seed
from test_agent import Conversation

LONDON_TZ = ZoneInfo("Europe/London")
SYDNEY_TZ = ZoneInfo("Australia/Sydney")
UTC = ZoneInfo("UTC")
LONDON = Location(name="London", latitude=51.50853, longitude=-0.12574, timezone="Europe/London", source="user")
SYDNEY = Location(name="Sydney", latitude=-33.86785, longitude=151.20732, timezone="Australia/Sydney", source="user")


# -- finding the place ------------------------------------------------------------


@pytest.mark.parametrize(
    ("city", "region", "label", "timezone"),
    [
        ("London", "GB", "London, United Kingdom", "Europe/London"),
        ("London", "UK", "London, United Kingdom", "Europe/London"),
        ("London", "England", "London, United Kingdom", "Europe/London"),
        ("Sydney", "AU", "Sydney, Australia", "Australia/Sydney"),
        ("Sydney", "Australia", "Sydney, Australia", "Australia/Sydney"),
        ("Sydney", "NSW", "Sydney, Australia", "Australia/Sydney"),
    ],
)
def test_find_cities_outside_the_us(city, region, label, timezone):
    [place] = geo.find_cities(city, region)
    assert (place.label, place.timezone) == (label, timezone)


def test_us_labels_are_unchanged():
    [oswego] = geo.find_cities("Oswego", "NY")
    assert oswego.label == "Oswego, NY"


@pytest.mark.parametrize(("city", "region"), [("Phoenix", "Arizona"), ("Oswego", "New York"), ("Phoenix", "arizona")])
def test_full_us_state_names_work(fresh_home, city, region):
    """Bedrock passes the state as said ("Arizona"); the offline agent converts it first. Both must work."""
    result = server.set_location(city=city, region=region)
    assert result["location"]["name"].startswith(f"{city}, ")


def test_ambiguous_london_lists_both_countries(fresh_home):
    result = server.set_location(city="London")
    assert result["status"] == "needs_clarification"
    assert result["candidates"] == ["London, United Kingdom", "London, Canada"]


@pytest.mark.parametrize(
    ("spoken", "parsed"),
    [
        ("London, UK", {"city": "London", "region": "UK"}),
        ("London England", {"city": "London", "region": "England"}),
        ("Sydney Australia", {"city": "Sydney", "region": "Australia"}),
        ("Sydney, NSW", {"city": "Sydney", "region": "NSW"}),
        ("Atlanta Georgia", {"city": "Atlanta", "region": "GA"}),  # the US state wins over the country
    ],
)
def test_parse_place_outside_the_us(spoken, parsed):
    assert agent.parse_place(spoken) == parsed


@pytest.mark.parametrize(
    ("said", "reply"),
    [
        ("I'm in London, UK", "Got it: London, United Kingdom, UK time."),
        ("I live in Sydney Australia", "Got it: Sydney, Australia, Australian Eastern time."),
        ("I'm in London", "Which London did you mean? London, United Kingdom or London, Canada?"),
    ],
)
def test_location_conversation(fresh_home, said, reply):
    assert Conversation().say(said) == reply


def test_time_zone_names_fall_back_to_the_city():
    assert agent.tz_name("Europe/Paris") == "Paris time"
    assert agent.tz_name("America/Argentina/Buenos_Aires") == "Buenos Aires time"


# -- the sun and the clocks ---------------------------------------------------------


def _hhmm(location: Location, event: str, day: date) -> str:
    return solar_time(event, day, location).strftime("%H:%M")


def test_seasons_are_opposite_in_the_two_hemispheres():
    june, december = date(2026, 6, 21), date(2026, 12, 21)
    # Long June evenings in London, long December evenings in Sydney (approximate published times).
    assert "21:05" <= _hhmm(LONDON, "sunset", june) <= "21:40"
    assert "15:40" <= _hhmm(LONDON, "sunset", december) <= "16:10"
    assert "19:50" <= _hhmm(SYDNEY, "sunset", december) <= "20:20"
    assert "16:40" <= _hhmm(SYDNEY, "sunset", june) <= "17:10"


@pytest.mark.parametrize(
    ("tz", "after", "direction", "utc_at", "day", "glide"),
    [
        # London falls back a week before the US (Oct 25 vs Nov 1, 2026), then springs forward Mar 28.
        (LONDON_TZ, "2026-10-06", "fall_back", "2026-10-25T01:00", date(2026, 10, 25), [15, 30, 45, 60]),
        (LONDON_TZ, "2026-11-01", "spring_forward", "2027-03-28T01:00", date(2027, 3, 28), [-15, -30, -45, -60]),
        # Sydney: Daylight Saving started Oct 4, 2026, so the next change is the April fall back.
        (SYDNEY_TZ, "2026-10-06", "fall_back", "2027-04-03T16:00", date(2027, 4, 4), [15, 30, 45, 60]),
        (SYDNEY_TZ, "2027-04-05", "spring_forward", "2027-10-02T16:00", date(2027, 10, 3), [-15, -30, -45, -60]),
    ],
)
def test_dst_changes_and_glides(tz, after, direction, utc_at, day, glide):
    change = next_dst_transition(tz, datetime.fromisoformat(after).replace(tzinfo=UTC))
    assert change.direction == direction
    assert change.at == datetime.fromisoformat(utc_at).replace(tzinfo=UTC)
    assert change.day == day
    plan = change.glide_plan()
    assert [m for _, m in plan] == glide
    assert [(day - d).days for d, _ in plan] == [4, 3, 2, 1]  # Wed..Sat before the Sunday change


# -- whole conversations ------------------------------------------------------------


@pytest.fixture(params=[("London", "UK", LONDON_TZ), ("Sydney", "Australia", SYDNEY_TZ)], ids=["london", "sydney"])
def world_home(request, fresh_home):
    city, region, tz = request.param
    seed(fresh_home, date(2026, 10, 8), city=city, region=region)
    fresh_home.clock.travel_to(datetime(2026, 10, 8, 19, 30, tzinfo=tz))
    return fresh_home, tz


def test_habit_checkin_and_sun_routine_work_abroad(world_home):
    _, tz = world_home
    chat = Conversation()
    # Sydney's history crosses its Oct 4 clock change; the 20-minutes-early habit still shows.
    assert "about 20 minutes early" in chat.say("Turn on the living room")
    assert chat.say("Yes, but 15 minutes before").startswith("Done. Weekday evening now runs at 6:15 PM.")
    assert chat.say("Yes") == "Done. Porch evening now follows sunset: 10 minutes before sunset."
    reply = chat.say("When do my porch lights come on tonight?")
    assert ": sunset is at " in reply and reply.endswith("and it comes on 10 minutes before.")


@pytest.mark.parametrize(
    ("city", "region", "tz", "ask_at", "question", "check_at", "wake_up"),
    [
        ("London", "UK", LONDON_TZ, (2026, 10, 19), "Clocks go back an hour on Sunday, October 25.",
         (2026, 10, 22), "Wake up comes on at 7:00 AM"),
        ("London", "UK", LONDON_TZ, (2027, 3, 22), "Clocks go forward an hour on Sunday, March 28.",
         (2027, 3, 25), "Wake up comes on at 6:00 AM"),
        ("Sydney", "Australia", SYDNEY_TZ, (2027, 3, 29), "Clocks go back an hour on Sunday, April 4.",
         (2027, 4, 1), "Wake up comes on at 7:00 AM"),
        ("Sydney", "Australia", SYDNEY_TZ, (2027, 9, 27), "Clocks go forward an hour on Sunday, October 3.",
         (2027, 9, 30), "Wake up comes on at 6:00 AM"),
    ],
)  # fmt: skip
def test_dst_glide_conversation_abroad(fresh_home, city, region, tz, ask_at, question, check_at, wake_up):
    seed(fresh_home, date(2026, 10, 8), city=city, region=region)
    fresh_home.clock.travel_to(datetime(*ask_at, 9, 0, tzinfo=tz))
    chat = Conversation()
    assert chat.say("What happens with daylight saving time?").startswith(question)
    assert chat.say("Yes please").startswith("Done. I'll ease your routines through the clock change")
    fresh_home.clock.travel_to(datetime(*check_at, 6, 0, tzinfo=tz))
    assert chat.say("Why does the bedroom light come on when it does?").startswith(wake_up)  # day 2: ±30 min


@pytest.mark.parametrize(
    ("city", "region", "tz", "asked"),
    [("London", "UK", LONDON_TZ, True), ("Oswego", "NY", ZoneInfo("America/New_York"), False)],
)
def test_london_is_asked_a_week_before_the_us(fresh_home, city, region, tz, asked):
    """On Oct 19, 2026 London's change is 6 days away; Oswego's is 13 days away and not asked yet."""
    seed(fresh_home, date(2026, 10, 8), city=city, region=region)
    fresh_home.clock.travel_to(datetime(2026, 10, 19, 9, 0, tzinfo=tz))
    kinds = [c["kind"] for c in server.get_checkins()["checkins"]]
    assert ("dst_glide" in kinds) is asked
