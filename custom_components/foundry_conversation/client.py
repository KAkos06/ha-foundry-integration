"""Microsoft Foundry Responses API client helpers."""

import json
import re
from collections.abc import AsyncGenerator, Callable, Iterable
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

import openai
from azure.identity.aio import ClientSecretCredential
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps
from httpx import AsyncClient, HTTPStatusError
from openai._streaming import AsyncStream
from openai.types.responses import (
    EasyInputMessageParam,
    FunctionToolParam,
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallParam,
    ResponseIncompleteEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseReasoningItem,
    ResponseReasoningItemParam,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
    ToolParam,
)
from openai.types.responses.response_input_param import FunctionCallOutput
from voluptuous_openapi import convert

from .const import (
    AZURE_AI_SCOPE,
    FOUNDRY_PROJECT_SCOPE,
    CONF_MAX_OUTPUT_TOKENS,
    CONF_MAX_TOOL_ITERATIONS,
    CONF_MODEL,
    CONF_REASONING_EFFORT,
    CONF_TARGET,
    CONF_TEMPERATURE,
    CONF_TIMEOUT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_TOOL_ITERATIONS,
    DEFAULT_TIMEOUT,
    REASONING_DISABLED,
    TARGET_AGENT,
    TARGET_MODEL,
)


class InvalidEndpointError(ValueError):
    """Raised when a Foundry endpoint is invalid."""


class FoundryError(HomeAssistantError):
    """Base class for user-facing Foundry errors."""

    error_key = "unknown"
    english_message = "Microsoft Foundry could not process the request."
    hungarian_message = "A Microsoft Foundry nem tudta feldolgozni a kérést."

    def user_message(self, language: str | None) -> str:
        """Return a localized user-facing message."""
        if language and language.lower().startswith("hu"):
            return self.hungarian_message
        return self.english_message


class FoundryAuthenticationError(FoundryError):
    """Authentication failed."""

    error_key = "invalid_auth"
    english_message = "The Microsoft Foundry credentials are invalid."
    hungarian_message = "A Microsoft Foundry hitelesítés érvénytelen."


class FoundryPermissionError(FoundryError):
    """Credentials are valid but do not have enough permission."""

    error_key = "insufficient_permissions"
    english_message = "The Microsoft Foundry credentials do not have enough permissions."
    hungarian_message = "A Microsoft Foundry hitelesítés érvényes, de nincs elég jogosultsága."


class FoundryConnectionError(FoundryError):
    """The endpoint could not be reached."""

    error_key = "cannot_connect"
    english_message = "The Microsoft Foundry endpoint is not reachable."
    hungarian_message = "A Microsoft Foundry endpoint nem érhető el."


class FoundryTimeoutError(FoundryError):
    """The request timed out."""

    error_key = "timeout"
    english_message = "Microsoft Foundry did not respond in time."
    hungarian_message = "A Microsoft Foundry nem válaszolt időben."


class FoundryRateLimitError(FoundryError):
    """The request was rate limited."""

    error_key = "rate_limit"
    english_message = "Microsoft Foundry is rate limiting requests. Try again later."
    hungarian_message = (
        "A Microsoft Foundry korlátozza a kéréseket. Próbáld újra később."
    )


class FoundryDeploymentError(FoundryError):
    """The configured model deployment is invalid."""

    error_key = "invalid_deployment"
    english_message = "The configured Microsoft Foundry deployment is not available."
    hungarian_message = "A beállított Microsoft Foundry deployment nem érhető el."


class FoundryDiscoveryError(FoundryError):
    """Models or agents could not be listed."""

    error_key = "discovery_failed"
    english_message = "Microsoft Foundry could not list models and agents."
    hungarian_message = (
        "A Microsoft Foundry nem tudta listázni a modelleket és agenteket."
    )


class FoundryInvalidResponseError(FoundryError):
    """The API returned an unusable response."""

    error_key = "invalid_response"
    english_message = "Microsoft Foundry returned an invalid response."
    hungarian_message = "A Microsoft Foundry érvénytelen választ adott."


class FoundryToolLimitError(FoundryError):
    """The maximum number of tool iterations was reached."""

    error_key = "tool_limit"
    english_message = "The Home Assistant tool-call limit was reached."
    hungarian_message = "A Home Assistant eszközhívási korlátját elértük."


