# Friction Log

These are the snags I hit while building On Time with AWS, Amazon Bedrock, the Alexa+ docs, and
MCP tools, from Oct 2 to Oct 11, 2026, on Windows 11. Each entry says what happened, how to
reproduce it, how I got past it, and what would fix it. Feature requests, ranked, are at the end.

**In short:** 12 issues, about 5.5 hours lost. The three blockers were all about getting access
(AWS account setup, Bedrock, and Alexa+), not about building the MCP server itself.

**Severity**
- **Blocker:** stopped that line of work, with no workaround I could control at the time.
- **Major:** the documented path failed or misled me, and getting past it took 30+ minutes or a re-plan.
- **Minor:** the documented path failed, but a quick workaround (under 30 minutes) existed.

**Status:** *Resolved* means fixed at the source. *Worked around* means it still happens and I
avoid it. *Open* means no fix is available to me.

## All issues at a glance
- **FL-001** AWS CLI crashes on Windows when listing Agent Toolkit skills · Minor · ~5 min · Worked around
- **FL-002** Unclear which AWS sign-up the hackathon needs · Minor · ~20 min · Resolved
- **FL-003** Bedrock lists models, but every call says "Operation not allowed" · Blocker · ~15 min · Resolved
- **FL-004** CLI and console silently on two different AWS projects · Major · ~30 min · Resolved
- **FL-005** Bedrock authorized, but the quota is 0 · Blocker · ~20 min · Resolved for Haiku 4.5
- **FL-006** `npx` blocked by PowerShell's execution policy · Minor · ~5 min · Worked around
- **FL-007** MCP Inspector v2 doesn't match the documented steps · Minor · ~15 min · Worked around
- **FL-008** Alexa+ MCP Toolkit is partner-only, but the setup pages don't say so · Blocker · ~2 h · Open
- **FL-009** boto3 needs an extra package for `aws login`; session expired early · Minor · ~15 min · Worked around
- **FL-010** Bedrock quotas silently reset to 0 a week later · Major · ~35 min · Worked around
- **FL-011** MCP Apps capability not advertised on a 2025-11-25 connection · Minor · ~20 min · Worked around
- **FL-012** Building a local web host for MCP Apps cards · Minor · ~30 min · Resolved

---

## FL-001 · AWS CLI crashes on Windows when listing Agent Toolkit skills
*Oct 2 · Minor · ~5 min · Worked around*
*AWS CLI 2.37.6, Windows 11, PowerShell 5.1, Agent Toolkit for AWS*

**What happened:** Step 6 of the Agent Toolkit setup guide verifies the install by listing skills.
On Windows it printed part of the list, then crashed with
`'charmap' codec can't encode character '→'` (exit code 255). Skill descriptions contain
characters like → and — that the default Windows console can't print, so the guide's own check
fails out of the box.

**To reproduce:** On Windows with default settings, run
`aws agent-toolkit list-available-skills --region us-east-1 --profile <profile>`.

**Workaround:** Run `$env:PYTHONIOENCODING = 'utf-8'` first.

**Suggestion:** Have the CLI always print UTF-8 on Windows (or keep skill descriptions plain
ASCII), and add this case to the setup troubleshooting page.

## FL-002 · Unclear which AWS sign-up the hackathon needs
*Oct 2 · Minor · ~20 min · Resolved*

**What happened:** The rules say "AWS: Get started at builder.aws.com." Signing up there with
GitHub, I ran into three overlapping ideas: an AWS Builder ID, a classic AWS account, and a "new
AWS experience" project. Nothing in the hackathon materials says which one is needed for Bedrock
and credits, or that the new experience is limited to one Region and starts on the Free plan.

**To reproduce:** Follow the rules' link to builder.aws.com and sign up with GitHub.

**Workaround:** Confirmed with `aws sts get-caller-identity` and
`aws freetier get-account-plan-state` that the sign-up had created a real account (Free plan,
Region us-east-2).

**Suggestion:** Add a short "Which AWS sign-up do I need?" note to the hackathon resources,
covering Regions and where to redeem promotional credits.

