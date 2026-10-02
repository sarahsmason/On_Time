# Friction Log

Log every snag the moment it happens. Judges award up to a 10% bonus for good friction logs.
Be specific: exact commands, versions, error text, and links to the docs you were reading.

Severity scale: **Blocker** (couldn't continue) · **Major** (lost > 1 hour) · **Minor** (annoying, quick fix) · **Papercut** (cosmetic/docs)

---

## Template (copy for each entry)

### FL-000: <short title>
- **Date:** 2026-MM-DD
- **Tool / SDK / Service (version):**
- **Task attempted:**
- **Steps taken:**
  1.
  2.
- **Expected result:**
- **Actual result:** (paste the exact error message)
- **Severity:** Blocker / Major / Minor / Papercut
- **Time lost:**
- **Workaround used:**
- **Actionable suggestion:** (a doc fix, feature, or better error message)

---

## Entries

<!-- Newest at the bottom. Number them FL-001, FL-002, ... -->

### FL-001: `aws agent-toolkit list-available-skills` crashes on Windows (charmap encoding)
- **Date:** 2026-10-02
- **Tool / SDK / Service (version):** AWS CLI 2.37.6 (bundled Python 3.14.6), Windows 11, PowerShell 5.1; Agent Toolkit for AWS
- **Task attempted:** Step 6 of the Agent Toolkit setup guide: verify the install by listing available skills.
- **Steps taken:**
  1. Ran `aws agent-toolkit list-available-skills --region us-east-1 --profile ontime`
- **Expected result:** JSON list of skills, exit code 0.
- **Actual result:** Partial JSON output, then `aws: [ERROR]: 'charmap' codec can't encode character '→' in position 878: character maps to <undefined>`, exit code 255. Skill descriptions contain Unicode (→, —) that the default Windows console code page cannot encode.
- **Severity:** Major (the official setup verification step fails out of the box on Windows)
- **Time lost:** ~5 min
- **Workaround used:** `$env:PYTHONIOENCODING = 'utf-8'` before running the command.
- **Actionable suggestion:** Have the CLI force UTF-8 output on Windows (or replace non-ASCII characters in skill descriptions), and add this case to `setup-troubleshooting.md` Step 6.

### FL-002: Unclear which sign-up path the hackathon needs (Builder ID vs AWS account vs "new AWS experience")
- **Date:** 2026-10-02
- **Tool / SDK / Service:** builder.aws.com, AWS sign-up, new AWS experience (social sign-in)
- **Task attempted:** Create the AWS account needed for Bedrock, hosting, and hackathon credits.
- **Steps taken:**
  1. Followed the hackathon rules link "AWS: Get started at builder.aws.com"
  2. Signed up with GitHub
- **Expected result:** Clear guidance on whether this creates an AWS account usable for Bedrock and credit redemption.
- **Actual result:** Three overlapping concepts (AWS Builder ID, classic AWS account, new AWS experience "project") and no guidance in the hackathon materials on which one is needed, or that the new experience is single-Region and Free plan by default.
- **Severity:** Minor
- **Time lost:** ~20 min
- **Workaround used:** Confirmed via `aws sts get-caller-identity` and `aws freetier get-account-plan-state` that the GitHub sign-up created a real account (Free plan, single Region us-east-2).
- **Actionable suggestion:** Add a short "which AWS sign-up do I need?" section to the hackathon resources, covering Region restrictions and where to redeem promotional credits for new-experience projects.

### FL-003: Bedrock models listed but every invocation fails with "Operation not allowed" (new AWS experience, Free plan)
- **Date:** 2026-10-02
- **Tool / SDK / Service (version):** Amazon Bedrock Runtime (Converse API), AWS CLI 2.37.6, new AWS experience project on the FREE plan, us-east-2
- **Task attempted:** Verify the project can call Claude on Bedrock before building the agent.
- **Steps taken:**
  1. `aws bedrock list-foundation-models --by-provider anthropic` listed Claude models in us-east-2
  2. `aws bedrock-runtime converse` with `anthropic.claude-haiku-4-5-...` and the `us.` inference profile
  3. Same call with `us.amazon.nova-micro-v1:0` (first-party model)
  4. `aws bedrock get-foundation-model-availability` returned `authorizationStatus: NOT_AUTHORIZED`, `agreementAvailability: NOT_AVAILABLE`
  5. `aws bedrock get-use-case-for-model-access` returned "You have not filled out the request form"
- **Expected result:** Either a successful response or an error stating exactly what is missing (plan upgrade, use-case form, or model agreement).
- **Actual result:** `ValidationException: Operation not allowed` for all models, including Amazon Nova. The models are listed, so it looks like they should work.
- **Severity:** Blocker (for the agent layer; core MCP work can continue)
- **Time lost:** ~15 min (so far)
- **Workaround used:** Submitted the Anthropic use-case form (in the correct project, see FL-004), which changed the models to `AUTHORIZED`; invocation then still needed a quota increase (see FL-005).
- **Actionable suggestion:** Return a specific error (e.g. "Bedrock model invocation requires the paid plan" or "Submit the Anthropic use-case form") and hide or flag models that can't be invoked on the current plan.

### FL-004: Two AWS projects; CLI and console silently on different accounts
- **Date:** 2026-10-02
- **Tool / SDK / Service (version):** new AWS experience (AWS Settings, multi-session console), AWS CLI 2.37.6 `aws login`
- **Task attempted:** Submit the Anthropic use-case form in the console and verify access from the CLI.
- **Steps taken:**
  1. Signed in to the CLI with `aws login --profile ontime` → account 3317-3029-7625
  2. Worked in the console from AWS Settings → URL showed account 3980-3615-8367
  3. Re-ran `aws login`; it initially re-used the 3317 session
- **Expected result:** One project per user, or `aws login` clearly showing which project and account it's signing in to, with a picker when several exist.
- **Actual result:** Two projects existed. Console changes (use-case form) applied to 3980 while CLI tests ran against 3317, producing confusing "not authorized" results for ~30 min.
- **Severity:** Major
- **Time lost:** ~30 min
- **Workaround used:** Compared the account ID in the console URL with `aws sts get-caller-identity`; re-ran `aws login` until the profile pointed to 3980.
- **Actionable suggestion:** `aws login` should print the project name and account ID on success and offer a project picker; AWS Settings should show which project each CLI session is bound to.

### FL-005: Bedrock authorized, but new account has 0 tokens/min quota and Sonnet 5 "not available for this account"
- **Date:** 2026-10-02
- **Tool / SDK / Service (version):** Amazon Bedrock Runtime (Converse), Service Quotas, us-east-2, new AWS experience project
- **Task attempted:** First call to Claude Haiku 4.5 and Claude Sonnet 5 after the Anthropic use-case form was accepted.
- **Steps taken:**
  1. `get-foundation-model-availability` → `authorizationStatus: AUTHORIZED`
  2. Converse with `anthropic.claude-haiku-4-5-...` → "on-demand throughput isn't supported; use an inference profile"
  3. Converse with `us.anthropic.claude-haiku-4-5-...` → `ThrottlingException: Too many tokens per day` on the very first call
  4. Converse with `anthropic.claude-sonnet-5` → `AccessDeniedException: not available for this account ... contact AWS Sales`
  5. Service Quotas: cross-Region tokens/min and requests/min for Haiku 4.5 and Nova Micro are **0**
- **Expected result:** A newly authorized model can serve at least a small number of test requests.
- **Actual result:** Authorized but unusable: quotas default to 0, the throttling message says "per day" even though no tokens were ever used, and the Sonnet 5 error points to Sales instead of a self-serve path.
- **Severity:** Blocker (for the agent layer)
- **Time lost:** ~20 min
- **Workaround used:** Requested quota increases in Service Quotas (us-east-2). Granted the same day: Haiku 4.5 cross-Region 5,000,000 tokens/min and 10 requests/min. The first call via `us.anthropic.claude-haiku-4-5-20251001-v1:0` then succeeded. Sonnet 5 is still "not available for this account", so the project uses Haiku 4.5 as the agent model.
- **Actionable suggestion:** Give new accounts a small non-zero default quota, say "your quota is 0" instead of "too many tokens per day", and link the throttling error directly to the Service Quotas increase page.

### FL-006: `npx @modelcontextprotocol/inspector` blocked in Windows PowerShell
- **Date:** 2026-10-02
- **Tool / SDK / Service (version):** MCP Inspector via npx, Node.js 24.19.0, Windows 11, Windows PowerShell 5.1
- **Task attempted:** Open MCP Inspector to test the local Streamable HTTP server, as shown in the MCP docs.
- **Steps taken:**
  1. Ran `npx @modelcontextprotocol/inspector` in PowerShell
- **Expected result:** Inspector starts and opens in the browser.
- **Actual result:** `npx.ps1 cannot be loaded because running scripts is disabled on this system` (PSSecurityException). Windows' default execution policy blocks the `npx.ps1` shim that Node installs.
- **Severity:** Minor
- **Time lost:** ~5 min
- **Workaround used:** Ran `npx.cmd @modelcontextprotocol/inspector` instead (no execution-policy change needed).
- **Actionable suggestion:** MCP and Inspector quickstarts should include a Windows note: use `npx.cmd` in PowerShell (or run from cmd.exe).

### FL-007: MCP Inspector v2 UI doesn't match the documented connection flow
- **Date:** 2026-10-02
- **Tool / SDK / Service (version):** MCP Inspector v2.9.0 (via `npx.cmd @modelcontextprotocol/inspector`)
- **Task attempted:** Connect Inspector to the local server at `http://127.0.0.1:8000/mcp` and call a tool.
- **Steps taken:**
  1. Followed the common guidance: pick "Streamable HTTP" in a left sidebar, paste the URL, click Connect
  2. Found instead a server-list dashboard with sample servers and no URL field
  3. Used **Add Servers → + Add manually**. A mouse click on the menu item did nothing; it had to be selected with the keyboard (Enter)
  4. Set Server ID `on-time`, Transport `streamable-http`, URL; toggled the card's switch
  5. The card kept showing "Disconnected" until the page refreshed its state, although the protocol log showed a successful `initialize`
- **Expected result:** The documented sidebar flow, or docs matching v2.
- **Actual result:** The connection worked (badge "MCP 2025-11-25"; `tools/call ping_time` OK in 141 ms), but only after figuring out the new UI by trial and error.
- **Severity:** Minor
- **Time lost:** ~15 min
- **Workaround used:** Add the server manually, then use the Tools tab → select tool → Execute Tool.
- **Actionable suggestion:** Update the Inspector README and MCP docs for the v2 flow; make the "Add manually" menu item respond to clicks; refresh the card's status as soon as `initialize` succeeds.