def normalize_endpoint(endpoint: str) -> str:
    """Validate and normalize an Azure/Foundry Responses base URL."""
    value = endpoint.strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as err:
        raise InvalidEndpointError("Endpoint is not a valid URL") from err
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidEndpointError("Endpoint must be a plain HTTPS URL")

    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")].rstrip("/")
    if "/api/projects/" in path and not path.endswith("/openai/v1"):
        project_tail = path.split("/api/projects/", 1)[1]
        if project_tail and "/" not in project_tail:
            path = f"{path}/openai/v1"
    if not path.endswith("/openai/v1"):
        raise InvalidEndpointError("Endpoint path must end with /openai/v1")

    return urlunsplit(("https", parsed.netloc, f"{path}/", "", ""))


def project_endpoint_from_openai(endpoint: str) -> str | None:
    """Return the project endpoint when the base URL targets a Foundry project."""
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if "/api/projects/" not in path or not path.endswith("/openai/v1"):
        return None
    path = path[: -len("/openai/v1")].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def make_target(target_type: str, name: str) -> str:
    """Create a stable stored target identifier."""
    return f"{target_type}:{name}"


def parse_target(target: str) -> tuple[str, str]:
    """Split a stored target into type and Foundry resource name."""
    target_type, separator, name = target.partition(":")
    if separator and target_type in (TARGET_MODEL, TARGET_AGENT) and name:
        return target_type, name
    return TARGET_MODEL, target


EXCLUDED_MODEL_PREFIXES = (
    "text-embedding",
    "text-search",
    "text-similarity",
    "text-davinci",
    "text-curie",
    "text-babbage",
    "text-ada",
    "dall-e",
    "tts",
    "whisper",
    "babbage",
    "davinci",
    "curie",
    "moderation",
    "omni-moderation",
    "realtime",
    "audio",
)
DATE_SNAPSHOT_PATTERN = re.compile(
    r"(-\d{4}-\d{2}-\d{2}$|-\d{8}$|-\d{4}$|-preview-\d{4}-\d{2}-\d{2}$)"
)


async def async_list_targets(
    client: openai.AsyncOpenAI,
    endpoint: str,
    http_client: AsyncClient,
    credential: ClientSecretCredential | None = None,
    api_key: str | None = None,
) -> list[tuple[str, str]]:
    """List model and agent targets available to the connection."""
    project_endpoint = project_endpoint_from_openai(endpoint)
    headers: dict[str, str] = {}
    if project_endpoint is not None:
        if credential is not None:
            for scope in (FOUNDRY_PROJECT_SCOPE, AZURE_AI_SCOPE):
                try:
                    token = await credential.get_token(scope)
                    headers = {"Authorization": f"Bearer {token.token}"}
                    if headers:
                        break
                except Exception:
                    pass
        elif api_key:
            headers = {"api-key": api_key}

    deployments: list[str] = []
    agents: list[str] = []

    if project_endpoint and headers:
        for api_version in (
            "2024-05-01-preview",
            "2024-10-01-preview",
            "v1",
            "2025-05-15-preview",
        ):
            try:
                dep_resp = await http_client.get(
                    f"{project_endpoint}/deployments",
                    params={"api-version": api_version, "limit": 100},
                    headers=headers,
                    timeout=5.0,
                )
                if dep_resp.status_code == 200:
                    dep_payload = dep_resp.json()
                    dep_items = (
                        dep_payload.get("data")
                        or dep_payload.get("value")
                        or (dep_payload if isinstance(dep_payload, list) else [])
                    )
                    found_deployments = []
                    for item in dep_items:
                        if isinstance(item, dict):
                            name = item.get("name") or item.get("id")
                            if isinstance(name, str) and name:
                                found_deployments.append(name)
                    if found_deployments:
                        deployments = sorted(set(found_deployments))
                        break
            except Exception:
                pass

        for path in ("agents", "assistants"):
            for api_version in (
                "v1",
                "2024-05-01-preview",
                "2024-10-01-preview",
                "2025-05-15-preview",
            ):
                try:
                    agent_resp = await http_client.get(
                        f"{project_endpoint}/{path}",
                        params={"api-version": api_version, "limit": 100},
                        headers=headers,
                        timeout=5.0,
                    )
                    if agent_resp.status_code == 200:
                        agent_payload = agent_resp.json()
                        agent_items = (
                            agent_payload.get("data")
                            or agent_payload.get("value")
                            or (agent_payload if isinstance(agent_payload, list) else [])
                        )
                        found_agents = []
                        for item in agent_items:
                            if isinstance(item, dict):
                                name = item.get("name") or item.get("id")
                                if isinstance(name, str) and name:
                                    found_agents.append(name)
                        if found_agents:
                            agents = sorted(set(found_agents))
                            break
                except Exception:
                    pass
            if agents:
                break

    targets: list[tuple[str, str]] = []
    if deployments:
        filtered_deployments = [
            dep
            for dep in deployments
            if not any(
                dep.lower().startswith(prefix) for prefix in EXCLUDED_MODEL_PREFIXES
            )
        ]
        targets.extend(
            (TARGET_MODEL, dep) for dep in (filtered_deployments or deployments)
        )
    else:
        models: list[str] = []
        try:
            models = sorted({model.id async for model in await client.models.list()})
        except Exception:
            # Model list on project endpoints is not available or auth restricted
            pass

        if models:
            filtered_models = [
                model
                for model in models
                if not any(
                    model.lower().startswith(prefix)
                    for prefix in EXCLUDED_MODEL_PREFIXES
                )
                and not DATE_SNAPSHOT_PATTERN.search(model)
            ]
            models_to_use = filtered_models if filtered_models else models
            targets.extend((TARGET_MODEL, model) for model in models_to_use)

    if agents:
        targets.extend((TARGET_AGENT, agent) for agent in agents)

    return targets


