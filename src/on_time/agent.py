"""The conversational agent behind Simulated On Time for Alexa+.

The browser is the MCP client. It sends the conversation to POST /agent and gets back the next
step: either tool calls (which the browser makes itself over MCP) or a spoken reply.

Conversation messages (kept by the browser):
  {"role": "user", "text": "..."}
  {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "arguments"}], "context": {...}}
  {"role": "tool", "results": [{"id", "name", "ok", "data", "text"}]}

Steps returned:
  {"type": "tool_calls", "calls": [...], "mode": ...}   or   {"type": "reply", "text": ..., "context": ..., "mode": ...}

Modes: "mock" (rule-based, no AWS; the default) and "bedrock" (Claude on Amazon Bedrock).
"""

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

Message = dict[str, Any]
Step = dict[str, Any]

LIGHT_ALIASES = {
    "bedroom": "bedroom", "bed room": "bedroom",
    "living room": "living_room", "livingroom": "living_room", "lounge": "living_room", "family room": "living_room",
    "kitchen": "kitchen",
    "porch": "porch", "front porch": "porch", "porch light": "porch",
}  # fmt: skip

TZ_NAMES = {
    "America/New_York": "Eastern time", "America/Detroit": "Eastern time", "America/Chicago": "Central time",
    "America/Denver": "Mountain time", "America/Phoenix": "Mountain time, no Daylight Saving",
    "America/Los_Angeles": "Pacific time", "America/Anchorage": "Alaska time",
    "Pacific/Honolulu": "Hawaii time, no Daylight Saving",
    "Europe/London": "UK time", "Australia/Sydney": "Australian Eastern time",
}  # fmt: skip


def tz_name(key: str) -> str:
    """'Eastern time', or 'Paris time' for zones without a friendlier name."""
    return TZ_NAMES.get(key) or f"{key.rsplit('/', 1)[-1].replace('_', ' ')} time"

HELP = (
    "I can set where you live, turn lights on or off, set up routines at a time or around sunrise and sunset, "
    "tell you when your lights come on and why, and ease your routines through Daylight Saving Time."
)


def _call(name: str, arguments: dict | None = None) -> Step:
    return {"type": "tool_calls", "calls": [{"id": f"call_{uuid4().hex[:8]}", "name": name, "arguments": arguments or {}}]}


def _reply(text: str, context: dict | None = None) -> Step:
    step: Step = {"type": "reply", "text": text}
    if context:
        step["context"] = context
    return step


