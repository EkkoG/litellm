import json
import time

import pytest

import litellm
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.credential_endpoints import endpoints


class FakeCredentialTable:
    def __init__(self):
        self.created_data = None

    async def find_unique(self, where):
        return None

    async def create(self, data):
        self.created_data = data
        return data


class FakePrismaClient:
    def __init__(self):
        self.table = FakeCredentialTable()
        self.db = type("FakeDB", (), {"litellm_credentialstable": self.table})()


@pytest.mark.asyncio
async def test_start_github_copilot_device_login_returns_device_code(monkeypatch):
    fake_prisma_client = FakePrismaClient()
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", fake_prisma_client)
    monkeypatch.setattr(
        "litellm.llms.github_copilot.authenticator.Authenticator._get_device_code",
        lambda self: {
            "device_code": "device-123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "interval": "5",
            "expires_in": "900",
        },
    )

    response = await endpoints.start_github_copilot_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginStartRequest(credential_name="copilot-admin"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["user_code"] == "ABCD-EFGH"
    assert response["verification_url"] == "https://github.com/login/device"
    assert endpoints._github_copilot_device_login_flows[response["login_id"]].credential_name == "copilot-admin"
    assert fake_prisma_client.table.created_data is None


@pytest.mark.asyncio
async def test_poll_github_copilot_device_login_creates_credential(monkeypatch):
    litellm.credential_list = []
    fake_prisma_client = FakePrismaClient()
    login_id = "login-123"
    endpoints._github_copilot_device_login_flows[login_id] = endpoints.GitHubCopilotDeviceLoginState(
        credential_name="copilot-admin",
        user_id="admin-user",
        device_code="device-123",
        interval=5,
        expires_at=time.time() + 60,
    )
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", fake_prisma_client)
    monkeypatch.setattr(
        "litellm.llms.github_copilot.authenticator.Authenticator._poll_for_access_token_once",
        lambda self, device_code: "github-access-token",
    )
    monkeypatch.setattr(
        "litellm.proxy.credential_endpoints.endpoints.CredentialHelperUtils.encrypt_credential_values",
        lambda credential: credential,
    )

    response = await endpoints.poll_github_copilot_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginPollRequest(login_id=login_id),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["status"] == "complete"
    stored_values = json.loads(fake_prisma_client.table.created_data["credential_values"])
    assert stored_values == {"github_copilot_access_token": "github-access-token"}
    assert litellm.credential_list[0].credential_info["custom_llm_provider"] == "github_copilot"
    assert login_id not in endpoints._github_copilot_device_login_flows