async def async_validate_connection(
    client: openai.AsyncOpenAI,
    target: str,
    *,
    timeout: float = 10.0,
) -> str:
    """Validate credentials, endpoint, and target with a minimal request.

    Returns the validated canonical target string (e.g. 'agent:name' or 'model:name').
    """
    target_type, target_name = parse_target(target)
    if target_type == TARGET_AGENT:
        request: dict[str, Any] = {
            "input": "Reply with OK.",
            "max_output_tokens": 128,
            "store": False,
            "timeout": timeout,
            "extra_body": {
                "agent_reference": {
                    "type": "agent_reference",
                    "name": target_name,
                }
            },
        }
        try:
            response = await client.responses.create(**request)
        except openai.OpenAIError as err:
            raise _translate_openai_error(err) from err

        if response.status not in (None, "completed"):
            raise FoundryInvalidResponseError(
                f"Validation response ended with status {response.status}"
            )
        return make_target(TARGET_AGENT, target_name)

    request = {
        "input": "Reply with OK.",
        "max_output_tokens": 128,
        "store": False,
        "timeout": timeout,
        "model": target_name,
    }
    try:
        response = await client.responses.create(**request)
    except (openai.NotFoundError, openai.BadRequestError) as err:
        if not target.startswith(f"{TARGET_MODEL}:"):
            try:
                agent_request: dict[str, Any] = {
                    "input": "Reply with OK.",
                    "max_output_tokens": 128,
                    "store": False,
                    "timeout": timeout,
                    "extra_body": {
                        "agent_reference": {
                            "type": "agent_reference",
                            "name": target_name,
                        }
                    },
                }
                agent_resp = await client.responses.create(**agent_request)
                if agent_resp.status in (None, "completed"):
                    return make_target(TARGET_AGENT, target_name)
            except Exception:
                pass
        raise _translate_openai_error(err) from err
    except openai.OpenAIError as err:
        raise _translate_openai_error(err) from err

    if response.status not in (None, "completed"):
        raise FoundryInvalidResponseError(
            f"Validation response ended with status {response.status}"
        )
    return make_target(TARGET_MODEL, target_name)


def _translate_openai_error(err: openai.OpenAIError) -> FoundryError:
    """Translate an OpenAI SDK error into a stable integration error."""
    if isinstance(err, openai.AuthenticationError):
        return FoundryAuthenticationError()
    if isinstance(err, openai.PermissionDeniedError):
        return FoundryPermissionError()
    if isinstance(err, openai.APITimeoutError):
        return FoundryTimeoutError()
    if isinstance(err, openai.RateLimitError):
        return FoundryRateLimitError()
    if isinstance(err, openai.APIConnectionError):
        return FoundryConnectionError()
    if isinstance(err, openai.NotFoundError):
        return FoundryDeploymentError()
    if isinstance(err, openai.BadRequestError):
        message = (err.message or "").lower()
        if "deployment" in message or "model" in message:
            return FoundryDeploymentError()
    return FoundryInvalidResponseError()


