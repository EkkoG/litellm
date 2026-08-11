from unittest.mock import MagicMock, patch

import pytest

from litellm.exceptions import AuthenticationError
from litellm.llms.chatgpt.chat.transformation import ChatGPTConfig


class TestChatGPTConfig:
    @patch("litellm.llms.chatgpt.chat.transformation.Authenticator")
    def test_static_api_key_provider_info_skips_local_login(self, mock_authenticator_class: MagicMock) -> None:
        config = ChatGPTConfig()

        api_base, api_key, provider = config._get_openai_compatible_provider_info(
            model="gpt-5.6-sol",
            api_base="https://gateway.example.com/backend-api/codex",
            api_key="gateway-key",
            custom_llm_provider="chatgpt",
        )

        assert api_base == "https://gateway.example.com/backend-api/codex"
        assert api_key == "gateway-key"
        assert provider == "chatgpt"
        mock_authenticator_class.return_value.get_access_token.assert_not_called()

    @patch("litellm.llms.chatgpt.chat.transformation.Authenticator")
    def test_static_api_key_requires_api_base(self, mock_authenticator_class: MagicMock) -> None:
        config = ChatGPTConfig()

        with pytest.raises(AuthenticationError, match="requires an explicit api_base"):
            config._get_openai_compatible_provider_info(
                model="gpt-5.6-sol",
                api_base=None,
                api_key="gateway-key",
                custom_llm_provider="chatgpt",
            )

        mock_authenticator_class.return_value.get_access_token.assert_not_called()

    @patch("litellm.llms.chatgpt.chat.transformation.Authenticator")
    def test_static_api_key_omits_account_id_and_ignores_caller_auth(
        self, mock_authenticator_class: MagicMock
    ) -> None:
        mock_authenticator = MagicMock()
        mock_authenticator_class.return_value = mock_authenticator
        config = ChatGPTConfig()

        headers = config.validate_environment(
            headers={
                "Authorization": "Bearer caller-key",
                "ChatGPT-Account-Id": "caller-account",
                "originator": "custom-origin",
            },
            model="gpt-5.6-sol",
            messages=[{"role": "user", "content": "hello"}],
            optional_params={},
            litellm_params={
                "api_key": "gateway-key",
                "api_base": "https://gateway.example.com/backend-api/codex",
            },
            api_key="gateway-key",
            api_base="https://gateway.example.com/backend-api/codex",
        )

        assert headers["Authorization"] == "Bearer gateway-key"
        assert "ChatGPT-Account-Id" not in headers
        assert headers["originator"] == "custom-origin"
        mock_authenticator.get_access_token.assert_not_called()
        mock_authenticator.get_account_id.assert_not_called()

    @patch("litellm.llms.chatgpt.chat.transformation.Authenticator")
    def test_validate_environment_derives_stable_session_id(self, mock_authenticator_class: MagicMock) -> None:
        mock_authenticator = MagicMock()
        mock_authenticator.get_account_id.return_value = "acct-123"
        mock_authenticator_class.return_value = mock_authenticator
        messages = [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "hello"},
        ]

        first_headers = ChatGPTConfig().validate_environment(
            headers={},
            model="gpt-5.6",
            messages=messages,
            optional_params={},
            litellm_params={},
            api_key="access-123",
        )
        second_headers = ChatGPTConfig().validate_environment(
            headers={},
            model="gpt-5.6",
            messages=messages,
            optional_params={},
            litellm_params={},
            api_key="access-123",
        )

        assert first_headers["session_id"] == second_headers["session_id"]
        assert len(first_headers["session_id"]) == 64
