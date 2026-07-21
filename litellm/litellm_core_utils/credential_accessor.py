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
        credential_info = _CREDENTIAL_INFO_ADAPTER.validate_python(credential.model_dump()["credential_info"])
        provider = credential_info.get("custom_llm_provider")
        auth_type = credential_info.get("auth_type")
        match (provider, auth_type):
            case ("chatgpt", _):
                from litellm.proxy.credential_endpoints.chatgpt_credential_utils import (
                    refresh_chatgpt_credential_if_needed,
                )

                return refresh_chatgpt_credential_if_needed(credential=credential)
            case ("xai", "oauth_json_import"):
                from litellm.proxy.credential_endpoints.xai_credential_utils import (
                    refresh_xai_credential_if_needed,
                )

                return refresh_xai_credential_if_needed(credential=credential)
            case _:
                return _CREDENTIAL_VALUES_ADAPTER.validate_python(credential.model_dump()["credential_values"])

    @staticmethod
    async def get_credential_values_async(credential_name: str) -> dict[str, object]:
        credential = CredentialAccessor.get_credential(credential_name)
        if credential is None:
            return {}
        credential_info = _CREDENTIAL_INFO_ADAPTER.validate_python(credential.model_dump()["credential_info"])
        provider = credential_info.get("custom_llm_provider")
        auth_type = credential_info.get("auth_type")
        match (provider, auth_type):
            case ("chatgpt", _):
                from litellm.proxy.credential_endpoints.chatgpt_credential_utils import (
                    async_refresh_chatgpt_credential_if_needed,
                )

                return await async_refresh_chatgpt_credential_if_needed(credential=credential)
            case ("xai", "oauth_json_import"):
                from litellm.proxy.credential_endpoints.xai_credential_utils import (
                    async_refresh_xai_credential_if_needed,
                )

                return await async_refresh_xai_credential_if_needed(credential=credential)
            case _:
                return _CREDENTIAL_VALUES_ADAPTER.validate_python(credential.model_dump()["credential_values"])

    @staticmethod
    def force_refresh_after_unauthorized(
        credential_name: str,
        rejected_access_token: str,
    ) -> dict[str, object] | None:
        credential = CredentialAccessor.get_credential(credential_name)
        if credential is None:
            return None
        credential_info = _CREDENTIAL_INFO_ADAPTER.validate_python(credential.model_dump()["credential_info"])
        if (
            credential_info.get("custom_llm_provider") != "xai"
            or credential_info.get("auth_type") != "oauth_json_import"
        ):
            return None
        from litellm.proxy.credential_endpoints.xai_credential_utils import force_refresh_xai_credential

        return force_refresh_xai_credential(credential, rejected_access_token)

    @staticmethod
    async def async_force_refresh_after_unauthorized(
        credential_name: str,
        rejected_access_token: str,
    ) -> dict[str, object] | None:
        credential = CredentialAccessor.get_credential(credential_name)
        if credential is None:
            return None
        credential_info = _CREDENTIAL_INFO_ADAPTER.validate_python(credential.model_dump()["credential_info"])
        if (
            credential_info.get("custom_llm_provider") != "xai"
            or credential_info.get("auth_type") != "oauth_json_import"
        ):
            return None
        from litellm.proxy.credential_endpoints.xai_credential_utils import async_force_refresh_xai_credential

        return await async_force_refresh_xai_credential(credential, rejected_access_token)

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
