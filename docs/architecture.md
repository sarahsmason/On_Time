# On Time: Architecture

An agentic lighting and schedule assistant for Alexa+. It learns habits from ad-hoc requests, tracks
seasonal (sunrise/sunset) drift, and plans gradual Daylight Saving Time transitions. It proposes
schedule changes, and the user approves each one.

```
[Simulated Alexa+ web app]  voice (browser speech) + chat + cards/timeline + time-travel clock
          │
[Agent: Strands SDK + Claude on Amazon Bedrock]
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

## Milestones
| Tag | Target date | Contents |
|---|---|---|
| v0.1 | Oct 8 | MCP server + simulated hub + seed data, verified in MCP Inspector |
| v0.2 | Oct 13 | Habit, seasonal, and DST engines + tests |
| v0.3 | Oct 17 | Simulated Alexa+ app + AWS deployment |
| v0.4 | Oct 20 | Stretch goals (real Alexa+ / Home Assistant / open-source library) |
| submit | Oct 22 | Video, description, feedback, friction log |
