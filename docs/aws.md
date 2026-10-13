# AWS Integration: Claude on Amazon Bedrock

On Time's assistant runs on **Amazon Bedrock**. Claude Haiku 4.5 reads the conversation, decides
which On Time tools to call, and writes the reply. The browser page makes every tool call over MCP.
Bedrock is optional: without it, an offline agent runs the same demo. With it, the assistant
understands free-form requests.

## At a glance
- **Service:** Amazon Bedrock Runtime, the `Converse` API with tool use (`toolConfig`).
- **Model:** Claude Haiku 4.5, through the US cross-Region inference profile
  `us.anthropic.claude-haiku-4-5-20251001-v1:0`.
- **Region:** us-east-2.
- **SDK:** boto3, installed as `boto3[crt]` so it can use `aws login` credentials.
- **Code:** [`src/on_time/agent.py`](../src/on_time/agent.py) (`BedrockAgent` and `next_step`), the
  `/agent` route in [`server.py`](../src/on_time/server.py), and the turn loop in
  [`web/app.js`](../src/on_time/web/app.js).
- **Tests:** [`tests/test_agent_bedrock.py`](../tests/test_agent_bedrock.py), which uses a stand-in
  for Bedrock to check tool calls, the allowlist, and the fallback.
- **How to turn it on:** set `ON_TIME_AGENT=bedrock` and `AWS_PROFILE=<profile>` (see the README).

## How one turn works
```
Browser (Simulated On Time for Alexa+)          On Time server                       Amazon Bedrock
  │ user: "Turn on the living room"
  │ tools/list over MCP  ─────────────────────► MCP server (13 tools)
  │ POST /agent {conversation, tools} ────────► BedrockAgent.step
  │                                               MCP inputSchema → Converse toolSpec ──► Converse (Claude Haiku 4.5)
  │                                               ◄── stopReason: tool_use, toolUse: set_light
  │                                               allowlist check
  │ ◄──────────── {tool_calls: [set_light]}
  │ tools/call set_light over MCP ────────────► MCP server → result + next_checkin
  │ POST /agent {…, toolResult} ──────────────► Converse ──► toolUse: get_checkins … then text
  │ ◄──────────── "Done. By the way: you've turned the living room on about 20 minutes early…"
```

1. **MCP tools become Bedrock tools.** The page sends the tools it got from `tools/list`. Each tool's
   input description (its JSON Schema) is passed to Bedrock unchanged, so any new MCP tool is
   available to Claude right away.
2. **Claude picks the next step:** either tool calls or a reply. Its instructions say to ask a
   check-in as soon as a result offers one, never to claim a change without making the tool call,
   and to repeat the server's summary instead of working out times itself.
3. **The browser makes the calls** over MCP (`tools/call`, Streamable HTTP, 2025-11-25). They appear
   live in the page's MCP protocol panel, and the results go back to Claude. A turn can take up to
   6 steps.
4. **The server does the exact parts.** Times, Daylight Saving dates, and confirmations like
   "now runs at 6:15 PM" come from the tool results, so Claude words the answer but never calculates it.

## Safety and reliability
- **Claude's tool calls are checked.** Only tools the page offered are accepted, the inputs must be
  well-formed, and the demo-clock tool is hidden from Claude.
- **No change without a yes.** Routines change only through `respond_to_checkin`, after the user answers.
- **Quick failure, smooth fallback.** At most 2 attempts with adaptive retry, a 5-second connect
  timeout, and a 30-second read timeout. If Bedrock has any problem (throttling, expired sign-in, a
  quota of 0), the turn continues on the offline agent, Bedrock is skipped for 60 seconds, and the
  page header shows which agent answered.
- **Small, cheap calls.** At most 400 output tokens, temperature 0.2, and only the last 40 messages
  are sent.

## What live testing on Bedrock taught us
The first full conversation on Bedrock (Oct 9) exposed four mistakes. Each was fixed by giving
Claude exact information in the tool results, not just by changing its instructions:

1. **It asked the check-in too late,** several turns after the evidence appeared. Now everyday tool
   results include the next check-in (`next_checkin`), so Claude asks right away.
2. **It described the Daylight Saving shift in the wrong direction.** Now `plan_dst_transition`
   returns a plain-language summary (`glide_summary`) to repeat.
3. **It said "done" without calling a tool.** Now a rule forbids it, and `respond_to_checkin`
   returns a summary that the reply must repeat.
4. **It did time math wrong** (5:50 PM instead of 6:10 PM) and answered from a routine's base
   setting. Now `list_routines` includes today's real time, and every change comes with its exact
   new time, so there's no arithmetic left for Claude to do.

## What the second live test taught us (Oct 13)
After adding routines and more demo homes, a full re-test on Bedrock (55 steps across five demo
homes) found three more problems, all now fixed and covered by tests:

1. **The conversation window broke Bedrock after about 10 exchanges.** The page sends only the latest
   40 messages, and that window could open on a tool result whose tool call had been cut off, which
   Bedrock rejects (`ValidationException`). The offline agent took over, as designed. Now the window
   always starts at something the user said.
2. **Claude removed the wrong routine.** Asked to "remove the porch evening routine" right after adding
   a new porch routine, it deleted the new one. Now it must look routines up by name first.
3. **A check-in outlived its routine.** After a routine was removed, its pending check-in kept being
   asked. Now check-ins for removed routines are dropped.

Two smaller fixes came out of the same run: full US state names ("Phoenix, Arizona") are accepted as
Claude passes them, and Claude finishes the user's request before raising a check-in. The final run:
55 steps, every one answered by Bedrock, with no fallbacks.

## AWS in the development workflow
- **Agent Toolkit for AWS**, connected to Claude Code (the coding assistant), gave AWS guidance while
  building: the new AWS experience, single-Region rules, and Bedrock setup.
- **The AWS CLI** (`aws login`, `bedrock`, `bedrock-runtime converse`, `service-quotas`, `sts`)
  checked access and diagnosed errors.
- **Service Quotas** was used to request the Haiku 4.5 quota for a new account.

Every snag along the way (the use-case form, quotas at 0, two projects, `aws login` with boto3) is in
the [friction log](friction-log.md): FL-001 to FL-005, FL-009, and FL-010.
