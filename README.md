# Microsoft Foundry Conversation for Home Assistant

<p align="center">
  <img src="https://raw.githubusercontent.com/KAkos06/ha-foundry-integration/main/custom_components/foundry_conversation/brand/icon@2x.png" width="160" alt="Microsoft Foundry integration logo">
</p>

This custom integration registers a Microsoft Foundry / Azure OpenAI Responses
API deployment as a native Home Assistant conversation agent. Home Assistant
communicates directly with Foundry; no proxy, Node-RED flow, MCP server, or
separate backend is used.

When **Allow Home Assistant control** is enabled, the integration exposes the
built-in Home Assistant Assist LLM API to the model. Only entities and scripts
that are exposed to Assist can be controlled.

## Requirements

- Home Assistant Core 2026.8.2 or newer
- A Microsoft Foundry or Azure OpenAI deployment supporting the Responses API
- An API key

Supported base URL examples:

```text
https://<resource>.openai.azure.com/openai/v1/
https://<resource>.services.ai.azure.com/openai/v1/
https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/
```

URLs ending in `/responses` are accepted and normalized automatically.

## Installation

### HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/KAkos06/ha-foundry-integration` with the
   **Integration** category.
3. Open **Microsoft Foundry**, select **Download**, then restart Home Assistant.
4. Open **Settings → Devices & services → Add integration** and select
   **Microsoft Foundry**.

### Manual installation

1. Copy `custom_components/foundry_conversation/` into:

   ```text
   /config/custom_components/foundry_conversation/
   ```

2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**.
4. Select **Microsoft Foundry**.
5. Enter the endpoint, API key, and model/deployment name. Home Assistant sends
   one small request to validate all three values.
6. Open **Settings → Voice assistants**, edit an assistant, and select
   **Microsoft Foundry** as its conversation agent.
7. To control Home Assistant, expose the desired entities to Assist, then open
   the integration options and enable **Allow Home Assistant control**.

The API key is stored in the Home Assistant config entry and is never included
in logs. Model, prompt, timeout, token limit, tool iteration limit, temperature,
reasoning effort, and Home Assistant control can be changed in integration
options.

## Home Assistant Conversation API

No custom API endpoint is required. Use the conversation entity ID shown by
Home Assistant as `agent_id` with the standard WebSocket API:

```json
{
  "id": 123,
  "type": "conversation/process",
  "text": "Kapcsold le a nappali lámpát",
  "language": "hu",
  "agent_id": "conversation.microsoft_foundry"
}
```

Reuse the returned `conversation_id` in later requests to retain the Home
Assistant ChatLog context.

## Security model

The integration does not enumerate entities and does not call Home Assistant
services directly. Tool definitions and execution come from Home Assistant's
LLM/Assist API. Therefore, disabling an entity's Assist exposure also prevents
this agent from controlling it.

## Troubleshooting

Enable debug logging without including credentials:

```yaml
logger:
  logs:
    custom_components.foundry_conversation: debug
```

Authentication failures start Home Assistant's reauthentication flow. Endpoint,
timeout, rate-limit, deployment, invalid-response, and tool-iteration errors are
reported separately in the UI and logs.
