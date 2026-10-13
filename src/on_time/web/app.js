// Simulated On Time for Alexa+: the page is a real MCP client of the On Time server at /mcp.
import { AppHost } from "/web/apps-host.js";
import { McpClient, McpError } from "/web/mcp-client.js";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "on-time-token";

let client;
let protocolCount = 0;

// -- formatting (times arrive as ISO strings in the home timezone; keep their wall-clock values) --

function wallTime(iso) {
  const [h, m] = iso.slice(11, 16).split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${suffix}`;
}

function wallDay(iso, opts = { weekday: "short", month: "short", day: "numeric" }) {
  const [y, mo, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, d)).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

// -- conversation ------------------------------------------------------------------------

export function say(role, text) {
  const box = $("messages");
  box.append(el("div", { class: `msg ${role}` }, text));
  box.scrollTop = box.scrollHeight;
}

// -- protocol panel ----------------------------------------------------------------------

function logProtocol({ message, reply, status, ms }) {
  protocolCount += 1;
  $("protocol-count").textContent = protocolCount;
  const name = message.method === "tools/call" ? ` ${message.params.name}` : "";
  const isError = status >= 400 || reply?.error || reply?.result?.isError;
  const item = el(
    "li",
    {},
    el(
      "details",
      {},
      el(
        "summary",
        {},
        el("span", { class: "method" }, `→ ${message.method}${name}`),
        el("span", { class: isError ? "st-bad" : "st-ok" }, `${status}${reply?.result?.isError ? " tool error" : ""}`),
        el("span", { class: "muted" }, `${ms} ms`),
      ),
      el("pre", {}, "request  ", JSON.stringify(message, null, 2)),
      el("pre", {}, "response ", reply ? JSON.stringify(reply, null, 2) : "(202 Accepted, no body)"),
    ),
  );
  $("protocol").prepend(item);
}

// MCP Apps traffic between this page (the host) and a card, over postMessage.
function logCard(direction, msg) {
  protocolCount += 1;
  $("protocol-count").textContent = protocolCount;
  const label = msg.method || (msg.error ? "error" : "result");
  const name = msg.method === "tools/call" ? ` ${msg.params?.name}` : "";
  $("protocol").prepend(
    el(
      "li",
      {},
      el(
        "details",
        {},
        el("summary", {}, el("span", { class: "method app" }, `${direction}  ${label}${name}`), el("span", { class: "muted" }, "MCP Apps")),
        el("pre", {}, JSON.stringify(msg, null, 2).slice(0, 4000)),
      ),
    ),
  );
}

// -- tool helpers ------------------------------------------------------------------------

export async function call(name, args = {}) {
  const result = await client.callTool(name, args);
  if (!result.ok) throw new Error(result.text || `${name} failed`);
  return result.data;
}

// -- panels --------------------------------------------------------------------------------

function renderClock(loc) {
  if (loc.status !== "set") {
    $("clock-time").textContent = "--:--";
    $("clock-meta").textContent = "Location not set";
    return;
  }
  $("clock-time").textContent = wallTime(loc.now);
  $("clock-meta").textContent = `${wallDay(loc.now, { weekday: "long", month: "short", day: "numeric" })} · ${loc.location.name}`;
  $("demo-toggle").classList.toggle("demo-on", loc.demo_clock);
  $("demo-toggle").textContent = loc.demo_clock ? "Demo time ●" : "Demo time";
}

function renderLights(data) {
  $("lights").replaceChildren(
    ...data.lights.map((light) =>
      el(
        "button",
        {
          class: `light${light.is_on ? " on" : ""}`,
          type: "button",
          "aria-pressed": String(light.is_on),
          title: `Turn ${light.name} ${light.is_on ? "off" : "on"}`,
          onclick: () => act(() => call("set_light", { light_id: light.id, action: light.is_on ? "off" : "on" })),
        },
        el("span", { class: "bulb" }),
        light.name,
        el("span", { class: "state" }, light.is_on ? "On" : "Off"),
      ),
    ),
  );
}

function renderCheckins(data) {
  const list = data.checkins;
  $("checkin-count").textContent = list.length || "";
  if (!list.length) {
    $("checkins").replaceChildren(el("p", { class: "empty" }, "Nothing to ask right now."));
    return;
  }
  const label = { learned_offset: "Your habit", anchor_switch: "Follow the sun", dst_glide: "Daylight Saving" };
  $("checkins").replaceChildren(
    ...list.map((c) =>
      el(
        "article",
        { class: "checkin" },
        el("div", { class: "kind" }, label[c.kind] || c.kind),
        el("p", {}, c.question),
        el("ul", {}, c.evidence.map((e) => el("li", {}, e))),
        el(
          "div",
          { class: "actions" },
          el("button", { class: "accept", type: "button", onclick: () => answer(c, "accept") }, "Yes"),
          c.kind === "dst_glide"
            ? ""
            : el("button", { type: "button", onclick: () => answer(c, "adjust") }, "Adjust…"),
          el("button", { type: "button", onclick: () => answer(c, "decline") }, "Not now"),
        ),
      ),
    ),
  );
}

function renderSchedule(data) {
  $("schedule-day").textContent = wallDay(data.day + "T00:00");
  if (!data.events.length) {
    $("schedule").replaceChildren(el("li", { class: "empty" }, "No routines today."));
    return;
  }
  $("schedule").replaceChildren(
    ...data.events.map((e) =>
      el(
        "li",
        {},
        el("span", { class: "time" }, wallTime(e.at)),
        el(
          "span",
          {},
          `${e.name} · ${e.action}`,
          el("div", { class: `why${e.factors.dst_glide_minutes ? " glide" : ""}` }, e.explanation.replace(/^.*?\(/, "(")),
        ),
      ),
    ),
  );
}

function renderDst(plan) {
  if (!plan.next_change) {
    $("dst").replaceChildren(el("p", { class: "empty" }, plan.note || "No change coming up."));
    return;
  }
  const steps = plan.glide_preview.map((s) =>
    el("div", { class: "glide-step" }, wallDay(s.day + "T00:00", { weekday: "short" }), el("b", {}, `${s.shift_minutes > 0 ? "+" : ""}${s.shift_minutes}m`)),
  );
  steps.push(el("div", { class: "glide-step change" }, wallDay(plan.next_change.at, { weekday: "short" }), el("b", {}, "new clock")));
  $("dst").replaceChildren(
    el("p", {}, plan.next_change.summary + "."),
    el("p", { class: "muted small" }, "Clock-based routines can ease in 15 minutes a day instead of jumping an hour:"),
    el("div", { class: "glide-steps" }, steps),
  );
}

export async function refresh() {
  const loc = await call("get_location");
  renderClock(loc);
  renderLights(await call("list_lights"));
  if (loc.status !== "set") {
    $("checkins").replaceChildren(el("p", { class: "empty" }, "Set your location to get started."));
    $("schedule").replaceChildren();
    $("dst").replaceChildren();
    return;
  }
  renderCheckins(await call("get_checkins"));
  renderSchedule(await call("get_schedule"));
  renderDst(await call("plan_dst_transition"));
}

// Run an action, then refresh the panels; show failures in the conversation.
export async function act(fn) {
  try {
    await fn();
  } catch (e) {
    say("system", e.message);
  }
  await refresh().catch((e) => say("system", e.message));
}

async function answer(checkin, response) {
  const args = { checkin_id: checkin.id, response };
  if (response === "adjust") {
    const hint = checkin.kind === "anchor_switch" ? "minutes relative to sunset/sunrise, e.g. -15" : "the new offset in minutes, e.g. -15";
    const value = prompt(`Adjust: ${hint}`);
    if (value === null || value.trim() === "" || Number.isNaN(Number(value))) return;
    args.minutes = Number(value);
  }
  await act(() => call("respond_to_checkin", args));
}

// -- demo clock --------------------------------------------------------------------------

function setupDemo() {
  const toggle = $("demo-toggle");
  const panel = $("demo-panel");
  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
  });
  const travel = (args) =>
    act(async () => {
      try {
        await call("set_demo_clock", args);
        $("demo-note").textContent = "";
        panel.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      } catch (e) {
        $("demo-note").textContent = e.message;
        throw e;
      }
    });
  $("demo-go").addEventListener("click", () => $("demo-at").value && travel({ at: $("demo-at").value }));
  panel.querySelectorAll(".presets button").forEach((b) =>
    b.addEventListener("click", () => travel(b.hasAttribute("data-reset") ? { reset: true } : { at: b.dataset.at })),
  );
}

// -- conversation: /agent decides the next step; this page makes every tool call over MCP --------

const conversation = [];
const MAX_STEPS = 6;
const AGENT_HIDDEN_TOOLS = new Set(["set_demo_clock"]); // demo control stays in the UI, not the agent

async function askAgent() {
  const tools = client.tools
    .filter((t) => !AGENT_HIDDEN_TOOLS.has(t.name))
    .map(({ name, description, inputSchema }) => ({ name, description, inputSchema }));
  const headers = { "Content-Type": "application/json" };
  if (client.token) headers.Authorization = `Bearer ${client.token}`;
  const response = await fetch("/agent", { method: "POST", headers, body: JSON.stringify({ messages: conversation, tools }) });
  const step = await response.json();
  if (!response.ok) throw new Error(step.error || `Agent error ${response.status}`);
  const chip = $("agent-mode");
  chip.textContent = step.mode === "bedrock" ? "Agent: Claude Haiku 4.5 on Amazon Bedrock" : "Agent: offline (mock)";
  chip.title = step.fallback_reason ? `Bedrock unavailable (${step.fallback_reason}); used the offline agent` : "Which agent chose the last step";
  return step;
}

async function onUserMessage(text) {
  conversation.push({ role: "user", text });
  $("say").disabled = true;
  let card = null; // the last tool call this turn that has an on-screen card
  try {
    for (let i = 0; i < MAX_STEPS; i++) {
      const step = await askAgent();
      if (step.type === "reply") {
        conversation.push({ role: "assistant", text: step.text, ...(step.context ? { context: step.context } : {}) });
        say("assistant", step.text);
        speak(step.text);
        if (card) await showCard(card);
        return;
      }
      conversation.push({ role: "assistant", text: step.text || "", tool_calls: step.calls });
      const results = [];
      for (const c of step.calls) {
        say("tool", `MCP tools/call ${c.name} ${JSON.stringify(c.arguments)}`);
        const r = await client.callTool(c.name, c.arguments);
        results.push({ id: c.id, name: c.name, ok: r.ok, data: r.data, text: r.text });
        if (r.ok && appHost?.cardFor(c.name)) card = { name: c.name, args: c.arguments, raw: r.raw };
      }
      conversation.push({ role: "tool", results });
    }
    say("system", "That took too many steps; please try rephrasing.");
  } catch (e) {
    say("system", e.message);
  } finally {
    $("say").disabled = false;
    $("say").focus();
    await refresh().catch((e) => say("system", e.message));
  }
}

// -- on-screen cards (MCP Apps): rendered after the reply that produced them ------------------

let appHost = null;

async function showCard({ name, args, raw }) {
  const box = $("messages");
  const holder = el("div", { class: "msg app-card" });
  box.append(holder);
  try {
    await appHost.render(holder, name, args, raw);
  } catch (e) {
    holder.remove();
    say("system", `Couldn't show the card: ${e.message}`);
  }
  box.scrollTop = box.scrollHeight;
}