## FL-003 · Bedrock lists models, but every call says "Operation not allowed"
*Oct 2 · Blocker for the agent (server work continued) · ~15 min, then FL-004 and FL-005 · Resolved*
*Amazon Bedrock Runtime (Converse API), AWS CLI 2.37.6, new AWS experience, Free plan, us-east-2*

**What happened:** `aws bedrock list-foundation-models` listed Claude models, so they looked
usable. But every `converse` call, even to Amazon's own Nova model, failed with
`ValidationException: Operation not allowed`. Only after digging did two other commands explain
it: the model was `NOT_AUTHORIZED`, and "You have not filled out the request form."

**To reproduce:**
1. `aws bedrock list-foundation-models --by-provider anthropic` (models appear)
2. `aws bedrock-runtime converse` with a Claude Haiku 4.5 model ID (fails)
3. `aws bedrock get-use-case-for-model-access` ("You have not filled out the request form")

**Workaround:** Submitted the Anthropic use-case form in the right project (see FL-004). That
authorized the models; calls then needed a quota increase (FL-005).

**Suggestion:** Return an error that names the missing step ("Submit the Anthropic use-case
form") and mark models in the list that can't be called yet.

## FL-004 · CLI and console silently on two different AWS projects
*Oct 2 · Major · ~30 min · Resolved*
*New AWS experience (AWS Settings, console), AWS CLI 2.37.6 `aws login`*

**What happened:** I had two projects. `aws login` signed the CLI in to project A, while the
console I opened from AWS Settings was in project B. I submitted the use-case form in the console
(project B) and kept testing from the CLI (project A), which kept saying "not authorized."
Re-running `aws login` reused the project A session.

**To reproduce:** With two projects, sign in with `aws login`, then open the console from AWS
Settings and compare the account ID in its URL with `aws sts get-caller-identity`.

**Workaround:** Compared the two account IDs and re-ran `aws login` until the CLI pointed at
project B.

**Suggestion:** Have `aws login` print the project name and account ID, offer a project picker,
and show in AWS Settings which project each CLI session uses.

## FL-005 · Bedrock authorized, but the quota is 0
*Oct 2 · Blocker for the agent · ~20 min · Resolved for Haiku 4.5; Sonnet 5 still unavailable*
*Amazon Bedrock Runtime, Service Quotas, us-east-2*

**What happened:** After the form, the models showed `AUTHORIZED`, but the very first call failed
with `ThrottlingException: Too many tokens per day`, though no tokens had ever been used. Service
Quotas showed the limits for Haiku 4.5 were **0**. Claude Sonnet 5 said "not available for this
account… contact AWS Sales," with no self-serve option.

**To reproduce:**
1. On a new account, call Converse with the `us.anthropic.claude-haiku-4-5-…` inference profile
2. Check the Haiku 4.5 cross-Region quotas in Service Quotas (both 0)

**Workaround:** Requested quota increases in Service Quotas; they were granted the same day (5M
tokens/min, 10 requests/min). The next call worked. The agent uses Haiku 4.5.

**Suggestion:** Give new accounts a small non-zero quota, say "your quota is 0" instead of "too
many tokens per day," and link the error to the quota request page.

## FL-006 · `npx` blocked by PowerShell's execution policy
*Oct 2 · Minor · ~5 min · Worked around*
*MCP Inspector via npx, Node.js 24.19.0, Windows PowerShell 5.1*

**What happened:** Running `npx @modelcontextprotocol/inspector`, as the MCP docs show, failed
with `npx.ps1 cannot be loaded because running scripts is disabled on this system`. Windows'
default policy blocks the `npx.ps1` script that Node installs.

**To reproduce:** In PowerShell with default settings, run `npx @modelcontextprotocol/inspector`.

**Workaround:** Run `npx.cmd @modelcontextprotocol/inspector` instead; no policy change needed.

**Suggestion:** Add a one-line Windows note to the MCP and Inspector quickstarts: use `npx.cmd`
in PowerShell.

## FL-007 · MCP Inspector v2 doesn't match the documented steps
*Oct 2 · Minor · ~15 min · Worked around*
*MCP Inspector v2.9.0*

**What happened:** The common guidance says to pick "Streamable HTTP" in a sidebar, paste the URL,
and click Connect. Version 2 shows a server dashboard instead, with no URL field. Under
**Add Servers → + Add manually**, clicking the menu item did nothing; only the keyboard (Enter)
worked. After connecting, the server card kept saying "Disconnected" even though the log showed a
successful connection. Once past that, tools worked fine (MCP 2025-11-25, a call in 141 ms).

**To reproduce:** Start Inspector v2.9.0 and try to connect to `http://127.0.0.1:8000/mcp`.

**Workaround:** Add the server manually with the keyboard, then use Tools → choose a tool →
Execute Tool.

**Suggestion:** Update the Inspector README and MCP docs for v2, make "Add manually" respond to
clicks, and refresh the card's status as soon as the connection succeeds.

## FL-008 · Alexa+ MCP Toolkit is partner-only, but the setup pages don't say so
*Oct 6, updated Oct 9 · Blocker for testing on a real Alexa+ device · ~2 h · Open*
*Alexa+ MCP Toolkit (Preview) docs, `alexa-ai` CLI*

**What happened:** The hackathon rules link only to modelcontextprotocol.io; I found the Alexa+
MCP Toolkit through a web search. Its Quickstart, Overview, Authentication, and Testing pages:
- use `alexa-ai` commands but never show how to install it;
- require OAuth 2.1 service tokens and say Alexa+ doesn't support `WWW-Authenticate` on a 401,
  which the MCP authorization spec relies on;
- don't accept static API keys, so a simple bearer token (fine for other MCP clients) won't work.

On Oct 9 the hackathon FAQ said these tools are a **partner-only Preview** that participants
can't use. The docs home page says so, but none of the setup pages I read did, so I planned a
whole phase around tools I could never access.

**To reproduce:** Start from the hackathon rules, find the toolkit by search, and read its
Quickstart, Overview, Authentication, and Testing pages.

**Workaround:** Re-planned twice (real device on Oct 6, simulated client on Oct 9). The demo runs
in my own browser page, which is a real MCP client: the FAQ-approved simulated path.

**Suggestion:** Put a "Preview: select partners only" banner on every toolkit page, say plainly in
the hackathon rules that a real Alexa+ device connection isn't available, offer participants a
sandbox or simulator, and explain how Alexa+'s 401 rules relate to the MCP authorization spec.

## FL-009 · boto3 needs an extra package for `aws login`; session expired early
*Oct 9 · Minor · ~15 min · Worked around*
*boto3/botocore 1.43.110, AWS CLI 2.37.6 `aws login`, Amazon Bedrock Runtime*

**What happened:** Using the `aws login` profile from Python failed with
`MissingDependencyException … pip install "botocore[crt]"`. After adding that package, about a
week after the first login, it failed again with `LoginRefreshRequired: Your session has expired`,
though the setup guide says sessions renew for 90 days.

**To reproduce:** Create a `boto3.Session()` with an `aws login` profile, without `botocore[crt]`
installed; then retry about a week later.

**Workaround:** Added `boto3[crt]` and ran `aws login` again. Meanwhile the app fell back to its
offline agent.

**Suggestion:** Include the needed package by default, mention it in the setup guide, and explain
when a login actually expires versus the 90-day renewal.

## FL-010 · Bedrock quotas silently reset to 0 a week later
*Oct 9 · Major · ~35 min (20 diagnosing, 15 waiting) · Worked around*
*Amazon Bedrock Runtime, Service Quotas, us-east-2*

**What happened:** Quotas that worked on Oct 2 were back to **0** on Oct 9, with no notice and no
request from me in the history. Every call failed with "Too many tokens per day," a limit Service
Quotas doesn't list. In my second project, one test call worked and the next failed with "Model use
case details have not been submitted for this account": the use-case form is needed per account,
yet a call can succeed before it's filed.

**To reproduce:**
1. Call Converse on Haiku 4.5 a week after quotas were granted
2. `get-service-quota` for Haiku 4.5 cross-Region tokens/min (L-58BE175A) and requests/min
   (L-CCA5DF70) shows 0; the AWS default shows 5,000,000 and 10,000
3. `list-requested-service-quota-change-history` is empty

**Workaround:** Switched to my other project (healthy limits) and submitted the use-case form
there. The app's offline agent kept the demo working in the meantime.

**Suggestion:** Notify accounts when quotas are lowered, show the daily token limit in Service
Quotas, say "your quota is 0" in the error, check the use-case form consistently, and give
hackathon participants a small guaranteed quota.

## FL-011 · MCP Apps capability not advertised on a 2025-11-25 connection
*Oct 11 · Minor · ~20 min · Worked around*
*MCP Python SDK 2.2.0 (`Apps` extension), MCP Apps 2026-01-26, MCP core 2025-11-25*

**What happened:** I added on-screen cards with the SDK's MCP Apps extension. The Alexa+ track
requires the 2025-11-25 protocol, and on that connection the server's capabilities had no
`extensions` field, so it looked like the cards weren't registered. In fact the SDK removes that
field on purpose, because it belongs to a newer protocol version. Neither the SDK nor the MCP Apps
docs mention this.

**To reproduce:** Register `Apps(...)` on an `MCPServer`, connect with protocol version
2025-11-25, and look at the server capabilities in the `initialize` result.

**Workaround:** Hosts find cards through each tool's `_meta.ui.resourceUri`, which works on
2025-11-25. My tests now check for that.

**Suggestion:** Say in the MCP Apps spec and SDK docs how cards are discovered on 2025-11-25, and
have the SDK log a note when it drops a capability.

## FL-012 · Building a local web host for MCP Apps cards
*Oct 11 · Minor · ~30 min · Resolved*
*MCP Apps 2026-01-26, my browser host and sandbox page, Edge/Chrome, one local server*

**What happened:** The spec says web hosts must show cards through a sandbox page on a different
web address than the host. With one local server, it took some trial to land on `127.0.0.1` for
the app and `localhost` for the sandbox. Then the card showed as a plain white box on the dark
page (a browser rule for frames whose color scheme differs from the page), and the card's first
size report was 0×0, which collapsed it.

**To reproduce:** Show a card on a dark host page through a sandbox frame, with no
`color-scheme` set and the size sent before layout.

**Workaround:** Served the sandbox from the other local address, set `color-scheme: dark` in it,
and had cards measure their content and never report a zero size.

**Suggestion:** Ship a small reference web host and sandbox with the MCP Apps docs, including a
local-development recipe, and mention `color-scheme` in the theming guidance.

---

## Feature requests, by priority

**Critical**
1. Let hackathon participants test on a real Alexa+ device (a sandbox, simulator access, or
   temporary Preview access), and say clearly in the rules what is and isn't available. *(FL-008)*
2. Give new AWS accounts a small non-zero Bedrock quota, and notify accounts when quotas are
   lowered. *(FL-005, FL-010)*
3. Make Bedrock errors name the missing step (use-case form, plan, or "your quota is 0") instead of
   "Operation not allowed" or "Too many tokens per day." *(FL-003, FL-005, FL-010)*

**Important**
4. Put a "Preview: select partners only" banner on every Alexa+ MCP Toolkit page, show how to
   install `alexa-ai`, and explain how its 401 handling fits the MCP authorization spec. *(FL-008)*
5. Have `aws login` show the project and account it signed in to, with a project picker. *(FL-004)*
6. Let the AWS SDKs use `aws login` without extra packages, and document how long sessions last. *(FL-009)*
7. Add "Which AWS sign-up do I need?" to the hackathon resources. *(FL-002)*
8. Explain in the MCP Apps docs how cards are discovered on 2025-11-25 servers. *(FL-011)*

**Nice to have**
9. UTF-8 output from the AWS CLI on Windows. *(FL-001)*
10. A Windows note (`npx.cmd`) in the MCP quickstarts. *(FL-006)*
11. Updated MCP Inspector v2 docs, plus fixes for "Add manually" and the connection status. *(FL-007)*
12. A reference web host and sandbox for MCP Apps, with a local-development recipe. *(FL-012)*
