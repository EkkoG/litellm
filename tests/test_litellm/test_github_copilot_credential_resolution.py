from unittest.mock import patch

import litellm

from litellm.llms.github_copilot.common_utils import get_github_copilot_access_token
from litellm.types.router import CredentialLiteLLMParams
from litellm.types.utils import CredentialItem
from litellm.utils import load_credentials_from_list


def test_github_copilot_access_token_survives_credential_filter():
    params = CredentialLiteLLMParams(github_copilot_access_token="github-token")

    assert params.model_dump(exclude_none=True) == {"github_copilot_access_token": "github-token"}


def test_managed_copilot_credential_preserves_dedicated_field():
    original_credentials = litellm.credential_list
    litellm.credential_list = [
        CredentialItem(
            credential_name="copilot-main",
            credential_values={"github_copilot_access_token": "github-token"},
            credential_info={"custom_llm_provider": "github_copilot"},
        )
    ]
    try:
        kwargs = {"litellm_credential_name": "copilot-main"}

        load_credentials_from_list(kwargs)

        assert kwargs["github_copilot_access_token"] == "github-token"
        assert "api_key" not in kwargs
    finally:
        litellm.credential_list = original_credentials


def test_copilot_provider_resolves_token_from_credential_name():
    original_credentials = litellm.credential_list
    litellm.credential_list = [
        CredentialItem(
            credential_name="copilot-main",
            credential_values={"github_copilot_access_token": "github-token"},
            credential_info={"custom_llm_provider": "github_copilot"},
        )
    ]
    try:
        kwargs = {"litellm_credential_name": "copilot-main"}
        load_credentials_from_list(kwargs)
        token = get_github_copilot_access_token(kwargs)

        assert token == "github-token"
    finally:
        litellm.credential_list = original_credentials


def test_completion_hydrates_managed_copilot_credential():
    original_credentials = litellm.credential_list
    litellm.credential_list = [
        CredentialItem(
            credential_name="copilot-main",
            credential_values={"github_copilot_access_token": "github-token"},
            credential_info={"custom_llm_provider": "github_copilot"},
        )
    ]
    captured_token = None

    def fake_dispatch(ctx):
        nonlocal captured_token
        captured_token = ctx.litellm_params.get("github_copilot_access_token")
        return litellm.ModelResponse(model="claude-opus-4.8")

    try:
        with patch("litellm.main._complete_custom_openai", side_effect=fake_dispatch):
            litellm.completion(
                model="github_copilot/claude-opus-4.8",
                messages=[{"role": "user", "content": "test"}],
                litellm_credential_name="copilot-main",
            )
    finally:
        litellm.credential_list = original_credentials

    assert captured_token == "github-token"
