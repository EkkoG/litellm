import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from litellm.llms.xai.oauth import XAI_OAUTH_EXPIRY_SKEW_SECONDS, XAIAuthRecord, XAIOAuthAuthenticator
from litellm.proxy.credential_endpoints.oauth_credential_refresh import OAuthCredentialRefreshManager
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem

XAI_CREDENTIAL_PROVIDER = "xai"


class XAIAuthenticator(Protocol):
    async def async_refresh_auth_record(self, auth_record: XAIAuthRecord) -> XAIAuthRecord: ...


@dataclass(frozen=True, slots=True)
class XAICredentialRefreshAdapter:
    authenticator: XAIAuthenticator = field(default_factory=XAIOAuthAuthenticator)

    def needs_refresh(self, values: Mapping[str, object]) -> bool:
        expires_at = values.get("xai_oauth_expires_at")
        refresh_token = values.get("xai_oauth_refresh_token")
        try:
            expired = time.time() >= float(str(expires_at)) - XAI_OAUTH_EXPIRY_SKEW_SECONDS
        except (TypeError, ValueError):
            return False
        return expired and isinstance(refresh_token, str) and bool(refresh_token)

    async def refresh_values(self, values: Mapping[str, object]) -> dict[str, str]:
        access_token = values.get("api_key")
        refresh_token = values.get("xai_oauth_refresh_token")
        token_endpoint = values.get("xai_oauth_token_endpoint")
        expires_at = values.get("xai_oauth_expires_at")
        if not isinstance(access_token, str) or not access_token:
            return {}
        if not isinstance(refresh_token, str) or not refresh_token:
            return {}
        if not isinstance(token_endpoint, str) or not token_endpoint:
            return {}
        try:
            parsed_expires_at = int(float(str(expires_at)))
        except (TypeError, ValueError):
            return {}
        refreshed = await self.authenticator.async_refresh_auth_record(
            XAIAuthRecord(
                access_token=access_token,
                refresh_token=refresh_token,
                token_endpoint=token_endpoint,
                expires_at=parsed_expires_at,
            )
        )
        return {
            "api_key": refreshed.access_token,
            "xai_oauth_refresh_token": refreshed.refresh_token,
            "xai_oauth_expires_at": str(refreshed.expires_at),
            "xai_oauth_token_endpoint": refreshed.token_endpoint,
        }


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


_XAI_CREDENTIAL_REFRESH_MANAGER = OAuthCredentialRefreshManager(
    adapter=XAICredentialRefreshAdapter(),
    repository_factory=_repository_factory,
    credential_getter=_credential_getter,
    credential_upsert=_credential_upsert,
)


def refresh_xai_credential_if_needed(
    credential: CredentialItem,
    user_id: str | None = None,
) -> dict[str, object]:
    return _XAI_CREDENTIAL_REFRESH_MANAGER.resolve(credential=credential, user_id=user_id)


async def async_refresh_xai_credential_if_needed(
    credential: CredentialItem,
    user_id: str | None = None,
) -> dict[str, object]:
    return await _XAI_CREDENTIAL_REFRESH_MANAGER.aresolve(credential=credential, user_id=user_id)


def force_refresh_xai_credential(
    credential: CredentialItem,
    rejected_access_token: str,
    user_id: str | None = None,
) -> dict[str, object]:
    return _XAI_CREDENTIAL_REFRESH_MANAGER.force_refresh(
        credential=credential,
        rejected_access_token=rejected_access_token,
        user_id=user_id,
    )


async def async_force_refresh_xai_credential(
    credential: CredentialItem,
    rejected_access_token: str,
    user_id: str | None = None,
) -> dict[str, object]:
    return await _XAI_CREDENTIAL_REFRESH_MANAGER.aforce_refresh(
        credential=credential,
        rejected_access_token=rejected_access_token,
        user_id=user_id,
    )


async def drain_xai_credential_refreshes() -> None:
    await _XAI_CREDENTIAL_REFRESH_MANAGER.drain()