function setupAppHost() {
  appHost = new AppHost(client, {
    onLog: logCard,
    onToolCalled: () => refresh().catch(() => {}),
    onContext: (text) => {
      if (!text) return;
      conversation.push({ role: "assistant", text: `(On Time card) ${text}` });
      say("system", text);
    },
    onUserMessage: (text) => {
      if (!text || $("say").disabled) return;
      $("say").value = text;
      $("composer").requestSubmit();
    },
  });
}

// -- voice: browser speech recognition in, speech synthesis out --------------------------------

let speakReplies = true;

function speak(text) {
  if (!speakReplies || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices().filter((v) => v.lang === "en-US");
  utterance.voice = voices.find((v) => /natural|neural|aria|jenny|samantha/i.test(v.name)) || voices[0] || null;
  utterance.rate = 1.03;
  window.speechSynthesis.speak(utterance);
}

function setupVoice() {
  const speaker = $("speaker");
  speaker.addEventListener("click", () => {
    speakReplies = !speakReplies;
    speaker.setAttribute("aria-pressed", String(speakReplies));
    speaker.title = speakReplies ? "Spoken replies on" : "Spoken replies off";
    if (!speakReplies) window.speechSynthesis?.cancel();
  });

  const mic = $("mic");
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    mic.disabled = true;
    mic.title = "Speech input isn't supported in this browser; type instead";
    return;
  }
  const recognizer = new Recognition();
  recognizer.lang = "en-US";
  recognizer.interimResults = true;
  recognizer.maxAlternatives = 1;
  let listening = false;

  recognizer.onresult = (event) => {
    const result = event.results[event.results.length - 1];
    $("say").value = result[0].transcript;
    if (result.isFinal) $("composer").requestSubmit();
  };
  recognizer.onend = () => {
    listening = false;
    mic.classList.remove("listening");
  };
  recognizer.onerror = (event) => {
    if (event.error !== "no-speech" && event.error !== "aborted") say("system", `Microphone: ${event.error}`);
  };
  mic.addEventListener("click", () => {
    if (listening) return recognizer.stop();
    window.speechSynthesis?.cancel();
    listening = true;
    mic.classList.add("listening");
    $("say").value = "";
    recognizer.start();
  });
}

