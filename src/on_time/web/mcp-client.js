// A minimal, real MCP client for the browser: JSON-RPC 2.0 over Streamable HTTP, spec 2025-11-25.
// initialize -> notifications/initialized -> tools/list -> tools/call. Every message is reported
// to `onLog` so the page can show the protocol live.

export const PROTOCOL_VERSION = "2025-11-25";

export class McpError extends Error {
  constructor(message, { code, status } = {}) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export class McpClient {
  constructor(url, { token = "", onLog = () => {} } = {}) {
    this.url = url;
    this.token = token;
    this.onLog = onLog;
    this.nextId = 1;
    this.protocolVersion = null;
    this.serverInfo = null;
    this.tools = [];
  }

  headers() {
    const h = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
    if (this.protocolVersion) h["MCP-Protocol-Version"] = this.protocolVersion;
    if (this.token) h.Authorization = `Bearer ${this.token}`;
    return h;
  }

  async post(message) {
    const started = performance.now();
    const response = await fetch(this.url, { method: "POST", headers: this.headers(), body: JSON.stringify(message) });
    const ms = Math.round(performance.now() - started);
    let reply = null;
    if (response.status !== 202) {
      const type = response.headers.get("content-type") || "";
      const text = await response.text();
      if (type.startsWith("text/event-stream")) {
        // Streamable HTTP may answer with SSE; the JSON-RPC response is in a `data:` line.
        const data = text.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).filter(Boolean);
        reply = data.length ? JSON.parse(data[data.length - 1]) : null;
      } else if (text) {
        try {
          reply = JSON.parse(text);
        } catch {
          reply = { error: { message: text } };
        }
      }
    }
    this.onLog({ message, reply, status: response.status, ms });
    if (!response.ok) {
      const detail = reply?.error?.message || reply?.error_description || `HTTP ${response.status}`;
      throw new McpError(detail, { status: response.status, code: reply?.error?.code });
    }
    return reply;
  }

  async request(method, params) {
    const message = { jsonrpc: "2.0", id: this.nextId++, method, ...(params ? { params } : {}) };
    const reply = await this.post(message);
    if (reply?.error) throw new McpError(reply.error.message, { code: reply.error.code });
    return reply?.result;
  }

  async notify(method, params) {
    await this.post({ jsonrpc: "2.0", method, ...(params ? { params } : {}) });
  }

  async connect() {
    const result = await this.request("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      // This page is an MCP Apps host: it can render ui:// cards (spec 2026-01-26).
      capabilities: { extensions: { "io.modelcontextprotocol/ui": { mimeTypes: ["text/html;profile=mcp-app"] } } },
      clientInfo: { name: "simulated-on-time-for-alexa", title: "Simulated On Time for Alexa+", version: "0.3.0" },
    });
    this.protocolVersion = result.protocolVersion;
    this.serverInfo = result.serverInfo;
    await this.notify("notifications/initialized");
    this.tools = (await this.request("tools/list")).tools;
    return result;
  }

  // Returns { ok, data, text, raw }: data is the structured result, text the human-readable content,
  // raw the full CallToolResult (what MCP Apps cards receive).
  async callTool(name, args = {}) {
    const result = await this.request("tools/call", { name, arguments: args });
    const text = (result.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
    let data = result.structuredContent ?? null;
    if (data === null && text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
    return { ok: !result.isError, data, text, raw: result };
  }
}
