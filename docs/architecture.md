# On Time: Architecture

An agentic **lighting** assistant for Alexa+. Each light routine's time combines three factors:
a gradual shift around Daylight Saving Time changes, sunrise/sunset at the user's location (from the
device or user input), and adjustment patterns the user confirms in conversational check-ins.
See [scheduling-model.md](scheduling-model.md).

```
[Simulated Alexa+ web app]  voice (browser speech) + chat + cards/timeline + time-travel clock
          │
[Agent: Strands SDK + Claude Haiku 4.5 on Amazon Bedrock (us-east-2)]
          │  MCP client (Streamable HTTP, spec 2025-11-25)
[On Time MCP server (Python, official `mcp` SDK)]
   tools: set_location, list_lights, set_light, get_schedule, explain_light_time,
          get_checkins, respond_to_checkin, plan_dst_transition, set_demo_clock
          │
[Engine]  base time (clock | sunrise/sunset via astral) + DST glide + learned offset
          adjustment-pattern detector · check-in queue
          │
[Device layer]  Simulated lights (demo)  →  later: Home Assistant / real lights
[Store]  SQLite (local)  →  DynamoDB (AWS)
```

## Key design decisions
- **Lights only.** Narrow scope, easy to demo, and easy to connect to real lights later.
- **Check-ins are pulled, not pushed.** An MCP server can't start a conversation, so check-ins
  wait in a queue and come up the next time the user talks to Alexa+ or opens the web view.
- **The user approves every change.** On Time never changes a routine without a yes.
- **Every time explains itself**, factor by factor, e.g. "sunset 6:28 − 20 min, + 15 min you
  confirmed, + 15 min DST glide (day 1 of 4)".
- **A time-travel clock** makes 30 days of behavior and the DST transition demoable in minutes.

## Specs
- MCP core + Streamable HTTP transport **2025-11-25** (stateless, Origin validation, auth)
- MCP Apps extension **2026-01-26**: `ui://on-time/suggestion-card`, `ui://on-time/schedule-timeline`

## Milestones
See [plan.md](plan.md) for full phase details and the transport compliance checklist.

| Tag | Target date | Contents |
|---|---|---|
| spike | Oct 3 | Hello-world server; 2025-11-25 negotiation + Origin/version checks proven |
| v0.1 | Oct 8 | Compliant MCP core (T1–T9 tests) + simulated hub + seed data |
| v0.2 | Oct 12 | Habit, seasonal, and DST engines + tests |
| v0.3 | Oct 16 | MCP Apps cards/timeline + simulated Alexa+ host (Strands + Bedrock) |
| v0.4 | Oct 19 | AWS deployment (HTTPS, auth, DynamoDB) + stretch goals |
| submit | Oct 22 | Video, description, feedback, friction log |
