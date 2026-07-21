from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from litellm.llms.custom_httpx.llm_http_handler import BaseLLMHTTPHandler


class _ProviderConfig:
    max_retry_on_unprocessable_entity_error = 0

    def should_retry_llm_api_inside_llm_translation_on_http_error(self, e, litellm_params):
        return False

    def get_error_class(self, error_message, status_code, headers):
        return RuntimeError(error_message)


class _Logging:
    pass


def _response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    return httpx.Response(status_code, request=request)


@pytest.mark.asyncio
async def test_async_managed_xai_401_refreshes_and_retries_once(monkeypatch):
    client = Mock()
    client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError("unauthorized", request=_response(401).request, response=_response(401)),
            _response(200),
        ]
    )
    refresh = AsyncMock(return_value={"api_key": "new-token"})
    monkeypatch.setattr(
        "litellm.litellm_core_utils.credential_accessor.CredentialAccessor.async_force_refresh_after_unauthorized",
        refresh,
    )

    response = await BaseLLMHTTPHandler()._make_common_async_call(
        async_httpx_client=client,
        provider_config=_ProviderConfig(),
        api_base="https://api.x.ai/v1/chat/completions",
        headers={"Authorization": "Bearer old-token"},
        data={"model": "grok"},
        timeout=30,
        litellm_params={
            "api_key": "old-token",
            "managed_credential_name": "xai-admin",
            "managed_credential_provider": "xai",
            "managed_credential_auth_type": "oauth_json_import",
        },
        logging_obj=_Logging(),
    )

    assert response.status_code == 200
    assert client.post.await_count == 2
    assert client.post.await_args_list[0].kwargs["headers"]["Authorization"] == "Bearer old-token"
    assert client.post.await_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new-token"
    refresh.assert_awaited_once_with("xai-admin", "old-token")


def test_sync_static_xai_401_does_not_refresh(monkeypatch):
    error_response = _response(401)
    client = Mock()
    client.post.side_effect = httpx.HTTPStatusError(
        "unauthorized",
        request=error_response.request,
        response=error_response,
    )
    refresh = Mock()
    monkeypatch.setattr(
        "litellm.litellm_core_utils.credential_accessor.CredentialAccessor.force_refresh_after_unauthorized",
        refresh,
    )

    with pytest.raises(Exception):
        BaseLLMHTTPHandler()._make_common_sync_call(
            sync_httpx_client=client,
            provider_config=_ProviderConfig(),
            api_base="https://api.x.ai/v1/chat/completions",
            headers={"Authorization": "Bearer static-token"},
            data={"model": "grok"},
            timeout=30,
            litellm_params={"api_key": "static-token"},
            logging_obj=_Logging(),
        )

    assert client.post.call_count == 1
    refresh.assert_not_called()
