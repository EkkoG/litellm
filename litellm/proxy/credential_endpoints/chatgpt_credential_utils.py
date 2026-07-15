import asyncio
from typing import Any, Optional

from litellm._logging import verbose_proxy_logger
from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.proxy.credential_endpoints.credential_writer import CredentialNotFound, CredentialWriter
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem

CHATGPT_CREDENTIAL_PROVIDER = "chatgpt"
_BACKGROUND_CREDENTIAL_TASKS: set[asyncio.Task[None]] = set()


def build_chatgpt_credential_values(tokens: dict[str, str], api_base: Optional[str] = None) -> dict[str, str]:
    auth_record = Authenticator()._build_auth_record(tokens)
    values = {
        "api_key": auth_record.get("access_token"),
        "chatgpt_refresh_token": auth_record.get("refresh_token"),
        "chatgpt_id_token": auth_record.get("id_token"),
        "chatgpt_account_id": auth_record.get("account_id"),
        "chatgpt_plan_type": auth_record.get("plan_type"),
        "chatgpt_expires_at": str(auth_record["expires_at"]) if auth_record.get("expires_at") is not None else None,
        "api_base": api_base,
    }
    return {key: value for key, value in values.items() if value is not None}


def refresh_chatgpt_credential_if_needed(
    credential: CredentialItem,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    credential_values = dict(credential.credential_values or {})
    authenticator = Authenticator()
    access_token = credential_values.get("api_key") or credential_values.get("chatgpt_access_token")
    auth_data = authenticator._build_auth_data_from_params(api_key=access_token, litellm_params=credential_values)
    if access_token and not authenticator._is_token_expired(auth_data, access_token):
        return credential_values

    refresh_token = credential_values.get("chatgpt_refresh_token")
    if not refresh_token:
        return credential_values

    refreshed_tokens = authenticator._refresh_tokens(refresh_token)
    refreshed_values = build_chatgpt_credential_values(
        tokens=refreshed_tokens,
        api_base=credential_values.get("api_base"),
    )
    credential_values.update(refreshed_values)
    credential.credential_values = credential_values
    _schedule_chatgpt_credential_persist(
        credential_name=credential.credential_name,
        credential_values=credential_values,
        user_id=user_id,
    )
    return dict(credential_values)


def _schedule_chatgpt_credential_persist(
    credential_name: str,
    credential_values: dict[str, Any],
    user_id: Optional[str] = None,
) -> None:
    try:
        from litellm.proxy.proxy_server import prisma_client
    except ImportError:
        return

    if prisma_client is None:
        return

    coroutine = _persist_chatgpt_credential_values(
        prisma_client=prisma_client,
        credential_name=credential_name,
        credential_values=credential_values,
        user_id=user_id,
    )
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coroutine)
        _BACKGROUND_CREDENTIAL_TASKS.add(task)
        task.add_done_callback(_handle_credential_persist_result)
    except RuntimeError:
        asyncio.run(coroutine)


def _handle_credential_persist_result(task: asyncio.Task[None]) -> None:
    _BACKGROUND_CREDENTIAL_TASKS.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        verbose_proxy_logger.error("Failed to persist refreshed ChatGPT credential: %s", exception)


async def _persist_chatgpt_credential_values(
    prisma_client: Any,
    credential_name: str,
    credential_values: dict[str, Any],
    user_id: Optional[str],
) -> None:
    result = await CredentialWriter(CredentialsRepository(prisma_client)).patch(
        credential_name=credential_name,
        patch=CredentialItem(
            credential_name=credential_name,
            credential_values=credential_values,
            credential_info={},
        ),
        actor_id=user_id or "litellm_proxy",
    )
    if isinstance(result, CredentialNotFound):
        raise ValueError(f"Credential not found: {credential_name}")
