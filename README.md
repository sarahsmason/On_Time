# On_Time

Agentic Alexa+ skill, using Python MCP server on AWS, that adapts your lights to your lifestyle including learning daily habits, following seasonal sunrise/sunset shifts, and easing through Daylight Saving Time.

> Your home's schedule, kept in step with your life, the seasons, and the clock.

On Time is a self-hosted **MCP server** (spec 2025-11-25, Streamable HTTP) for Alexa+ that:
- **Follows the sun** by anchoring light routines to sunrise and sunset at your location (from your device or what you tell it)
- **Glides through Daylight Saving Time** by shifting light routines a few minutes a day before the change
- **Learns from your adjustments**, then checks in conversationally before changing a routine
- **Explains every time** factor by factor, and never changes a routine without a yes

See [docs/scheduling-model.md](docs/scheduling-model.md).

Built for the *Build, Ship, Shape: Amazon Developer Hackathon*, Alexa+ track.

## Status
🚧 In progress. See [docs/architecture.md](docs/architecture.md).

## Requirements
- Python 3.12+
- Node.js LTS (only for MCP Inspector)
- An AWS account with Amazon Bedrock access (for the agent and deployment)

## Quick start
Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run on-time          # MCP server at http://127.0.0.1:8000/mcp (Streamable HTTP)
uv run pytest           # spec 2025-11-25 transport compliance tests
```

Inspect it with [MCP Inspector](https://github.com/modelcontextprotocol/inspector) v2:
run `npx @modelcontextprotocol/inspector` (in Windows PowerShell use `npx.cmd`), then
**Add Servers → + Add manually** with transport `streamable-http` and URL
`http://127.0.0.1:8000/mcp`, toggle it on, and open **Tools → ping_time → Execute Tool**.

| Env var | Default | Purpose |
|---|---|---|
| `ON_TIME_HOST` | `127.0.0.1` | Bind address (localhost only by default) |
| `ON_TIME_PORT` | `8000` | Port |
| `ON_TIME_ALLOWED_ORIGINS` | _(none)_ | Extra comma-separated browser origins allowed past Origin validation |

> **Windows + OneDrive:** keep the virtual environment out of the synced folder with
> `$env:UV_PROJECT_ENVIRONMENT = "$env:USERPROFILE\.venvs\on-time"` before running `uv`.

## Project docs
- [Build plan](docs/plan.md)
- [Architecture](docs/architecture.md)
- [Friction log](docs/friction-log.md)
- [Product feedback](docs/product-feedback.md)

## License
MIT. See [LICENSE](LICENSE).
