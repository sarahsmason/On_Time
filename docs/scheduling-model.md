# On_Time: Lighting Schedule Model

**Scope: lights only.** Every light routine's time on a given day comes from three factors.
On_Time combines them and can explain each one.

```
effective_time(routine, day) = base_time(routine, day)          # 1. clock or sunrise/sunset at the user's location
                             + dst_glide(routine, day)          # 2. gradual shift around a DST change
                             + learned_offset(routine)          # 3. confirmed pattern from check-ins
```

## The routine
A routine is a scheduled lighting action, e.g. "Bedroom lights on at wake time".

| Field | Example |
|---|---|
| lights | `bedroom`, `living_room` |
| action | `on` / `off` (on/off only; brightness and warmth are out of scope) |
| anchor | `clock 06:30` **or** `solar sunset -20m` / `solar sunrise +0m` |
| learned_offset | `+15m` (only after the user confirms in a check-in) |

## Factor 1: Base time (clock or sun at the user's location)
- **Clock anchor:** a fixed local time (wake-up, bedtime).
- **Solar anchor:** sunrise or sunset at the user's location, plus or minus an offset, computed daily with `astral`.
- **Location sources:**
  1. **Device:** the Alexa+ device location, passed in by the host (real Alexa+ or the simulated host).
  2. **User input:** a spoken or typed city, ZIP, or lat/lon ("I'm in Oswego, NY"), via `set_location`.

  The location also sets the IANA timezone, which DST calculations rely on.

## Factor 2: DST glide
Clocks jump an hour; people adjust better in small steps.

- **Applies to clock-anchored routines.** Solar routines already follow the sun, so On_Time only explains their apparent one-hour clock jump.
- **Default: 4 days at 15 minutes per day.**
- **Fall back** (e.g. Sun Nov 1, 2026): on the 4 days *before* the change (Wed Oct 28 to Sat Oct 31), shift the routine **later** by +15, +30, +45, +60 min. By the eve of the change it sits +60 min (old clock), which equals the original time on the new clock. From the change on, the offset is 0.
- **Spring forward:** the mirror image, stepping **earlier** by 15 min/day.
- The plan is proposed in a check-in ("Daylight Saving Time ends Sunday. Want me to ease your bedroom lights 15 minutes a day starting Wednesday?"), never applied silently.

## Factor 3: Learned adjustments from conversational check-ins
- Every ad-hoc light change ("Alexa, dim the living room") is logged with its time and the scheduled time it deviated from.
- The pattern detector looks for **consistent deviations**: e.g. the user turned the living room on 15–25 min before the routine on 5 of the last 7 evenings.
- It can also notice **the wrong anchor**: the user's on-times track sunset better than the clock, so it suggests switching the routine to a solar anchor.
- Each finding becomes a **check-in**: a short conversational question with the evidence attached.

| Response | Effect |
|---|---|
| Accept | Apply the change (learned offset or anchor switch) |
| Adjust ("make it 10 minutes") | Apply the user's value instead |
| Decline | Suppress this suggestion for 14 days |

- **Check-ins are pulled, not pushed.** An MCP server can't start a conversation, so pending check-ins come back with tool results and the host raises them at the next natural moment.

## Precedence and safety
1. A one-off command today ("lights on now") always wins for that moment, and is logged as evidence.
2. Only **confirmed** changes alter a routine. Pending suggestions never apply themselves.
3. Every effective time can be explained factor by factor (`explain_light_time`).

## MCP tools (lights only)
| Tool | Purpose |
|---|---|
| `set_location` / `get_location` | Location and timezone from device or user input |
| `list_lights` | Lights and their current state |
| `set_light` | Ad-hoc on/off; logged as evidence |
| `get_schedule` | Effective routine times for a date, with factor breakdown |
| `explain_light_time` | "Why will the bedroom lights come on at 6:40?" |
| `get_checkins` | Pending conversational check-ins with evidence |
| `respond_to_checkin` | Accept / adjust / decline |
| `plan_dst_transition` | Preview the day-by-day glide for the next DST change |
| `set_demo_clock` | Time travel for demos (demo mode only) |