def _format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> FunctionToolParam:
    """Convert a Home Assistant LLM tool to a Responses function tool."""
    unsupported_keys = {"oneOf", "anyOf", "allOf", "enum", "not"}
    schema = convert(tool.parameters, custom_serializer=custom_serializer)
    schema = {
        key: value for key, value in schema.items() if key not in unsupported_keys
    }
    if schema.get("type") != "object":
        schema = {
            "type": "object",
            "properties": schema.get("properties", {}),
        }
    schema.setdefault("properties", {})
    return FunctionToolParam(
        type="function",
        name=tool.name,
        description=tool.description,
        parameters=schema,
        strict=False,
    )


def _convert_content_to_input(
    chat_content: Iterable[conversation.Content],
) -> list[Any]:
    """Convert Home Assistant ChatLog content into Responses input items."""
    messages: list[Any] = []
    for content in chat_content:
        if isinstance(content, conversation.ToolResultContent):
            messages.append(
                FunctionCallOutput(
                    type="function_call_output",
                    call_id=content.tool_call_id,
                    output=json_dumps(content.tool_result),
                )
            )
            continue

        if content.content:
            role: Literal["user", "assistant", "system", "developer"] = content.role
            if role == "system":
                role = "developer"
            messages.append(
                EasyInputMessageParam(
                    type="message",
                    role=role,
                    content=content.content,
                )
            )

        if not isinstance(content, conversation.AssistantContent):
            continue
        if content.tool_calls:
            for tool_call in content.tool_calls:
                messages.append(
                    ResponseFunctionToolCallParam(
                        type="function_call",
                        name=tool_call.tool_name,
                        arguments=json_dumps(tool_call.tool_args),
                        call_id=tool_call.id,
                    )
                )
        if isinstance(content.native, ResponseReasoningItem):
            messages.append(
                ResponseReasoningItemParam(
                    type="reasoning",
                    id=content.native.id,
                    summary=(
                        [{"type": "summary_text", "text": content.thinking_content}]
                        if content.thinking_content
                        else []
                    ),
                    encrypted_content=content.native.encrypted_content,
                )
            )
    return messages


async def _transform_stream(
    stream: AsyncStream[ResponseStreamEvent],
) -> AsyncGenerator[
    conversation.AssistantContentDeltaDict | conversation.ToolResultContentDeltaDict
]:
    """Transform a Responses API stream into Home Assistant ChatLog deltas."""
    tool_calls: dict[int, ResponseFunctionToolCall] = {}
    saw_output = False
    completed = False

    async for event in stream:
        if isinstance(event, ResponseOutputItemAddedEvent):
            if isinstance(event.item, ResponseFunctionToolCall):
                tool_calls[event.output_index] = event.item
                yield {"role": "assistant"}
                saw_output = True
            elif isinstance(event.item, (ResponseOutputMessage, ResponseReasoningItem)):
                yield {"role": "assistant"}
        elif isinstance(event, ResponseFunctionCallArgumentsDeltaEvent):
            if tool_call := tool_calls.get(event.output_index):
                tool_call.arguments += event.delta
        elif isinstance(event, ResponseFunctionCallArgumentsDoneEvent):
            tool_call = tool_calls.pop(event.output_index, None)
            if tool_call is None:
                raise FoundryInvalidResponseError(
                    "Tool arguments arrived without a call"
                )
            try:
                arguments = json.loads(event.arguments)
            except (json.JSONDecodeError, TypeError) as err:
                raise FoundryInvalidResponseError(
                    "Tool arguments are not valid JSON"
                ) from err
            if not isinstance(arguments, dict):
                raise FoundryInvalidResponseError("Tool arguments must be an object")
            yield {
                "tool_calls": [
                    llm.ToolInput(
                        id=tool_call.call_id,
                        tool_name=tool_call.name,
                        tool_args=arguments,
                    )
                ]
            }
            saw_output = True
        elif isinstance(event, ResponseTextDeltaEvent):
            if event.delta:
                yield {"content": event.delta}
                saw_output = True
        elif isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
            if event.delta:
                yield {"thinking_content": event.delta}
                saw_output = True
        elif isinstance(event, ResponseOutputItemDoneEvent) and isinstance(
            event.item, ResponseReasoningItem
        ):
            yield {
                "native": ResponseReasoningItem(
                    type="reasoning",
                    id=event.item.id,
                    summary=[],
                    encrypted_content=event.item.encrypted_content,
                )
            }
        elif isinstance(event, ResponseCompletedEvent):
            completed = True
            if tool_calls:
                raise FoundryInvalidResponseError(
                    "The response ended before tool arguments were completed"
                )
            if not saw_output:
                raise FoundryInvalidResponseError("The response contained no output")
        elif isinstance(event, ResponseIncompleteEvent):
            reason = (
                event.response.incomplete_details.reason
                if event.response.incomplete_details
                else "unknown"
            )
            raise FoundryInvalidResponseError(f"Response incomplete: {reason}")
        elif isinstance(event, ResponseFailedEvent):
            reason = (
                event.response.error.message
                if event.response.error is not None
                else "unknown"
            )
            raise FoundryInvalidResponseError(f"Response failed: {reason}")
        elif isinstance(event, ResponseErrorEvent):
            raise FoundryInvalidResponseError(f"Response error: {event.message}")

    if not completed:
        raise FoundryInvalidResponseError("The response stream ended unexpectedly")


