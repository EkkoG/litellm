from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Optional, Protocol

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.proxy.credential_endpoints.oauth_credential_refresh import OAuthCredentialRefreshManager
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem

CHATGPT_CREDENTIAL_PROVIDER = "chatgpt"


class ChatGPTAuthenticator(Protocol):
    def is_access_token_expired(self, values: Mapping[str, object]) -> bool: ...

    async def async_refresh_tokens(self, refresh_token: str) -> dict[str, str]: ...


def build_chatgpt_credential_values(tokens: dict[str, str], api_base: Optional[str] = None) -> dict[str, str]:
    auth_record = Authenticator()._build_auth_record(tokens)  # pyright: ignore[reportPrivateUsage]  # legacy SDK API
    values = {
        "api_key": auth_record.get("access_token"),
        "chatgpt_refresh_token": auth_record.get("refresh_token"),
        "chatgpt_id_token": auth_record.get("id_token"),
        "chatgpt_account_id": auth_record.get("account_id"),
        "chatgpt_plan_type": auth_record.get("plan_type"),
        "chatgpt_expires_at": (
            str(auth_record.get("expires_at")) if isinstance(auth_record.get("expires_at"), (int, float, str)) else None
        ),
        "api_base": api_base,
    }
    return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class ChatGPTCredentialRefreshAdapter:
    authenticator: ChatGPTAuthenticator = field(default_factory=Authenticator)

    def needs_refresh(self, values: Mapping[str, object]) -> bool:
        return self.authenticator.is_access_token_expired(dict(values)) and bool(values.get("chatgpt_refresh_token"))

    async def refresh_values(self, values: Mapping[str, object]) -> dict[str, str]:
        refresh_token = values.get("chatgpt_refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return {}
        tokens = await self.authenticator.async_refresh_tokens(refresh_token)
        api_base = values.get("api_base")
        return build_chatgpt_credential_values(
            tokens=tokens,
            api_base=api_base if isinstance(api_base, str) else None,
        )


class ChatGPTCredentialRefreshManager(OAuthCredentialRefreshManager):
    def __init__(
        self,
        authenticator: ChatGPTAuthenticator | None = None,
        repository_factory: Callable[[], CredentialsRepository | None] | None = None,
        credential_getter: Callable[[str], CredentialItem | None] | None = None,
        credential_upsert: Callable[[list[CredentialItem]], None] | None = None,
    ) -> None:
        super().__init__(
            adapter=ChatGPTCredentialRefreshAdapter(authenticator=authenticator or Authenticator()),
            repository_factory=repository_factory,
            credential_getter=credential_getter,
            credential_upsert=credential_upsert,
        )


def _repository_factory() -> CredentialsRepository | None:
    try:
        from litellm.proxy.proxy_server import prisma_client
    except ImportError:
        return None
    return CredentialsRepository(prisma_client) if prisma_client is not None else None


def _credential_getter(credential_name: str) -> CredentialItem | None:
    from litellm.litellm_core_utils.credential_accessor import CredentialAccessor

    return CredentialAccessor.get_credential(credential_name)


def _credential_upsert(credentials: list[CredentialItem]) -> None:
    from litellm.litellm_core_utils.credential_accessor import CredentialAccessor

    CredentialAccessor.upsert_credentials(credentials)


_CHATGPT_CREDENTIAL_REFRESH_MANAGER = ChatGPTCredentialRefreshManager(
    repository_factory=_repository_factory,
    credential_getter=_credential_getter,
    credential_upsert=_credential_upsert,
)


def refresh_chatgpt_credential_if_needed(
    credential: CredentialItem,
    user_id: Optional[str] = None,
) -> dict[str, object]:
    return _CHATGPT_CREDENTIAL_REFRESH_MANAGER.resolve(credential=credential, user_id=user_id)


async def async_refresh_chatgpt_credential_if_needed(
    credential: CredentialItem,
    user_id: Optional[str] = None,
) -> dict[str, object]:
    return await _CHATGPT_CREDENTIAL_REFRESH_MANAGER.aresolve(credential=credential, user_id=user_id)


async def drain_chatgpt_credential_refreshes() -> None:
    await _CHATGPT_CREDENTIAL_REFRESH_MANAGER.drain()
