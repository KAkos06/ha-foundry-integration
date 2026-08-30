"""Authentication helpers for Microsoft Foundry."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import openai
from azure.identity.aio import ClientSecretCredential
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
    FOUNDRY_PROJECT_SCOPE,
)


@dataclass(slots=True)
class FoundryConnection:
    """Clients and credentials created for a Foundry connection."""

    openai_client: openai.AsyncOpenAI
    credential: ClientSecretCredential | None = None
    api_key: str | None = None

    async def async_close(self) -> None:
        """Close resources owned by the connection."""
        if self.credential is not None:
            await self.credential.close()


class FoundryAsyncOpenAI(openai.AsyncOpenAI):
    """OpenAI client variant that sends Azure Foundry API keys correctly."""

    def __init__(self, *args: Any, foundry_api_key: str | None = None, **kwargs: Any) -> None:
        """Store the raw Foundry API key for header overrides."""
        super().__init__(*args, **kwargs)
        self._foundry_api_key = foundry_api_key

    @property
    def auth_headers(self) -> dict[str, str]:
        """Use ``api-key`` for Foundry API-key auth instead of bearer auth."""
        if self._foundry_api_key:
            return {"api-key": self._foundry_api_key}
        return super().auth_headers


def _build_foundry_token_provider(
    credential: ClientSecretCredential,
) -> Callable[[], Awaitable[str]]:
    """Return a bearer-token provider for Foundry/OpenAI v1 endpoints.

    Microsoft documents ``https://ai.azure.com/.default`` for Responses API
    calls with Microsoft Entra ID. We keep a secondary fallback only if token
    acquisition for that audience fails entirely.
    """

    async def _get_token() -> str:
        last_error: Exception | None = None
        for scope in (FOUNDRY_PROJECT_SCOPE, AZURE_AI_SCOPE):
            try:
                token = await credential.get_token(scope)
            except Exception as err:
                last_error = err
                continue
            return token.token
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Microsoft Foundry token scope succeeded")

    return _get_token


def create_foundry_connection(
    data: Mapping[str, Any], http_client: AsyncClient
) -> FoundryConnection:
    """Create an OpenAI-compatible client for the configured authentication."""
    credential: ClientSecretCredential | None = None
    raw_api_key: str | None = None
    api_key: str | Callable[[], Awaitable[str]]
    if data.get(CONF_AUTH_TYPE, AUTH_API_KEY) == AUTH_API_KEY:
        raw_api_key = data[CONF_API_KEY]
        api_key = raw_api_key
    else:
        credential = ClientSecretCredential(
            tenant_id=data[CONF_TENANT_ID],
            client_id=data[CONF_CLIENT_ID],
            client_secret=data[CONF_CLIENT_SECRET],
        )
        api_key = _build_foundry_token_provider(credential)

    return FoundryConnection(
        openai_client=FoundryAsyncOpenAI(
            api_key=api_key,
            base_url=data[CONF_ENDPOINT],
            http_client=http_client,
            foundry_api_key=raw_api_key,
        ),
        credential=credential,
        api_key=raw_api_key,
    )

