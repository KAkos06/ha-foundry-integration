# Microsoft Foundry Conversation for Home Assistant

<p align="center">
  <img src="https://raw.githubusercontent.com/KAkos06/ha-foundry-integration/main/custom_components/foundry_conversation/brand/icon@2x.png" width="160" alt="Microsoft Foundry integration logo">
</p>

This custom integration registers a Microsoft Foundry model deployment or
Foundry agent as a native Home Assistant conversation agent. Home Assistant
communicates directly with Foundry; no proxy, Node-RED flow, MCP server, or
separate backend is used.

When **Allow Home Assistant control** is enabled, the integration exposes the
built-in Home Assistant Assist LLM API to the model. Only entities and scripts
that are exposed to Assist can be controlled.

## Requirements

- Home Assistant Core 2026.8.2 or newer
- A Microsoft Foundry or Azure OpenAI deployment supporting the Responses API
- An API key for model targets, or a Microsoft Entra ID service principal for
  model and agent targets

Supported base URL examples:

```text
https://<resource>.openai.azure.com/openai/v1/
https://<resource>.services.ai.azure.com/openai/v1/
https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/
```

Bare Foundry project endpoints and URLs ending in `/responses` are accepted and
normalized automatically.

## Models and agents

After authentication, the integration loads available targets into a dropdown.
Every option is labeled with `model` or `agent`, for example:

```text
gpt-5.4 — model
ai-home-assistant-agent — agent
```

API-key authentication can discover and use models. Agent discovery and
invocation require a Microsoft Entra ID service principal with access to the
Foundry project. Configure its tenant ID, client ID, and client secret in the
integration. The service principal needs an appropriate project role, such as
**Foundry User**.

Foundry agents use the instructions and tools stored in their Foundry agent
definition, including web search and MCP/toolbox tools. When **Allow Home
Assistant control** is enabled, the integration also supplies Home Assistant's
Assist function tools for that response. Model targets receive them through the
Responses API `tools` parameter. Agent targets receive them through an
`additional_tools` developer input item because Foundry rejects the top-level
`tools` parameter when an `agent_reference` is present.

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
5. Enter the endpoint, choose API-key or Entra ID authentication, then select a
   model or agent from the discovered dropdown. Home Assistant sends one small
   request to validate the selected target.
6. Open **Settings → Voice assistants**, edit an assistant, and select
   **Microsoft Foundry** as its conversation agent.
7. To control Home Assistant, expose the desired entities to Assist, then open
   the integration options and enable **Allow Home Assistant control**.

Credentials are stored in the Home Assistant config entry and are never
included in logs. Target, prompt, timeout, token limit, tool iteration limit,
temperature, reasoning effort, and Home Assistant control can be changed in
integration options. Existing model-only installations are migrated
automatically.

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

## Versioning

Published versions use semantic `vX.Y.Z` GitHub releases. HACS installs the
latest release and displays its tag instead of a commit hash. The release tag
must match the `version` value in the integration manifest.
