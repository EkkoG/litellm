import pytest

from litellm.llms.chatgpt.device_authorization import ChatGPTDeviceAuthorizationProvider
from litellm.proxy.credential_endpoints.device_login_flow import ProviderAuthorized, ProviderChallenge


class FakeAuthenticator:
    def request_device_code(self):
        return {"device_auth_id": "device-123", "user_code": "ABCD-EFGH", "interval": "5"}

    def poll_for_authorization_code_once(self, device_code):
        return {
            "authorization_code": "authorization-code",
            "code_verifier": "verifier",
            "code_challenge": "challenge",
        }

    def exchange_code_for_tokens(self, code_data):
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
        }


@pytest.mark.asyncio
async def test_chatgpt_device_authorization_builds_managed_credential():
    provider = ChatGPTDeviceAuthorizationProvider(FakeAuthenticator())

    challenge = await provider.begin(api_base="https://chatgpt.example.com")

    assert isinstance(challenge, ProviderChallenge)
    result = await provider.poll(challenge, credential_name="chatgpt-admin")
    assert isinstance(result, ProviderAuthorized)
    assert result.credential.credential_name == "chatgpt-admin"
    assert result.credential.credential_values["api_key"] == "access-token"
    assert result.credential.credential_values["chatgpt_refresh_token"] == "refresh-token"
    assert result.credential.credential_values["api_base"] == "https://chatgpt.example.com"
