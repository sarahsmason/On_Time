# On_Time

Agentic Alexa+ skill, using Python MCP server on AWS, that adapts your lights to your lifestyle including learning daily habits, following seasonal sunrise/sunset shifts, and easing through Daylight Saving Time.

> Your home's schedule, kept in step with your life, the seasons, and the clock.

On Time is a self-hosted **MCP server** (spec 2025-11-25, Streamable HTTP) for Alexa+ that:
- **Learns habits** from your ad-hoc requests and proposes routines, explaining why
- **Tracks seasonal drift** and re-anchors schedules to sunrise and sunset
- **Glides through Daylight Saving Time** by shifting alarms, lights, and thermostat 10–15 minutes a day
- **Asks before changing anything**

Built for the *Build, Ship, Shape: Amazon Developer Hackathon*, Alexa+ track.

## Status
🚧 In progress. See [docs/architecture.md](docs/architecture.md).

## Requirements
- Python 3.12+
- Node.js LTS (only for MCP Inspector)
- An AWS account with Amazon Bedrock access (for the agent and deployment)

## Quick start
_Coming in v0.1._

## Project docs
- [Architecture](docs/architecture.md)
- [Friction log](docs/friction-log.md)
- [Product feedback](docs/product-feedback.md)

## License
MIT. See [LICENSE](LICENSE).