function setupComposer() {
  $("suggestions").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      if ($("say").disabled) return;
      $("say").value = b.textContent;
      $("composer").requestSubmit();
    }),
  );
  $("composer").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("say").value.trim();
    if (!text) return;
    $("say").value = "";
    say("user", text);
    await onUserMessage(text);
  });
}

// -- start -------------------------------------------------------------------------------

async function connect() {
  client = new McpClient(new URL("/mcp", location.href).href, {
    token: sessionStorage.getItem(TOKEN_KEY) || "",
    onLog: logProtocol,
  });
  try {
    const init = await client.connect();
    setupAppHost();
    $("conn").textContent = `MCP ${init.protocolVersion} · ${client.tools.length} tools`;
    $("conn").className = "chip conn ok";
    say("system", `Connected to ${init.serverInfo.title || init.serverInfo.name} over Streamable HTTP (MCP ${init.protocolVersion}).`);
    say(
      "assistant",
      "Hi! I keep your lights in step with the sun, the seasons, and you. Ask when a light comes on, " +
        "turn one on or off, or ask about Daylight Saving Time. I'll check in before I change anything.",
    );
    await refresh();
  } catch (e) {
    if (e instanceof McpError && e.status === 401) {
      const token = prompt("This server needs its access token (ON_TIME_API_TOKEN):");
      if (token) {
        sessionStorage.setItem(TOKEN_KEY, token);
        return connect();
      }
    }
    $("conn").textContent = "Not connected";
    $("conn").className = "chip conn bad";
    say("system", `Couldn't connect: ${e.message}`);
  }
}

$("protocol-toggle").addEventListener("click", () => {
  const log = $("protocol");
  log.hidden = !log.hidden;
  $("protocol-toggle").setAttribute("aria-expanded", String(!log.hidden));
});
setupDemo();
setupComposer();
setupVoice();
connect();
