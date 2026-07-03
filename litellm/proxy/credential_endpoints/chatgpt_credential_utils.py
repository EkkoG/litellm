import asyncio
from typing import Any, Optional

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.proxy.common_utils.encrypt_decrypt_utils import encrypt_value_helper
from litellm.proxy.utils import jsonify_object
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem


CHATGPT_CREDENTIAL_PROVIDER = "chatgpt"


def build_chatgpt_credential_values(tokens: dict[str, str], api_base: Optional[str] = None) -> dict[str, str]:
    auth_record = Authenticator()._build_auth_record(tokens)
    values = {
        "api_key": auth_record.get("access_token"),
        "chatgpt_refresh_token": auth_record.get("refresh_token"),
        "chatgpt_id_token": auth_record.get("id_token"),
        "chatgpt_account_id": auth_record.get("account_id"),
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
        loop.create_task(coroutine)
    except RuntimeError:
        asyncio.run(coroutine)


async def _persist_chatgpt_credential_values(
    prisma_client: Any,
    credential_name: str,
    credential_values: dict[str, Any],
    user_id: Optional[str],
) -> None:
    encrypted_values = {key: encrypt_value_helper(value) for key, value in credential_values.items()}
    data = jsonify_object(
        {
            "credential_values": encrypted_values,
            "updated_by": user_id or "litellm_proxy",
        }
    )
    await CredentialsRepository(prisma_client).update_by_name(
        credential_name=credential_name,
        data=data,
    )
