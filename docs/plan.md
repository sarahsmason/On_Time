# On_Time: Build Plan

**Deadline:** Fri Oct 23, 2026, 12:00 PT. **Target submit:** Thu Oct 22.
**Track:** Alexa+ (self-hosted MCP server). **Mini challenges:** AWS Builder, plus Open Source as a stretch.

## Governing specs
| Spec | Version | Role |
|---|---|---|
| MCP core + Streamable HTTP transport | **2025-11-25** | Hard requirement (Stage 1 pass/fail) |
| MCP Apps extension (`ui://` resources) | **2026-01-26** | Rubric booster: interactive cards and timelines |

## AWS environment
| Setting | Value |
|---|---|
| Project type | New AWS experience (single Region; managed SCPs/RCPs) |
| Selected Region | **us-east-2**: all Regional resources go here (no cross-Region resources) |
| CLI profile | `ontime` (`aws login`) |
| Agent model | **Claude Haiku 4.5** via inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Bedrock quotas | Haiku 4.5: 5M tokens/min, **10 requests/min** (keep agent calls lean) |
| Not available | Claude Sonnet 5 ("not available for this account") |

## Transport compliance checklist (from the 2025-11-25 spec)
Every item has an automated test in `tests/test_transport.py`.

| # | Requirement | Spec level | Our approach |
|---|---|---|---|
| T1 | Single MCP endpoint supporting POST + GET (`/mcp`) | MUST | Python `mcp` SDK, Streamable HTTP |
| T2 | `initialize` negotiates `protocolVersion: 2025-11-25` | MUST (rules) | Assert in tests |
| T3 | POST request → `application/json` or `text/event-stream` | MUST | Stateless + JSON responses |
| T4 | Notification/response POST → `202 Accepted` | MUST | SDK; asserted in tests |
| T5 | GET → SSE stream **or** `405` | MUST | SSE stream (SDK default; compliant). No server push used. |
| T6 | Invalid `Origin` → `403` | MUST | Allow-list middleware |
| T7 | Unsupported `MCP-Protocol-Version` → `400` | MUST | SDK or middleware; asserted |
| T8 | Bind to `127.0.0.1` when local | SHOULD | Default host setting |
| T9 | Authentication on all connections | SHOULD | Bearer token locally → OAuth/Cognito or API key on AWS |
| T10 | Sessions (`MCP-Session-Id`) | MAY | **Stateless**: user memory lives in the DB, not the session |
| T11 | Resumability (`Last-Event-ID`) | MAY | Out of scope (documented) |

---

## Phase 0: Setup and spec spike (Sep 30 – Oct 3)
**Resources:** Python 3.12, uv, git, Node 24 (Inspector), GitHub, AWS account + CLI, Bedrock access
**Tasks**
- [x] Tools installed; repo public with MIT license; starter docs pushed
- [x] AWS credits form submitted
- [x] AWS project (new AWS experience, GitHub sign-in), CLI profile `ontime` via `aws login`
- [x] AWS Agent Toolkit installed (AWS MCP server + skills; rules in `CLAUDE.md`)
- [x] Bedrock: Anthropic use-case form accepted; Haiku 4.5 quota raised; test call succeeds
- [ ] AWS Settings: MFA on sign-in, spend limit
- [x] **Hello-world MCP server** (`/mcp`, one `ping_time` tool), MCP Python SDK 2.2.0
- [x] **Spec spike:** SDK negotiates 2025-11-25; T1–T8 and T10 covered by `tests/test_transport.py` (10 passing)
- [x] Verified in MCP Inspector v2.9.0: connected as "MCP 2025-11-25"; `ping_time` OK
- [ ] GitHub topics (optional)

**Artifacts:** `server/` hello world · `tests/test_transport.py` (first checks) · first friction log entries

## Phase 1: Compliant MCP core and simulated home (Oct 4 – Oct 8)
**Resources:** `mcp` SDK, `pydantic`, SQLite, `pytest`, `httpx`, MCP Inspector
**Tasks**
- Harden the transport: Origin allow-list (T6), version check (T7), bearer auth (T9), stateless mode (T10)
- Data model (see [scheduling-model.md](scheduling-model.md)): location, lights, routines (clock or solar anchor + learned offset), adjustment log, check-ins
- Simulated lights: bedroom, living room, kitchen, porch (state: on/off, brightness, warmth)
- Location from device (host-supplied) or user input (city / ZIP / lat-lon) → lat/lon + IANA timezone
- **Tools** (clear descriptions and JSON schemas for Alexa+ / LLM tool selection):
  `set_location`, `get_location`, `list_lights`, `set_light`, `get_schedule`, `explain_light_time`,
  `get_checkins`, `respond_to_checkin`, `plan_dst_transition`, `set_demo_clock`