def spoken_time(iso: str) -> str:
    t = datetime.fromisoformat(iso)
    return t.strftime("%I:%M %p").lstrip("0")


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _clock(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")


def describe_event(e: dict) -> str:
    """'Porch evening comes on at 6:12 PM: sunset is at 6:22 PM, and it comes on 10 minutes before.'"""
    verb = "comes on" if e["action"] == "on" else "turns off"
    detail = e["factors"]["base"]["detail"]  # "clock 19:00" or "sunset 18:22 − 10 min"
    kind, hhmm, *rest = detail.split()
    reasons = []
    if kind == "clock":
        reasons.append(f"it's set for {_clock(hhmm)}")
    else:
        reasons.append(f"{kind} is at {_clock(hhmm)}")
        if rest:
            sign, minutes = rest[0], rest[1]
            reasons.append(f"it {verb} {minutes} minutes {'before' if sign == '−' else 'after'}")
    if learned := e["factors"]["learned_offset_minutes"]:
        reasons.append(f"you asked for it {abs(learned)} minutes {'earlier' if learned < 0 else 'later'}")
    if glide := e["factors"]["dst_glide_minutes"]:
        reasons.append(f"it's {abs(glide)} minutes {'later' if glide > 0 else 'earlier'} to ease into the clock change")
    return f"{e['name']} {verb} at {spoken_time(e['at'])}: {_and(reasons)}."


def find_light(text: str) -> str | None:
    lowered = text.lower()
    for alias in sorted(LIGHT_ALIASES, key=len, reverse=True):
        if alias in lowered:
            return LIGHT_ALIASES[alias]
    return None


def parse_place(raw: str) -> dict[str, str]:
    """'Oswego, New York' / 'Oswego NY' / 'Sydney Australia' / 'Phoenix' -> {"city": ..., "region": ...}."""
    from on_time.geo import US_STATES, country_words

    text = raw.strip().rstrip(".!?").strip()
    if "," in text:
        city, region = (part.strip() for part in text.split(",", 1))
    else:
        city, region = text, ""
        for name in sorted([*US_STATES, *(w.lower() for w in country_words())], key=len, reverse=True):
            if text.lower().endswith(" " + name):
                city, region = text[: -len(name)].strip(), text[-len(name) :]
                break
        else:
            parts = text.rsplit(" ", 1)
            if len(parts) == 2 and len(parts[1]) == 2 and parts[1].isalpha():
                city, region = parts[0], parts[1]
    args = {"city": city}
    if region:
        args["region"] = US_STATES.get(region.lower(), region.upper() if len(region) == 2 else region)
    return args


# -- mock agent ------------------------------------------------------------------------------

YES = re.compile(r"^(yes|yeah|yep|sure|ok(ay)?|please|do it|sounds good|go ahead|absolutely)\b", re.I)
NO = re.compile(r"^(no|nope|not now|don'?t|leave it|nah|skip)\b", re.I)
ADJUST_CLOCK = re.compile(r"make it (\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
ADJUST_MINUTES = re.compile(r"(\d{1,3})\s*min(?:ute)?s?\.?\s*(earlier|later|before|after|sooner)", re.I)
LOCATION = re.compile(r"\b(?:i'?m in|i live in|we'?re in|we live in|home is in|set (?:my )?location to)\s+(.+)", re.I)
LIGHT = re.compile(r"\bturn (on|off)\b|\b(on|off)\s*$|\blights? (on|off)\b", re.I)
DST = re.compile(r"daylight|\bdst\b|clocks?\b.*\b(go|change|back|forward)|time change|fall back|spring forward", re.I)
CHECKINS = re.compile(r"\b(questions?|check-?ins?|suggestions?|what'?s new|anything for me)\b", re.I)
WHY = re.compile(r"^\s*why\b", re.I)
SCHEDULE = re.compile(r"\bwhen\b|what time|schedule|tonight|today|tomorrow", re.I)
STATUS = re.compile(r"\b(which|what) lights\b|lights? (status|are on)|is the .* on", re.I)


def _turn(messages: Sequence[Message]) -> tuple[str, dict[str, dict], Message | None]:
    """The latest user text, tool results gathered since it (by tool name), and the check-in last asked."""
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    results: dict[str, dict] = {}
    for m in messages[last_user + 1 :]:
        if m["role"] == "tool":
            for r in m["results"]:
                results[r["name"]] = r
    asked = None
    for m in reversed(messages[:last_user]):
        if m["role"] == "assistant" and m.get("text"):
            asked = (m.get("context") or {}).get("checkin")
            break
    return messages[last_user]["text"], results, asked


def _first_question(checkins: list[dict], prefix: str) -> tuple[str, dict | None]:
    if not checkins:
        return "", None
    c = checkins[0]
    return f"{prefix}{c['question']}", {"checkin": {"id": c["id"], "kind": c["kind"], "proposal": c["proposal"]}}


SPOKEN_DURATIONS = [
    (r"\bhalf an hour\b|\bhalf hour\b|\b30 mins?\b", "30 minutes"),
    (r"\ba quarter (?:of an )?hour\b|\bquarter hour\b", "15 minutes"),
    (r"\b(?:an|one|1) hours?\b", "60 minutes"),
    (r"\b(\d{1,2}) hours?\b", lambda m: f"{int(m.group(1)) * 60} minutes"),
    (r"\bforty[- ]five\b", "45"), (r"\btwenty[- ]five\b", "25"), (r"\bthirty\b", "30"), (r"\btwenty\b", "20"),
    (r"\bfifteen\b", "15"), (r"\bten\b", "10"), (r"\bfive\b", "5"), (r"\bforty\b", "40"), (r"\bsixty\b", "60"),
]  # fmt: skip


def normalize_durations(text: str) -> str:
    """'half an hour earlier' -> '30 minutes earlier'; 'twenty min before' -> '20 min before'."""
    for pattern, replacement in SPOKEN_DURATIONS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def _adjust_minutes(text: str, checkin: dict) -> int | None:
    """The user's adjustment in minutes (negative = earlier/before), or None if they didn't give one."""
    proposal = checkin.get("proposal", {})
    text = normalize_durations(text)
    if m := ADJUST_MINUTES.search(text):
        n = int(m.group(1))
        return -n if m.group(2).lower() in ("earlier", "before", "sooner") else n
    if (m := ADJUST_CLOCK.search(text)) and checkin["kind"] == "learned_offset" and "new_time" in proposal:
        proposed = datetime.strptime(proposal["new_time"], "%I:%M %p")
        base = proposed - timedelta(minutes=int(proposal["learned_offset_minutes"]))
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        meridian = (m.group(3) or base.strftime("%p")).upper()
        hour = hour % 12 + (12 if meridian == "PM" else 0)
        return int((base.replace(hour=hour, minute=minute) - base).total_seconds() // 60)
    return None


# -- routines: create, list, remove ----------------------------------------------------------

ROUTINE_SOLAR = re.compile(r"(?:(\d{1,3})\s*min(?:ute)?s?\.?\s*(before|after)\s+|\bat\s+)(sunset|sunrise|dusk|dawn)\b", re.I)
ROUTINE_CLOCK = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\w:])", re.I)
ROUTINE_VERB = re.compile(r"^\s*(?:please\s+)?(?:turn|switch|add|create|set up|schedule)\b", re.I)
ROUTINE_LIST = re.compile(r"\b(?:list|show|what|which)\b.*\broutines\b|\bmy routines\b", re.I)
ROUTINE_REMOVE = re.compile(r"\b(?:remove|delete|cancel|get rid of)\b.*\broutine\b", re.I)
QUESTION = re.compile(r"^\s*(?:when|what|why|does|do|is|how|which)\b", re.I)
SUN_WORDS = {"dusk": "sunset", "dawn": "sunrise"}
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]


