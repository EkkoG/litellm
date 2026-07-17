from unittest.mock import MagicMock

import httpx

from litellm.llms.moonshot.responses.transformation import MoonshotResponsesAPIConfig
from litellm.types.llms.openai import ResponsesAPIOptionalRequestParams
from litellm.types.router import GenericLiteLLMParams
from litellm.types.utils import LlmProviders
from litellm.utils import ProviderConfigManager


class TestMoonshotResponsesAPIConfig:
    def test_provider_registration_uses_native_responses_config(self):
        config = ProviderConfigManager.get_provider_responses_api_config(
            model="moonshot/kimi-k2.5",
            provider=LlmProviders.MOONSHOT,
        )

        assert isinstance(config, MoonshotResponsesAPIConfig)
        assert config.custom_llm_provider == LlmProviders.MOONSHOT

    def test_get_complete_url(self):
        config = MoonshotResponsesAPIConfig()

        assert config.get_complete_url(None, {}) == "https://api.moonshot.ai/v1/responses"
        assert (
            config.get_complete_url("https://api.kimi.com/coding/v1/", {}) == "https://api.kimi.com/coding/v1/responses"
        )
        assert (
            config.get_complete_url("https://api.kimi.com/coding/v1/responses", {})
            == "https://api.kimi.com/coding/v1/responses"
        )

    def test_validate_environment_uses_explicit_api_key(self):
        config = MoonshotResponsesAPIConfig()

        headers = config.validate_environment(
            headers={"X-Test": "value"},
            model="kimi-k2.5",
            litellm_params=GenericLiteLLMParams(api_key="test-moonshot-key"),
        )

        assert headers == {
            "Authorization": "Bearer test-moonshot-key",
            "Content-Type": "application/json",
            "X-Test": "value",
        }

    def test_preserves_one_token_responses_request(self):
        config = MoonshotResponsesAPIConfig()

        optional_params = config.map_openai_params(
            response_api_optional_params=ResponsesAPIOptionalRequestParams(
                max_output_tokens=1,
                stream=False,
            ),
            model="kimi-k2.5",
            drop_params=False,
        )
        request = config.transform_responses_api_request(
            model="kimi-k2.5",
            input="Reply with one word",
            response_api_optional_request_params=optional_params,
            litellm_params=GenericLiteLLMParams(),
            headers={},
        )

        assert request == {
            "input": "Reply with one word",
            "max_output_tokens": 1,
            "model": "kimi-k2.5",
            "stream": False,
        }

    def test_parses_native_incomplete_response(self):
        config = MoonshotResponsesAPIConfig()
        raw_response = httpx.Response(
            status_code=200,
            json={
                "id": "resp_123",
                "object": "response",
                "created_at": 1,
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "model": "kimi-k2.5",
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_123",
                        "status": "incomplete",
                        "summary": [],
                    },
                    {
                        "type": "message",
                        "id": "msg_123",
                        "status": "incomplete",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "The", "annotations": []}],
                    },
                ],
                "usage": {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
            },
            request=httpx.Request("POST", "https://api.kimi.com/coding/v1/responses"),
        )

        response = config.transform_response_api_response(
            model="kimi-k2.5",
            raw_response=raw_response,
            logging_obj=MagicMock(),
        )

        dumped_response = response.model_dump()

        assert response.status == "incomplete"
        assert response.incomplete_details is not None
        assert response.incomplete_details.reason == "max_output_tokens"
        assert dumped_response["output"][0]["type"] == "reasoning"
        assert dumped_response["output"][1]["type"] == "message"
        assert dumped_response["output"][1]["content"][0]["text"] == "The"

    def test_uses_managed_websocket_bridge(self):
        assert MoonshotResponsesAPIConfig().supports_native_websocket() is False