class FoundryClient:
    """Async Microsoft Foundry Responses client."""

    def __init__(self, client: openai.AsyncOpenAI) -> None:
        """Initialize the client wrapper."""
        self._client = client

    async def async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        entity_id: str,
        options: dict[str, Any],
    ) -> None:
        """Stream a response and execute HA tools through the ChatLog."""
        messages = _convert_content_to_input(chat_log.content)
        target_type, target_name = parse_target(
            options.get(CONF_TARGET, options.get(CONF_MODEL, ""))
        )
        model_args: dict[str, Any] = {
            "input": messages,
            "max_output_tokens": options.get(
                CONF_MAX_OUTPUT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
            ),
            "store": False,
            "stream": True,
            "user": chat_log.conversation_id,
        }
        if target_type == TARGET_AGENT:
            model_args["extra_body"] = {
                "agent_reference": {
                    "type": "agent_reference",
                    "name": target_name,
                }
            }
        else:
            model_args["model"] = target_name
        reasoning_effort = options.get(CONF_REASONING_EFFORT, REASONING_DISABLED)
        if target_type == TARGET_MODEL and reasoning_effort != REASONING_DISABLED:
            model_args["reasoning"] = {"effort": reasoning_effort}
            model_args["include"] = ["reasoning.encrypted_content"]
        if (
            target_type == TARGET_MODEL
            and CONF_TEMPERATURE in options
            and reasoning_effort
            in (
                REASONING_DISABLED,
                "none",
            )
        ):
            model_args[CONF_TEMPERATURE] = options[CONF_TEMPERATURE]

        tools: list[ToolParam] = []
        if target_type == TARGET_MODEL and chat_log.llm_api:
            tools = [
                cast(
                    ToolParam,
                    _format_tool(tool, chat_log.llm_api.custom_serializer),
                )
                for tool in chat_log.llm_api.tools
            ]
        if tools:
            model_args["tools"] = tools

        max_iterations = options.get(
            CONF_MAX_TOOL_ITERATIONS, DEFAULT_MAX_TOOL_ITERATIONS
        )
        timeout = options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        for _iteration in range(max_iterations):
            try:
                stream = await self._client.responses.create(
                    **model_args,
                    timeout=float(timeout),
                )
                content_stream = chat_log.async_add_delta_content_stream(
                    entity_id,
                    _transform_stream(stream),
                )
                new_content = [content async for content in content_stream]
            except FoundryError:
                raise
            except openai.OpenAIError as err:
                raise _translate_openai_error(err) from err

            if not new_content:
                raise FoundryInvalidResponseError("The response contained no content")
            messages.extend(_convert_content_to_input(new_content))
            model_args["input"] = messages
            if not chat_log.unresponded_tool_results:
                return

        raise FoundryToolLimitError()
