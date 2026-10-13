"""Mock agent: whole conversations, run the way the web client runs them."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from on_time import agent, server
from on_time.seed import seed

NY = ZoneInfo("America/New_York")


class Conversation:
    """Plays the browser: asks the agent, makes the tool calls, feeds results back."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.calls: list[str] = []

    def say(self, text: str) -> str:
        self.messages.append({"role": "user", "text": text})
        for _ in range(6):
            step = agent.next_step(self.messages, tools=[])
            if step["type"] == "reply":
                self.messages.append({"role": "assistant", "text": step["text"], "context": step.get("context")})
                return step["text"]
            self.messages.append({"role": "assistant", "text": "", "tool_calls": step["calls"]})
            results = []
            for c in step["calls"]:
                self.calls.append(c["name"])
                try:
                    data = getattr(server, c["name"])(**c["arguments"])
                    results.append({"id": c["id"], "name": c["name"], "ok": True, "data": data, "text": ""})
                except ToolError as e:
                    results.append({"id": c["id"], "name": c["name"], "ok": False, "data": None, "text": str(e)})
            self.messages.append({"role": "tool", "results": results})
        raise AssertionError("agent did not reply")


@pytest.fixture
def chat(fresh_home):
    seed(fresh_home, date(2026, 10, 8))
    fresh_home.clock.travel_to(datetime(2026, 10, 8, 20, 0, tzinfo=NY))
    return Conversation()


def test_parse_place():
    assert agent.parse_place("Oswego, New York") == {"city": "Oswego", "region": "NY"}
    assert agent.parse_place("Oswego NY.") == {"city": "Oswego", "region": "NY"}
    assert agent.parse_place("Salt Lake City Utah") == {"city": "Salt Lake City", "region": "UT"}
    assert agent.parse_place("Phoenix") == {"city": "Phoenix"}


def test_location_with_state(chat):
    assert chat.say("I'm in Oswego, New York") == "Got it: Oswego, NY, Eastern time."


def test_ambiguous_location_asks(chat):
    assert chat.say("I live in Oswego") == "Which Oswego did you mean? Oswego, IL or Oswego, NY?"


def test_demo_script_conversation(chat):
    reply = chat.say("Turn on the living room")
    assert reply.startswith("Done. By the way: You've turned the living room on about 20 minutes early")
    assert chat.calls == ["set_light", "get_checkins"]

    reply = chat.say("Make it 6:15")
    assert reply.startswith("Done. Weekday evening now runs at 6:15 PM. One more: Your porch times follow sunset")

    assert chat.say("Yes") == "Done. Porch evening now follows sunset: 10 minutes before sunset."

    reply = chat.say("When do my porch lights come on tonight?")
    assert reply.startswith("Porch evening comes on at 6:")
    assert reply.endswith("and it comes on 10 minutes before.")
    assert ": sunset is at 6:" in reply

    assert chat.say("Why does the living room come on then?") == (
        "Weekday evening comes on at 6:15 PM: it's set for 6:30 PM and you asked for it 15 minutes earlier."
    )


def test_decline_mutes(chat):
    chat.say("Turn on the living room")
    assert chat.say("Not now").startswith("No problem. I won't ask about that again until 2026-10-22.")


def test_adjust_in_minutes(chat):
    chat.say("Turn on the living room")
    assert chat.say("make it 10 minutes earlier").startswith("Done. Weekday evening now runs at 6:20 PM.")


@pytest.mark.parametrize(
    ("answer", "expected_time"),
    [
        ("yes, but 20 min before", "6:10 PM"),  # an amount wins over "yes"
        ("Yes but twenty five minutes earlier", "6:05 PM"),
        ("sure, make it 10 minutes earlier", "6:20 PM"),
        ("no, 15 minutes before", "6:15 PM"),  # an amount wins over "no"
        ("yes but half an hour earlier", "6:00 PM"),
        ("ok but make it 6:25", "6:25 PM"),
    ],
)
def test_qualified_answers_adjust_instead_of_accepting(chat, answer, expected_time):
    chat.say("Turn on the living room")
    assert chat.say(answer).startswith(f"Done. Weekday evening now runs at {expected_time}.")


def test_yes_but_without_an_amount_asks_how_much(chat):
    chat.say("Turn on the living room")
    assert chat.say("yes, but not that early") == "Sure. How many minutes earlier or later should it be?"
    assert chat.say("15 minutes earlier").startswith("Done. Weekday evening now runs at 6:15 PM.")


def test_anchor_switch_with_qualified_answer(chat):
    chat.say("Turn on the living room")
    chat.say("Not now")  # living room declined; porch is next
    assert chat.say("any questions?").startswith("Your porch times follow sunset")
    assert chat.say("Yes, but 20 minutes before sunset") == (
        "Done. Porch evening now follows sunset: 20 minutes before sunset."
    )


def test_normalize_durations():
    from on_time.agent import normalize_durations

    assert normalize_durations("half an hour earlier") == "30 minutes earlier"
    assert normalize_durations("an hour later") == "60 minutes later"
    assert normalize_durations("2 hours earlier") == "120 minutes earlier"
    assert normalize_durations("twenty min before") == "20 min before"


def test_dst_conversation(chat, fresh_home):
    fresh_home.clock.travel_to(datetime(2026, 10, 26, 9, 0, tzinfo=NY))
    reply = chat.say("What happens when daylight saving time ends?")
    assert reply.startswith("Clocks go back an hour on Sunday, November 1.")
    assert chat.say("Yes please") .startswith("Done. I'll ease your routines through the clock change")
    events = {e["routine_id"]: e for e in server.get_schedule(day="2026-10-29")["events"]}
    assert events["rt_wake"]["factors"]["dst_glide_minutes"] == 30


