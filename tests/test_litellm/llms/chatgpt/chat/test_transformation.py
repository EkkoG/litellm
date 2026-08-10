from unittest.mock import MagicMock, patch

from litellm.llms.chatgpt.chat.transformation import ChatGPTConfig


class TestChatGPTConfig:
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