def parse_routine(text: str) -> dict | None:
    """'Turn the porch on 10 minutes before sunset every day' -> add_routine arguments.

    None if the text isn't a routine request; {"ask": ...} if the time is ambiguous ("at 7:30").
    """
    lowered = normalize_durations(text.lower())
    light = find_light(lowered)
    if not light or QUESTION.match(lowered) or ROUTINE_REMOVE.search(lowered):
        return None
    action = "off" if re.search(r"\boff\b", lowered) else "on"
    label = light.replace("_", " ").capitalize() + (" off" if action == "off" else "")
    if m := ROUTINE_SOLAR.search(lowered):
        event = SUN_WORDS.get(m.group(3), m.group(3))
        minutes = int(m.group(1) or 0)
        args: dict[str, Any] = {"solar_event": event, "offset_minutes": -minutes if m.group(2) == "before" else minutes}
        name = f"{label} {minutes} min {m.group(2)} {event}" if minutes else f"{label} at {event}"
    elif m := ROUTINE_CLOCK.search(lowered):
        hour, minute, meridian = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if not meridian and not m.group(2):
            return None  # "at 10" alone isn't clearly a time
        if meridian:
            hour = hour % 12 + (12 if meridian == "pm" else 0)
        elif hour <= 12:
            return {"ask": f"Is that {m.group(1)}:{m.group(2)} AM or PM? Say it like 'at {m.group(1)}:{m.group(2)} PM'."}
        if hour > 23 or minute > 59:
            return None
        args = {"at": f"{hour:02d}:{minute:02d}"}
        name = f"{label} {_clock(args['at'])}"
    else:
        return None
    if re.search(r"\bweekdays?\b|\bweeknights?\b", lowered):
        args["days"] = WEEKDAYS
    elif re.search(r"\bweekends?\b", lowered):
        args["days"] = WEEKENDS
    return {"name": name, "light_ids": [light], "action": action, **args}


def _days_text(days: list[int] | None) -> str:
    if not days or len(set(days)) == 7:
        return "every day"
    return {tuple(WEEKDAYS): "on weekdays", tuple(WEEKENDS): "on weekends"}.get(tuple(sorted(days)), "on some days")


def _anchor_text(routine: dict) -> str:
    """'10 minutes before sunset', 'at sunset', or 'at 6:15 PM' (clock time plus any confirmed change)."""
    anchor = routine["anchor"]
    if anchor["kind"] == "solar":
        offset = anchor["offset_minutes"]
        return f"{abs(offset)} minutes {'before' if offset < 0 else 'after'} {anchor['event']}" if offset else f"at {anchor['event']}"
    at = datetime.strptime(anchor["at"][:5], "%H:%M") + timedelta(minutes=routine.get("learned_offset_minutes") or 0)
    return f"at {at.strftime('%I:%M %p').lstrip('0')}"


