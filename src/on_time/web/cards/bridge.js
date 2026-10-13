// Minimal MCP Apps view runtime (spec 2026-01-26): JSON-RPC 2.0 over postMessage with the host.
// Inlined into each ui:// card by the server, so the cards load nothing from the network.
const OnTimeCard = (() => {
  const PROTOCOL_VERSION = "2026-01-26";
  let nextId = 1;
  const pending = new Map();
  const handlers = {};

  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.id != null && !msg.method) {
      const waiter = pending.get(msg.id);
      if (!waiter) return;
      pending.delete(msg.id);
      if (msg.error) waiter.reject(new Error(msg.error.message || "Host error"));
      else waiter.resolve(msg.result);
    } else if (msg.method && msg.id == null) {
      handlers[msg.method]?.(msg.params || {});
    } else if (msg.method) {
      // Host requests to the view (ui/resource-teardown, ping): nothing to clean up, just acknowledge.
      window.parent.postMessage({ jsonrpc: "2.0", id: msg.id, result: {} }, "*");
    }
  });

  function request(method, params) {
    const id = nextId++;
    window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }

  function notify(method, params = {}) {
    window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }

  function on(method, fn) {
    handlers[method] = fn;
  }

  function applyHostContext(ctx = {}) {
    const root = document.documentElement;
    for (const [name, value] of Object.entries(ctx.styles?.variables || {})) {
      if (value) root.style.setProperty(name, value);
    }
    if (ctx.theme) root.style.colorScheme = ctx.theme;
  }

  async function start(name) {
    on("ui/notifications/host-context-changed", applyHostContext);
    const init = await request("ui/initialize", {
      protocolVersion: PROTOCOL_VERSION,
      appInfo: { name, version: "1.0.0" },
      appCapabilities: { availableDisplayModes: ["inline"] },
    });
    applyHostContext(init.hostContext);
    notify("ui/notifications/initialized");
    // Report the content's natural size (the card element, not the viewport) whenever it changes.
    // Hosts may not have laid the frame out yet, so zero sizes are skipped and resizes re-report.
    let last = "";
    const content = document.querySelector(".card") || document.getElementById("root") || document.body;
    const report = () => {
      const box = content.getBoundingClientRect();
      const width = Math.ceil(box.width);
      const height = Math.ceil(box.bottom + 2);
      if (!width || !height || `${width}x${height}` === last) return;
      last = `${width}x${height}`;
      notify("ui/notifications/size-changed", { width, height });
    };
    const observer = new ResizeObserver(report);
    observer.observe(document.documentElement);
    observer.observe(content);
    window.addEventListener("resize", report);
    requestAnimationFrame(report);
    OnTimeCard.reportSize = report;
    return init;
  }

  // Calls an On Time tool through the host; returns the structured result.
  async function callTool(name, args = {}) {
    const result = await request("tools/call", { name, arguments: args });
    if (result?.isError) throw new Error(result.content?.map((c) => c.text).join(" ") || `${name} failed`);
    return result?.structuredContent ?? {};
  }

  // Tells the host's assistant what happened in the card (used in its next turn).
  function updateModelContext(text) {
    return request("ui/update-model-context", { content: [{ type: "text", text }] }).catch(() => {});
  }

  function wallTime(iso) {
    const [h, m] = iso.slice(11, 16).split(":").map(Number);
    return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
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
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  return { start, on, callTool, updateModelContext, wallTime, wallDay, el, reportSize: () => {} };
})();
