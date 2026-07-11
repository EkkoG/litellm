import pytest

from litellm.llms.github_copilot.device_authorization import GitHubCopilotDeviceAuthorizationProvider
from litellm.proxy.credential_endpoints.device_login_flow import ProviderAuthorized, ProviderChallenge, ProviderPending


class FakeAuthenticator:
    def __init__(self) -> None:
        self.access_token = None

    def request_device_code(self):
        return {
            "device_code": "device-123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "interval": "5",
            "expires_in": "900",
        }

    def poll_for_access_token_once(self, device_code):
        return self.access_token


@pytest.mark.asyncio
async def test_github_copilot_device_authorization_maps_pending_and_authorized():
    authenticator = FakeAuthenticator()
    provider = GitHubCopilotDeviceAuthorizationProvider(authenticator)
    challenge = await provider.begin(api_base=None)
    assert isinstance(challenge, ProviderChallenge)

    pending = await provider.poll(challenge, credential_name="copilot-admin")
    assert isinstance(pending, ProviderPending)

    authenticator.access_token = "github-access-token"
    authorized = await provider.poll(challenge, credential_name="copilot-admin")
    assert isinstance(authorized, ProviderAuthorized)
    assert authorized.credential.credential_values == {"github_copilot_access_token": "github-access-token"}