def _routine_added(new: dict, routines: list[dict]) -> str:
    lights = _and([lid.replace("_", " ") for lid in new["light_ids"]])
    verb = "comes on" if new["action"] == "on" else "turns off"
    reply = f"Done. I added '{new['name']}': the {lights} {verb} {_anchor_text(new)} {_days_text(new['days'])}."
    today = next((r["today"] for r in routines if r["id"] == new["id"]), None)
    if isinstance(today, dict):
        reply += f" Today that's {spoken_time(today['at'])}."
    same = [r for r in routines if r["id"] != new["id"] and r["action"] == new["action"] and set(r["light_ids"]) & set(new["light_ids"])]
    if same:
        old = same[0]
        reply += (
            f" Your '{old['name']}' routine also turns it {old['action']} {_anchor_text(old)}."
            f" Say 'remove the {old['name'].lower()} routine' if you don't need it."
        )
    return reply


def _match_routine(text: str, routines: list[dict]) -> dict | None:
    """The routine named in the text, or the only routine for the light it names."""
    named = [r for r in routines if r["name"].lower() in text]
    if named:
        return max(named, key=lambda r: len(r["name"]))
    light = find_light(text)
    for_light = [r for r in routines if light in r["light_ids"]] if light else []
    return for_light[0] if len(for_light) == 1 else None


def _confirm(result: dict) -> str:
    data = result.get("data") or {}
    status = data.get("status")
    if status == "declined":
        return f"No problem. I won't ask about that again until {data.get('not_asked_again_until', 'a while')}."
    routine = data.get("routine")
    if routine is None:
        return "Done. I'll ease your routines through the clock change, 15 minutes a day."
    anchor = routine["anchor"]
    if anchor["kind"] == "solar":
        offset = anchor["offset_minutes"]
        when = f"{abs(offset)} minutes {'before' if offset < 0 else 'after'} {anchor['event']}" if offset else f"at {anchor['event']}"
        return f"Done. {routine['name']} now follows {anchor['event']}: {when}."
    at = datetime.strptime(anchor["at"], "%H:%M:%S") + timedelta(minutes=routine["learned_offset_minutes"])
    return f"Done. {routine['name']} now runs at {at.strftime('%I:%M %p').lstrip('0')}."


