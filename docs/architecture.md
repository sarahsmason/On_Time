# On Time: Architecture

An agentic lighting and schedule assistant for Alexa+. It learns habits from ad-hoc requests, tracks
seasonal (sunrise/sunset) drift, and plans gradual Daylight Saving Time transitions. It proposes
schedule changes, and the user approves each one.

```
[Simulated Alexa+ web app]  voice (browser speech) + chat + cards/timeline + time-travel clock
          │
[Agent: Strands SDK + Claude Haiku 4.5 on Amazon Bedrock (us-east-2)]
          │  MCP client (Streamable HTTP, spec 2025-11-25)
[On Time MCP server (Python, official `mcp` SDK)]
   tools: log_action, get_suggestions, apply_change, plan_dst_glide,
          get_schedule, preview_season_drift
          │
[Engine]  habit detection · sunrise/sunset (astral) · DST glide planner · suggestion queue
          │
[Device layer]  Simulated hub (demo)  →  later: Home Assistant / real devices
[Store]  SQLite (local)  →  DynamoDB (AWS)
```

## Key design decisions
- **Suggestions are pulled, not pushed.** An MCP server can't start a conversation, so suggestions
  wait in a queue and are shown the next time the user interacts with Alexa+ or opens the web view.
- **The user approves every change.** On Time never changes a schedule without a yes.
- **Every suggestion explains itself**, e.g. "5 of the last 7 weekdays around 6:40am".
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
