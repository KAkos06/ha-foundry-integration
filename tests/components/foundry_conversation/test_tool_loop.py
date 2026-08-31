"""Tests for the Microsoft Foundry tool-calling loop."""

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.helpers import llm

from custom_components.foundry_conversation.client import (
    FoundryClient,
    FoundryToolLimitError,
)
from custom_components.foundry_conversation.const import (
    CONF_MAX_TOOL_ITERATIONS,
    CONF_MODEL,
    CONF_REASONING_EFFORT,
    CONF_TARGET,
    CONF_TEMPERATURE,
)


class FakeChatLog:
    """Minimal ChatLog double that simulates HA tool execution."""

    llm_api = None
    conversation_id = "conversation-1"

    def __init__(self, *, always_call_tool: bool = False) -> None:
        """Initialize the fake log."""
        self.content: list[conversation.Content] = [
            conversation.SystemContent(content="System prompt"),
            conversation.UserContent(content="Turn on the lamp"),
        ]
        self._round = 0
        self._always_call_tool = always_call_tool

    @property
    def unresponded_tool_results(self) -> bool:
        """Return whether another model request is needed."""
        return self.content[-1].role == "tool_result"

    async def async_add_delta_content_stream(
        self,
        agent_id: str,
        _stream: Any,
    ) -> AsyncGenerator[conversation.Content]:
        """Simulate ChatLog consuming a stream and executing a tool."""
        self._round += 1
        if self._round == 1 or self._always_call_tool:
            call_id = f"call-{self._round}"
            items: list[conversation.Content] = [
                conversation.AssistantContent(
                    agent_id=agent_id,
                    tool_calls=[
                        llm.ToolInput(
                            id=call_id,
                            tool_name="HassTurnOn",
                            tool_args={"name": "Lamp"},
                        )
                    ],
                ),
                conversation.ToolResultContent(
                    agent_id=agent_id,
                    tool_call_id=call_id,
                    tool_name="HassTurnOn",
                    tool_result={"success": True},
                ),
            ]
        else:
            items = [
                conversation.AssistantContent(
                    agent_id=agent_id,
                    content="The lamp is on.",
                )
            ]

        for item in items:
            self.content.append(item)
            yield item


def _make_client() -> tuple[FoundryClient, AsyncMock]:
    """Create a Foundry client with a mocked Responses resource."""
    create = AsyncMock(return_value=object())
    sdk_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return FoundryClient(cast(Any, sdk_client)), create


async def test_tool_result_is_sent_back_to_model() -> None:
    """A HA tool result causes a second Responses request."""
    client, create = _make_client()
    chat_log = FakeChatLog()

    await client.async_handle_chat_log(
        cast(conversation.ChatLog, chat_log),
        "conversation.microsoft_foundry",
        {CONF_MODEL: "deployment", CONF_MAX_TOOL_ITERATIONS: 10},
    )

    assert create.await_count == 2
    second_input = create.await_args_list[1].kwargs["input"]
    assert any(item.get("type") == "function_call_output" for item in second_input)


async def test_reasoning_omits_temperature() -> None:
    """Temperature is not sent while model reasoning is active."""
    client, create = _make_client()

    await client.async_handle_chat_log(
        cast(conversation.ChatLog, FakeChatLog()),
        "conversation.microsoft_foundry",
        {
            CONF_MODEL: "deployment",
            CONF_MAX_TOOL_ITERATIONS: 10,
            CONF_REASONING_EFFORT: "high",
            CONF_TEMPERATURE: 0.5,
        },
    )

    assert "temperature" not in create.await_args_list[0].kwargs
    assert create.await_args_list[0].kwargs["reasoning"] == {"effort": "high"}


async def test_agent_target_uses_agent_reference() -> None:
    """Agent targets omit model parameters and pass an agent reference."""
    client, create = _make_client()

    await client.async_handle_chat_log(
        cast(conversation.ChatLog, FakeChatLog()),
        "conversation.microsoft_foundry",
        {
            CONF_TARGET: "agent:home-agent",
            CONF_MAX_TOOL_ITERATIONS: 10,
            CONF_REASONING_EFFORT: "high",
            CONF_TEMPERATURE: 0.5,
        },
    )

    request = create.await_args_list[0].kwargs
    assert "model" not in request
    assert "reasoning" not in request
    assert "temperature" not in request
    assert request["extra_body"]["agent_reference"] == {
        "type": "agent_reference",
        "name": "home-agent",
    }


async def test_agent_target_receives_home_assistant_tools() -> None:
    """Agent targets receive runtime Home Assistant function tools."""
    client, create = _make_client()
    chat_log = FakeChatLog()
    chat_log.llm_api = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="HassTurnOn",
                description="Turn on a Home Assistant entity",
                parameters=vol.Schema({vol.Required("name"): str}),
            )
        ],
        custom_serializer=None,
    )

    await client.async_handle_chat_log(
        cast(conversation.ChatLog, chat_log),
        "conversation.microsoft_foundry",
        {
            CONF_TARGET: "agent:home-agent",
            CONF_MAX_TOOL_ITERATIONS: 10,
        },
    )

    assert create.await_args_list[0].kwargs["tools"][0]["name"] == "HassTurnOn"


async def test_tool_iteration_limit() -> None:
    """Repeated tool calls stop at the configured iteration limit."""
    client, create = _make_client()

    with pytest.raises(FoundryToolLimitError):
        await client.async_handle_chat_log(
            cast(conversation.ChatLog, FakeChatLog(always_call_tool=True)),
            "conversation.microsoft_foundry",
            {CONF_MODEL: "deployment", CONF_MAX_TOOL_ITERATIONS: 2},
        )

    assert create.await_count == 2