def mock_step(messages: Sequence[Message]) -> Step:
    text, results, asked = _turn(messages)
    lowered = text.lower().strip()
    routine = parse_routine(text)
    new_routine_request = routine is not None and bool(ROUTINE_VERB.match(lowered))

    # 1. An answer to the check-in we just asked. A stated amount wins over yes/no:
    #    "yes, but 20 min before" and "no, make it 6:15" are both adjustments.
    #    "Turn the porch on at sunset every day" is a new routine, even with a check-in pending.
    adjustment = _adjust_minutes(lowered, asked) if asked else None
    wants_change = adjustment is not None or bool(re.search(r"\bmake it\b|\binstead\b|\bbut\b", lowered))
    if asked and not new_routine_request and (YES.match(lowered) or NO.match(lowered) or wants_change):
        if "respond_to_checkin" not in results:
            if adjustment is not None:
                return _call(
                    "respond_to_checkin",
                    {"checkin_id": asked["id"], "response": "adjust", "minutes": adjustment, "note": text},
                )
            if wants_change:
                return _reply("Sure. How many minutes earlier or later should it be?", {"checkin": asked})
            if YES.match(lowered):
                return _call("respond_to_checkin", {"checkin_id": asked["id"], "response": "accept", "note": text})
            return _call("respond_to_checkin", {"checkin_id": asked["id"], "response": "decline", "note": text})
        answered = results["respond_to_checkin"]
        if not answered["ok"]:
            return _reply(answered["text"])
        if "get_checkins" not in results:
            return _call("get_checkins")
        question, context = _first_question(results["get_checkins"]["data"]["checkins"], " One more: ")
        return _reply(_confirm(answered) + question, context)

    # 2. Where the home is.
    if m := LOCATION.search(text):
        if "set_location" not in results:
            return _call("set_location", parse_place(m.group(1)))
        r = results["set_location"]
        if not r["ok"]:
            return _reply(r["text"])
        if r["data"]["status"] == "needs_clarification":
            return _reply(f"{r['data']['question']} {' or '.join(r['data']['candidates'])}?")
        loc = r["data"]["location"]
        return _reply(f"Got it: {loc['name']}, {tz_name(loc['timezone'])}.")

    # 3. Routines: create (before "turn on", which would switch the light right now), remove, list.
    if routine is not None:
        if "ask" in routine:
            return _reply(routine["ask"])
        if "add_routine" not in results:
            return _call("add_routine", routine)
        r = results["add_routine"]
        if not r["ok"]:
            return _reply(r["text"])
        if "list_routines" not in results:
            return _call("list_routines")
        return _reply(_routine_added(r["data"]["routine"], results["list_routines"]["data"]["routines"]))

    if ROUTINE_REMOVE.search(lowered):
        if "list_routines" not in results:
            return _call("list_routines")
        routines = results["list_routines"]["data"]["routines"]
        if "remove_routine" not in results:
            if not routines:
                return _reply("You don't have any routines.")
            if (match := _match_routine(lowered, routines)) is None:
                return _reply(f"Which one? Say 'remove the' and its name: {_and([r['name'] for r in routines])}.")
            return _call("remove_routine", {"routine_id": match["id"]})
        r = results["remove_routine"]
        return _reply(f"Done. I removed {r['data']['removed']['name']}." if r["ok"] else r["text"])

    if ROUTINE_LIST.search(lowered):
        if "list_routines" not in results:
            return _call("list_routines")
        routines = results["list_routines"]["data"]["routines"]
        if not routines:
            return _reply("You don't have any routines yet. Try 'turn the porch on at sunset every day'.")
        items = [f"{r['name']} ({r['action']} {_anchor_text(r)} {_days_text(r['days'])})" for r in routines]
        return _reply(f"You have {len(routines)} routines: {_and(items)}.")

    # 4. Turning a light on or off.
    light = find_light(lowered)
    if not light and LIGHT.search(lowered) and not STATUS.search(lowered):
        if "list_lights" not in results:
            return _call("list_lights")
        names = [l["name"].lower() for l in results["list_lights"]["data"]["lights"]]
        return _reply(f"I don't see that light. You have {_and(names)} lights.")
    if light and LIGHT.search(lowered) and not STATUS.search(lowered):
        action = "off" if re.search(r"\boff\b", lowered) else "on"
        if "set_light" not in results:
            return _call("set_light", {"light_id": light, "action": action})
        r = results["set_light"]
        if not r["ok"]:
            return _reply(r["text"])
        if r["data"].get("pending_checkins") and "get_checkins" not in results and not asked:
            return _call("get_checkins")
        question, context = _first_question((results.get("get_checkins") or {}).get("data", {}).get("checkins", []), " By the way: ")
        return _reply("Done." + question, context)

    # 5. Daylight Saving Time.
    if DST.search(lowered):
        if "plan_dst_transition" not in results:
            return _call("plan_dst_transition")
        if "get_checkins" not in results:
            return _call("get_checkins")
        plan = results["plan_dst_transition"]["data"]
        if not results["plan_dst_transition"]["ok"]:
            return _reply(results["plan_dst_transition"]["text"])
        if not plan["next_change"]:
            return _reply(plan["note"])
        glide = [c for c in results["get_checkins"]["data"]["checkins"] if c["kind"] == "dst_glide"]
        if glide:
            question, context = _first_question(glide, "")
            return _reply(question, context)
        return _reply(
            f"{plan['next_change']['summary']}. About a week before, I'll offer to ease your clock-based "
            "routines 15 minutes a day so it isn't a one-hour jump."
        )

    # 6. Pending check-ins.
    if CHECKINS.search(lowered):
        if "get_checkins" not in results:
            return _call("get_checkins")
        question, context = _first_question(results["get_checkins"]["data"]["checkins"], "")
        return _reply(question or "Nothing to ask right now. Your lights are in step.", context)

    # 7. Which lights are on.
    if STATUS.search(lowered):
        if "list_lights" not in results:
            return _call("list_lights")
        lights = results["list_lights"]["data"]["lights"]
        on = [l["name"].lower() for l in lights if l["is_on"]]
        return _reply(f"The {_and(on)} {'is' if len(on) == 1 else 'are'} on." if on else "All the lights are off.")

    # 8. When / why a light comes on.
    if WHY.search(lowered) or SCHEDULE.search(lowered):
        if "get_schedule" not in results:
            return _call("get_schedule")
        r = results["get_schedule"]
        if not r["ok"]:
            return _reply(r["text"])
        events = r["data"]["events"]
        if light:
            events = [e for e in events if light in e["light_ids"]]
            # "When does the porch come on?" is about its on-time, even if a routine also turns it off.
            wanted = "off" if re.search(r"\boff\b", lowered) else "on" if re.search(r"\b(come|comes|turn|go|goes) on\b", lowered) else None
            events = [e for e in events if e["action"] == wanted] or events
        if not events:
            return _reply("There's nothing scheduled for that today.")
        if WHY.search(lowered) or light:
            return _reply(describe_event(events[0]))
        summary = _and([f"{e['name'].lower()} {e['action']} at {spoken_time(e['at'])}" for e in events])
        return _reply(f"Today: {summary}.")

    if re.search(r"\b(hi|hello|hey|help|what can you do)\b", lowered):
        return _reply(f"Hi! {HELP}")
    return _reply(f"Sorry, I didn't catch that. {HELP}")


