from typing import Any

from litellm.exceptions import AuthenticationError
from litellm.llms.openai.openai import OpenAIConfig
from litellm.types.llms.openai import AllMessageValues

from ..authenticator import Authenticator
from ..common_utils import (
    GetAccessTokenError,
    derive_chatgpt_session_id,
    get_chatgpt_default_headers,
)
from .streaming_utils import ChatGPTToolCallNormalizer


class ChatGPTConfig(OpenAIConfig):
    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        custom_llm_provider: str = "openai",
    ) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    def _get_openai_compatible_provider_info(
        self,
        model: str,
        api_base: str | None,
        api_key: str | None,
        custom_llm_provider: str,
    ) -> tuple[str | None, str | None, str]:
        static_api_key = api_key.strip() if api_key else None
        if static_api_key:
            if not api_base:
                raise AuthenticationError(
                    model=model,
                    llm_provider=custom_llm_provider,
                    message="ChatGPT api_key authentication requires an explicit api_base",
                )
            return api_base, static_api_key, custom_llm_provider

        dynamic_api_base = self.authenticator.get_api_base()
        try:
            dynamic_api_key = self.authenticator.get_access_token()
        except GetAccessTokenError as e:
            raise AuthenticationError(
                model=model,
                llm_provider=custom_llm_provider,
                message=str(e),
            )
        return dynamic_api_base, dynamic_api_key, custom_llm_provider

    def validate_environment(
        self,
        headers: dict,
        model: str,
        messages: list[AllMessageValues],
        optional_params: dict,
        litellm_params: dict,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> dict:
        validated_headers = super().validate_environment(
            headers, model, messages, optional_params, litellm_params, api_key, api_base
        )

        configured_api_key = litellm_params.get("api_key")
        static_api_key = configured_api_key.strip() if isinstance(configured_api_key, str) else None
        if static_api_key and not litellm_params.get("api_base"):
            raise AuthenticationError(
                model=model,
                llm_provider="chatgpt",
                message="ChatGPT api_key authentication requires an explicit api_base",
            )

        account_id = None if static_api_key else self.authenticator.get_account_id()
        session_id = derive_chatgpt_session_id(litellm_params, messages, account_id)
        default_headers = get_chatgpt_default_headers(api_key or "", account_id, session_id)
        caller_headers = {
            key: value
            for key, value in validated_headers.items()
            if not isinstance(key, str) or key.lower() not in ("authorization", "chatgpt-account-id")
        }
        return {**default_headers, **caller_headers}

    def post_stream_processing(self, stream: Any) -> Any:
        return ChatGPTToolCallNormalizer(stream)

    def map_openai_params(
        self,
        non_default_params: dict,
        optional_params: dict,
        model: str,
        drop_params: bool,
    ) -> dict:
        optional_params = super().map_openai_params(non_default_params, optional_params, model, drop_params)
        optional_params.setdefault("stream", False)
        return optional_params