def test_dst_before_the_window_explains_whats_coming(chat):
    reply = chat.say("When do the clocks change?")
    assert reply.startswith("Clocks go back 1 hour on Sunday, November 1. About a week before")


def test_status_and_help(chat):
    chat.say("Turn off the porch")
    assert chat.say("Which lights are on?") == "The kitchen and living room are on."
    assert chat.say("hello").startswith("Hi! I can set where you live")
    assert chat.say("blorp").startswith("Sorry, I didn't catch that.")


def test_unknown_light_lists_the_real_ones(chat):
    assert chat.say("turn on the garage light") == "I don't see that light. You have bedroom, kitchen, living room and porch lights."


def test_tool_errors_are_spoken(chat):
    assert "couldn't find a city" in chat.say("I'm in Nowhereville")


def test_glide_day_explanation(chat, fresh_home):
    fresh_home.clock.travel_to(datetime(2026, 10, 26, 9, 0, tzinfo=NY))
    chat.say("What about daylight saving time?")
    chat.say("yes")
    fresh_home.clock.travel_to(datetime(2026, 10, 29, 5, 0, tzinfo=NY))
    assert chat.say("Why does the bedroom light come on when it does?") == (
        "Wake up comes on at 7:00 AM: it's set for 6:30 AM and it's 30 minutes later to ease into the clock change."
    )


def _routines_named(name: str) -> list[dict]:
    return [r for r in server.list_routines()["routines"] if r["name"] == name]


def test_sunset_routine_is_created_not_switched_on(chat, fresh_home):
    porch_was_on = {l["id"]: l["is_on"] for l in server.list_lights()["lights"]}["porch"]
    changes = len(fresh_home.repo.list_adjustments())
    reply = chat.say("Turn the porch on 10 minutes before sunset every day")
    assert reply.startswith(
        "Done. I added 'Porch 10 min before sunset': the porch comes on 10 minutes before sunset every day. "
        "Today that's 6:"
    )
    assert reply.endswith(
        "Your 'Porch evening' routine also turns it on at 7:00 PM. "
        "Say 'remove the porch evening routine' if you don't need it."
    )
    assert chat.calls == ["add_routine", "list_routines"]
    [new] = _routines_named("Porch 10 min before sunset")
    assert new["anchor"] == {"kind": "solar", "event": "sunset", "offset_minutes": -10}
    # A routine request never flips the light now or counts as a manual change.
    assert {l["id"]: l["is_on"] for l in server.list_lights()["lights"]}["porch"] == porch_was_on
    assert len(fresh_home.repo.list_adjustments()) == changes


@pytest.mark.parametrize(
    ("request_text", "name", "anchor", "days"),
    [
        ("Turn the bedroom off at 11:30 pm on weekdays", "Bedroom off 11:30 PM", {"kind": "clock", "at": "23:30:00"}, [0, 1, 2, 3, 4]),
        ("turn the porch on at dusk", "Porch at sunset", {"kind": "solar", "event": "sunset", "offset_minutes": 0}, None),
        ("Switch the kitchen on half an hour after sunrise on weekends", "Kitchen 30 min after sunrise",
         {"kind": "solar", "event": "sunrise", "offset_minutes": 30}, [5, 6]),
        ("Turn the living room on at 18:45", "Living room 6:45 PM", {"kind": "clock", "at": "18:45:00"}, None),
    ],
)  # fmt: skip
def test_routine_requests(chat, request_text, name, anchor, days):
    assert chat.say(request_text).startswith(f"Done. I added '{name}'")
    [new] = _routines_named(name)
    assert {k: v for k, v in new["anchor"].items() if k in anchor} == anchor
    assert new["days"] == (days or [0, 1, 2, 3, 4, 5, 6])


def test_ambiguous_clock_time_asks_am_or_pm(chat):
    assert chat.say("Turn the kitchen on at 7:30") == "Is that 7:30 AM or PM? Say it like 'at 7:30 PM'."
    assert chat.calls == []


def test_questions_about_sunset_dont_create_routines(chat):
    reply = chat.say("Why does the porch come on at sunset?")
    assert reply.startswith("Porch evening comes on at")
    assert "add_routine" not in chat.calls


def test_routine_request_wins_over_a_pending_checkin(chat):
    chat.say("Turn on the living room")  # the habit check-in is now pending
    assert chat.say("Turn the porch on at sunset every day").startswith("Done. I added 'Porch at sunset'")
    assert "respond_to_checkin" not in chat.calls


def test_list_and_remove_routines(chat):
    reply = chat.say("What routines do I have?")
    assert reply.startswith("You have 4 routines: ")
    assert "Wake up (on at 6:30 AM on weekdays)" in reply
    assert "Lights out (off at 11:00 PM every day)" in reply

    assert chat.say("Remove the porch evening routine") == "Done. I removed Porch evening."
    assert not _routines_named("Porch evening")

    assert chat.say("Delete the routine").startswith("Which one? Say 'remove the' and its name: ")
    assert chat.calls.count("remove_routine") == 1  # nothing removed without a name


def test_removing_a_routine_drops_its_pending_checkin(chat):
    """Found in live Bedrock testing (Oct 13): after "Porch evening" was removed, the assistant kept asking
    whether that routine should follow sunset."""
    kinds = lambda: [(c["kind"], c["routine_id"]) for c in server.get_checkins()["checkins"]]  # noqa: E731
    assert ("anchor_switch", "rt_porch") in kinds()
    assert chat.say("Remove the porch evening routine") == "Done. I removed Porch evening."
    assert all(rid != "rt_porch" for _, rid in kinds())


def test_agent_requires_a_user_message():
    with pytest.raises(ValueError):
        agent.next_step([], tools=[])