# -- Bedrock agent (Claude Haiku 4.5 via the Converse API) -----------------------------------

DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "us-east-2"
HIDDEN_TOOLS = {"set_demo_clock"}  # demo control stays in the UI

SYSTEM_PROMPT = """You are Alexa+ with the On Time add-on, speaking to someone at home. On Time manages \
their light routines (on/off only). Each routine's time comes from the clock or from sunrise/sunset at \
their home, a gradual Daylight Saving Time glide, and changes they confirmed in check-ins.

How to behave:
- Reply in one or two short spoken sentences. No markdown, lists, or emoji. Say times like "6:15 PM".
- Use the tools for every fact about the home; never guess times, lights, or schedules.
- Never change a routine without a clear yes. Check-ins are the way to propose changes.
- Always finish what the user asked first (make every tool call it needs); never put a request on hold to \
ask a check-in.
- If a tool result includes next_checkin, end your reply by asking that question directly, in your own \
words. Don't ask permission to ask ("I have some questions when you're ready" is wrong). Use get_checkins \
for its evidence if helpful.
- For Daylight Saving questions, call plan_dst_transition and say what glide_summary says (fall back \
moves routines LATER, spring forward EARLIER). If next_checkin is a dst_glide check-in, ask it now.
- Never say something was changed, scheduled, or will be applied unless a tool call in this turn did it. \
"Yes" to a check-in you asked means calling respond_to_checkin before you confirm.
- Never calculate times yourself. To confirm a change, say the summary from respond_to_checkin. For "when" \
or "why" a light comes on, use explain_light_time or get_schedule (they include the Daylight Saving glide \
and confirmed changes); a routine's base setting is not its time today.
- When the user answers a check-in you asked ("yes", "not now", "make it 6:15", "10 minutes earlier"), \
call respond_to_checkin with that check-in's id: accept, decline, or adjust with minutes (negative = earlier). \
For a 'learned_offset' check-in, minutes is the offset from the routine's base clock time; for an \
'anchor_switch' it is minutes relative to sunrise/sunset. Then confirm briefly and offer the next check-in if any.
- Read the whole answer: if it names a different amount ("yes, but 20 minutes before", "no, make it 6:25"), \
that is an adjust with the user's amount, not an accept or decline. If they want a change but give no \
amount, ask how many minutes.
- To remove or change a routine the user names ("remove the porch evening routine"), call list_routines \
first and use the id of the routine whose name matches their words, even if you created another routine \
for the same light this turn. Say the removed routine's name when you confirm.
- For a specific day ("on Thanksgiving", "next Friday"), call get_schedule with that date (YYYY-MM-DD in \
the home's time zone).
- If a city is ambiguous, ask which one using the candidates. Convert ZIP codes to a city first.
- To explain a time, use get_schedule or explain_light_time and say the reason plainly."""


