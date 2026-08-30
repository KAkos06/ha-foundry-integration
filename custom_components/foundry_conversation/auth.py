"""Authentication helpers for Microsoft Foundry."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import openai
from azure.identity.aio import ClientSecretCredential, get_bearer_token_provider
from homeassistant.const import CONF_API_KEY
from httpx import AsyncClient

from .const import (
    AUTH_API_KEY,
    AZURE_AI_SCOPE,
    CONF_AUTH_TYPE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_ENDPOINT,
    CONF_TENANT_ID,
)


@dataclass(slots=True)
class FoundryConnection:
    """Clients and credentials created for a Foundry connection."""

    openai_client: openai.AsyncOpenAI
    credential: ClientSecretCredential | None = None

    async def async_close(self) -> None:
        """Close resources owned by the connection."""
        if self.credential is not None:
            await self.credential.close()


def create_foundry_connection(
    data: Mapping[str, Any], http_client: AsyncClient
) -> FoundryConnection:
    """Create an OpenAI-compatible client for the configured authentication."""
    credential: ClientSecretCredential | None = None
    api_key: str | Callable[[], Awaitable[str]]
    if data.get(CONF_AUTH_TYPE, AUTH_API_KEY) == AUTH_API_KEY:
        api_key = data[CONF_API_KEY]
    else:
        credential = ClientSecretCredential(
            tenant_id=data[CONF_TENANT_ID],
            client_id=data[CONF_CLIENT_ID],
            client_secret=data[CONF_CLIENT_SECRET],
        )
        api_key = get_bearer_token_provider(credential, AZURE_AI_SCOPE)

    return FoundryConnection(
        openai_client=openai.AsyncOpenAI(
            api_key=api_key,
            base_url=data[CONF_ENDPOINT],
            http_client=http_client,
        ),
        credential=credential,
    )
