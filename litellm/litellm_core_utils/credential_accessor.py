"""Utils for accessing credentials."""

from typing import List, Optional

from pydantic import TypeAdapter

import litellm
from litellm.types.utils import CredentialItem

_CREDENTIAL_VALUES_ADAPTER = TypeAdapter(dict[str, object])
_CREDENTIAL_INFO_ADAPTER = TypeAdapter(dict[str, object])


class CredentialAccessor:
    @staticmethod
    def get_credential(credential_name: str) -> Optional[CredentialItem]:
        return next(
            (credential for credential in litellm.credential_list if credential.credential_name == credential_name),
            None,
        )

    @staticmethod
    def get_credential_values(credential_name: str) -> dict[str, object]:
        credential = CredentialAccessor.get_credential(credential_name)
        if credential is None:
            return {}
        if (
            _CREDENTIAL_INFO_ADAPTER.validate_python(credential.credential_info or {}).get("custom_llm_provider")
            == "chatgpt"
        ):
            from litellm.proxy.credential_endpoints.chatgpt_credential_utils import (
                refresh_chatgpt_credential_if_needed,
            )

            return refresh_chatgpt_credential_if_needed(credential=credential)
        return _CREDENTIAL_VALUES_ADAPTER.validate_python(credential.credential_values or {})

    @staticmethod
    async def get_credential_values_async(credential_name: str) -> dict[str, object]:
        credential = CredentialAccessor.get_credential(credential_name)
        if credential is None:
            return {}
        if (
            _CREDENTIAL_INFO_ADAPTER.validate_python(credential.credential_info or {}).get("custom_llm_provider")
            == "chatgpt"
        ):
            from litellm.proxy.credential_endpoints.chatgpt_credential_utils import (
                async_refresh_chatgpt_credential_if_needed,
            )

            return await async_refresh_chatgpt_credential_if_needed(credential=credential)
        return _CREDENTIAL_VALUES_ADAPTER.validate_python(credential.credential_values or {})

    @staticmethod
    def upsert_credentials(credentials: List[CredentialItem]) -> None:
        updates = {credential.credential_name: credential for credential in credentials}
        retained = tuple(
            credential for credential in litellm.credential_list if credential.credential_name not in updates
        )
        litellm.credential_list = [*retained, *updates.values()]

    @staticmethod
    def delete_credential(credential_name: str) -> None:
        litellm.credential_list = [
            credential for credential in litellm.credential_list if credential.credential_name != credential_name
        ]