class BedrockAgent:
    """Turns the browser's conversation into Converse API calls; returns the next step."""

    def __init__(self, model_id: str = DEFAULT_MODEL, region: str = DEFAULT_REGION, max_tokens: int = 400) -> None:
        import boto3
        from botocore.config import Config

        self.model_id = model_id
        self.max_tokens = max_tokens
        # Interactive use: retry once, then fall back to the mock agent rather than make the user wait.
        config = Config(retries={"total_max_attempts": 2, "mode": "adaptive"}, connect_timeout=5, read_timeout=30)
        self.client = boto3.Session(region_name=region).client("bedrock-runtime", config=config)

    @staticmethod
    def tool_config(tools: Sequence[dict]) -> dict:
        specs = [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": (t.get("description") or t["name"])[:1500],
                    "inputSchema": {"json": t.get("inputSchema") or {"type": "object", "properties": {}}},
                }
            }
            for t in tools
            if t.get("name") not in HIDDEN_TOOLS
        ]
        return {"tools": specs}

    @staticmethod
    def converse_messages(messages: Sequence[Message]) -> list[dict]:
        """Browser conversation -> Converse messages (alternating roles, starting with the user).

        The page sends only the latest messages, so the window can open mid-turn: on a tool result
        whose tool call was cut off, which Converse rejects (ValidationException). Start at the first
        thing the user actually said.
        """
        first_user = next((i for i, m in enumerate(messages) if m["role"] == "user"), len(messages))
        messages = messages[first_user:]
        out: list[dict] = []

        def add(role: str, blocks: list[dict]) -> None:
            if not blocks:
                return
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})

        for m in messages:
            if m["role"] == "user":
                add("user", [{"text": m["text"]}])
            elif m["role"] == "assistant":
                blocks = [{"text": m["text"]}] if m.get("text") else []
                blocks += [
                    {"toolUse": {"toolUseId": c["id"], "name": c["name"], "input": c.get("arguments") or {}}}
                    for c in m.get("tool_calls") or []
                ]
                add("assistant", blocks)
            elif m["role"] == "tool":
                add(
                    "user",
                    [
                        {
                            "toolResult": {
                                "toolUseId": r["id"],
                                "content": [{"json": r["data"]}] if isinstance(r.get("data"), dict) else [{"text": r.get("text") or "(no content)"}],
                                **({} if r.get("ok", True) else {"status": "error"}),
                            }
                        }
                        for r in m["results"]
                    ],
                )
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def step(self, messages: Sequence[Message], tools: Sequence[dict]) -> Step:
        allowed = {t["name"] for t in tools if t.get("name") not in HIDDEN_TOOLS}
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=self.converse_messages(messages),
            toolConfig=self.tool_config(tools),
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0.2},
        )
        content = response["output"]["message"]["content"]
        text = " ".join(b["text"] for b in content if "text" in b).strip()
        # Model output is untrusted: only tools the page offered, arguments must be objects.
        calls = [
            {"id": b["toolUse"]["toolUseId"], "name": b["toolUse"]["name"], "arguments": b["toolUse"].get("input") or {}}
            for b in content
            if "toolUse" in b and b["toolUse"]["name"] in allowed and isinstance(b["toolUse"].get("input", {}), dict)
        ]
        if response.get("stopReason") == "tool_use" and calls:
            return {"type": "tool_calls", "calls": calls, "text": text}
        return _reply(text or "Sorry, I didn't catch that.")


_bedrock: BedrockAgent | None = None
# After a failure, skip Bedrock for a while so each step doesn't wait on a doomed call.
BEDROCK_COOLDOWN_SECONDS = 60
_bedrock_down: tuple[float, str] | None = None  # (monotonic time it failed, reason)


def _bedrock_agent() -> BedrockAgent:
    global _bedrock
    if _bedrock is None:
        import os

        _bedrock = BedrockAgent(
            model_id=os.environ.get("ON_TIME_BEDROCK_MODEL", DEFAULT_MODEL),
            region=os.environ.get("ON_TIME_BEDROCK_REGION", DEFAULT_REGION),
        )
    return _bedrock


def next_step(messages: Sequence[Message], tools: Sequence[dict], mode: str = "mock", bedrock: Any = None) -> Step:
    if not messages or not any(m.get("role") == "user" for m in messages):
        raise ValueError("messages must include a user message")
    if mode == "bedrock":
        import time

        from botocore.exceptions import BotoCoreError, ClientError

        global _bedrock_down
        cooling = _bedrock_down and time.monotonic() - _bedrock_down[0] < BEDROCK_COOLDOWN_SECONDS
        if not cooling:
            try:
                step = (bedrock or _bedrock_agent()).step(messages, tools)
                _bedrock_down = None
                step["mode"] = "bedrock"
                return step
            except (ClientError, BotoCoreError) as e:
                # Actionable: keep the conversation going on the offline agent (throttled, no credentials, ...).
                _bedrock_down = (time.monotonic(), type(e).__name__)
        step = mock_step(messages)
        step["mode"] = "mock"
        step["fallback_reason"] = _bedrock_down[1] if _bedrock_down else "unavailable"
        return step
    step = mock_step(messages)
    step["mode"] = "mock"
    return step
