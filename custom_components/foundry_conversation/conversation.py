"""Conversation platform for Microsoft Foundry."""

import logging
from typing import Any, Literal, override

from homeassistant.components import conversation
from homeassistant.const import CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FoundryConfigEntry
from .client import FoundryAuthenticationError, FoundryError, parse_target
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_MODEL,
    CONF_TARGET,
    DEFAULT_NAME,
    DEFAULT_OPTIONS,
    DOMAIN,
    TARGET_MODEL,
)

LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: FoundryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a Microsoft Foundry conversation entity."""
    async_add_entities([FoundryConversationEntity(config_entry)])


class FoundryConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """Microsoft Foundry conversation agent."""

    _attr_name = DEFAULT_NAME
    _attr_supports_streaming = True

    def __init__(self, entry: FoundryConfigEntry) -> None:
        """Initialize the conversation entity."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        target_type = parse_target(
            entry.options.get(CONF_TARGET, entry.options.get(CONF_MODEL, ""))
        )[0]
        if entry.options.get(CONF_ALLOW_CONTROL, False) and target_type == TARGET_MODEL:
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return supported languages."""
        return MATCH_ALL

    @override
    async def async_added_to_hass(self) -> None:
        """Register the entity as a conversation agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Unregister the conversation agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @override
    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process a conversation turn through Microsoft Foundry."""
        options: dict[str, Any] = {**DEFAULT_OPTIONS, **self.entry.options}
        target_type = parse_target(
            options.get(CONF_TARGET, options.get(CONF_MODEL, ""))
        )[0]
        llm_api = (
            llm.LLM_API_ASSIST
            if options.get(CONF_ALLOW_CONTROL, False) and target_type == TARGET_MODEL
            else None
        )
        extra_prompt_parts = [
            (
                "Reply in the language identified by this BCP-47 language tag: "
                f"{user_input.language}."
            )
        ]
        if user_input.extra_system_prompt:
            extra_prompt_parts.append(user_input.extra_system_prompt)

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                llm_api,
                options.get(CONF_PROMPT),
                "\n".join(extra_prompt_parts),
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        assert self.entity_id is not None
        try:
            await self.entry.runtime_data.client.async_handle_chat_log(
                chat_log,
                self.entity_id,
                options,
            )
        except FoundryError as err:
            self._log_error(err)
            if isinstance(err, FoundryAuthenticationError):
                self.entry.async_start_reauth(self.hass)
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                err.user_message(user_input.language),
            )
            return conversation.ConversationResult(
                response=response,
                conversation_id=chat_log.conversation_id,
            )

        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    @staticmethod
    def _log_error(err: FoundryError) -> None:
        """Log API details without logging credentials or request headers."""
        cause = err.__cause__
        LOGGER.error(
            "Microsoft Foundry request failed: category=%s cause=%s status=%s "
            "code=%s message=%s",
            err.error_key,
            type(cause).__name__ if cause else type(err).__name__,
            getattr(cause, "status_code", None),
            getattr(cause, "code", None),
            getattr(cause, "message", str(err)),
        )
