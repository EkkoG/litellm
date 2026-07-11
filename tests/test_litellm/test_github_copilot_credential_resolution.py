import litellm

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