- Seed script: 30 days of realistic ad-hoc light adjustments with a few clear patterns
- Time-travel clock (server-side "now" override, used only in demo mode)

**Artifacts:** `v0.1` tag · full transport test suite green (T1–T9) · README "Run locally" section · Inspector screenshots for the write-up

## Phase 2: Intelligence engine (Oct 9 – Oct 12)
**Resources:** `astral` (sunrise/sunset), `zoneinfo` (DST)
**Tasks**
- **Factor 1, base time:** clock anchors and solar anchors (sunrise/sunset ± offset at the user's location)
- **Factor 2, DST glide:** step clock-anchored light routines 60/N min/day across the N days before a change (default 6; fall-back Nov 1, 2026)
- **Factor 3, learned offset:** detect consistent deviations in ad-hoc adjustments (support + confidence + evidence), including "track sunset instead of the clock"
- Check-in queue: pending → accepted / adjusted / declined (declined suppressed 14 days)
- Pull-based surfacing: tool results carry pending check-ins (servers cannot start conversations)
- `explain_light_time`: factor-by-factor breakdown for any routine and date

**Artifacts:** `v0.2` tag · engine tests (DST boundary days, midnight wrap, weekends)

## Phase 3: MCP Apps UI and simulated Alexa+ host (Oct 13 – Oct 16)
**Resources:** MCP Apps spec 2026-01-26, `@modelcontextprotocol/ext-apps` (view + app-bridge), Strands Agents SDK, Bedrock Claude, FastAPI
**Tasks**
- **MCP Apps** (`ui://` resources served by the Python server):
  - `ui://on-time/suggestion-card`: explanation + Approve/Decline (calls `apply_change` / `decline_suggestion`)
  - `ui://on-time/schedule-timeline`: before/after view, DST glide day-by-day
- **Simulated Alexa+ host** web app:
  - Voice in/out (browser speech) + chat
  - Agent: Strands + Claude on Bedrock, connected to our server as an **MCP client over Streamable HTTP**
  - Renders our MCP Apps via app-bridge; time-travel control for the demo
- Verify the same server and UI work in a second MCP Apps host (Claude or VS Code)

**Artifacts:** `v0.3` tag · demo script draft · screenshots/GIFs of cards in two hosts

## Phase 4: AWS deployment and stretch goals (Oct 17 – Oct 19)
**Resources:** Bedrock AgentCore Runtime (fallback: App Runner or Fargate), DynamoDB, CloudWatch, Secrets Manager
**Tasks**
- Deploy the MCP server over **HTTPS** with auth enforced (T9); run the transport suite against the deployed URL
- Swap SQLite → DynamoDB behind the same repository interface
- Deploy the simulated Alexa+ host; public demo URL + judge test credentials
- Stretch (pick ≤2, cut in this order last→first):
  1. Connect a real Alexa+ device to the deployed server
  2. Home Assistant adapter (one real light)
  3. Open Source mini challenge: extract the DST/seasonal planner as a standalone MIT library with tests

**Artifacts:** `v0.4` tag · README "AWS architecture" section · deployed URLs

## Phase 5: Submission (Oct 20 – Oct 22)
**Artifacts**
1. Final README: one-command local run, deployed URL, judge credentials, architecture diagram, **spec compliance table (T1–T11)**
2. Demo video < 3 min (YouTube): problem → set location → check-in from adjustment pattern → sunset-anchored lights → DST glide timeline → architecture/AWS → close
3. Devpost description mapped to the judging rubric
4. Product feedback for each tool (MCP SDK, Inspector, MCP Apps, Strands, Bedrock, AgentCore, DynamoDB, Alexa+)
5. Friction log (cleaned up), feature requests
6. Mini challenge entries (AWS Builder; Open Source if done)

**Submit Oct 22.** Oct 23 morning is buffer only.

## Cut order if behind
Home Assistant → real Alexa+ → open-source library → second MCP Apps host → voice input.
**Never cut:** transport compliance (T1–T9), MCP Apps suggestion card, time-travel demo, friction log.
