// MCP Apps host (spec 2026-01-26) for Simulated On Time for Alexa+.
// Renders a tool's ui:// card in the conversation through a sandbox proxy on a different origin,
// and answers the card's JSON-RPC messages: ui/initialize, tools/call and resources/read (proxied
// to the On Time MCP server), size changes, model-context updates, links, and chat messages.

export const APP_MIME_TYPE = "text/html;profile=mcp-app";
const PROTOCOL_VERSION = "2026-01-26";
const MAX_HEIGHT = 640;

// Our palette under the spec's standard variable names, so cards match the page.
const THEME = {
  "--color-background-primary": "#161f36",
  "--color-background-secondary": "#1d2844",
  "--color-text-primary": "#e8edf7",
  "--color-text-secondary": "#9aa6c2",
  "--color-border-primary": "#2a3657",
  "--color-text-warning": "#ffb547",
  "--color-background-warning": "#ffb547",
  "--color-text-success": "#4fd1a5",
  "--color-text-danger": "#ff7a85",
  "--font-sans": 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  "--border-radius-md": "10px",
  "--border-radius-lg": "14px",
};

// The sandbox must be a different origin from this page: swap 127.0.0.1 and localhost.
function sandboxOrigin() {
  const swap = location.hostname === "127.0.0.1" ? "localhost" : "127.0.0.1";
  return `${location.protocol}//${swap}${location.port ? ":" + location.port : ""}`;
}

export class AppHost {
  constructor(client, { onLog = () => {}, onUserMessage = async () => {}, onContext = () => {}, onToolCalled = () => {} } = {}) {
    this.client = client;
    this.onLog = onLog;
    this.onUserMessage = onUserMessage;
    this.onContext = onContext;
    this.onToolCalled = onToolCalled;
    this.resources = new Map();
  }

  cardFor(toolName) {
    const tool = this.client.tools.find((t) => t.name === toolName);
    const uri = tool?._meta?.ui?.resourceUri;
    return uri ? { tool, uri } : null;
  }

  async resource(uri) {
    if (!this.resources.has(uri)) {
      const result = await this.client.request("resources/read", { uri });
      const content = result.contents?.find((c) => c.uri === uri) || result.contents?.[0];
      if (!content || content.mimeType !== APP_MIME_TYPE) throw new Error(`${uri} is not an MCP Apps resource`);
      this.resources.set(uri, content);
    }
    return this.resources.get(uri);
  }

  // Renders the card for `toolName` into `container`, given the call's arguments and raw result.
  async render(container, toolName, args, result) {
    const card = this.cardFor(toolName);
    if (!card) return false;
    const content = await this.resource(card.uri);
    const origin = sandboxOrigin();
    const frame = document.createElement("iframe");
    frame.className = "app-frame";
    frame.title = content._meta?.ui?.title || "On Time card";
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
    frame.src = `${origin}/web/sandbox.html?host=${encodeURIComponent(location.origin)}`;
    container.append(frame);

    const post = (msg) => {
      this.onLog("host → card", msg);
      frame.contentWindow?.postMessage(msg, origin);
    };
    const reply = (id, result) => post({ jsonrpc: "2.0", id, result });
    const fail = (id, code, message) => post({ jsonrpc: "2.0", id, error: { code, message } });

    const listener = async (event) => {
      if (event.source !== frame.contentWindow || event.origin !== origin) return;
      const msg = event.data;
      if (!msg || msg.jsonrpc !== "2.0") return;
      if (msg.method) this.onLog("card → host", msg);
      const { id, method, params = {} } = msg;
      try {
        switch (method) {
          case "ui/notifications/sandbox-proxy-ready":
            return post({
              jsonrpc: "2.0",
              method: "ui/notifications/sandbox-resource-ready",
              params: { html: content.text, csp: content._meta?.ui?.csp, permissions: content._meta?.ui?.permissions },
            });
          case "ui/initialize":
            return reply(id, {
              protocolVersion: PROTOCOL_VERSION,
              hostInfo: { name: "simulated-on-time-for-alexa", version: "0.4.0" },
              hostCapabilities: { serverTools: {}, serverResources: {}, logging: {}, openLinks: {} },
              hostContext: {
                toolInfo: { tool: card.tool },
                theme: "dark",
                styles: { variables: THEME },
                displayMode: "inline",
                availableDisplayModes: ["inline"],
                containerDimensions: { maxHeight: MAX_HEIGHT },
                locale: navigator.language || "en-US",
                timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                platform: "web",
              },
            });
          case "ui/notifications/initialized":
            post({ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: args || {} } });
            return post({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result });
          case "ui/notifications/size-changed":
            if (params.height) frame.style.height = `${Math.min(params.height, MAX_HEIGHT)}px`;
            return;
          case "tools/call": {
            const target = this.client.tools.find((t) => t.name === params.name);
            const visibility = target?._meta?.ui?.visibility || ["model", "app"];
            if (!target || !visibility.includes("app")) return fail(id, -32602, `Tool ${params.name} isn't available to cards`);
            const toolResult = await this.client.request("tools/call", params);
            this.onToolCalled(params.name);
            return reply(id, toolResult);
          }
          case "resources/read":
            return reply(id, await this.client.request("resources/read", params));
          case "ui/update-model-context":
            this.onContext((params.content || []).map((c) => c.text).filter(Boolean).join(" "));
            return reply(id, {});
          case "ui/message":
            reply(id, {});
            return this.onUserMessage(params.content?.text || "");
          case "ui/open-link":
            window.open(params.url, "_blank", "noopener");
            return reply(id, {});
          case "ping":
            return reply(id, {});
          case "notifications/message":
            return console.info("[card]", params);
          default:
            if (id != null && method) return fail(id, -32601, `Unsupported: ${method}`);
        }
      } catch (e) {
        if (id != null) fail(id, -32000, e.message);
      }
    };
    window.addEventListener("message", listener);
    return true;
  }
}
