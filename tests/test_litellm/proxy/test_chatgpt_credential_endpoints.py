import time
import json

import pytest

import litellm
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.credential_endpoints import endpoints


class FakeCredentialTable:
    def __init__(self):
        self.created_data = None
        self.updated_data = None

    async def find_unique(self, where):
        return None

    async def create(self, data):
        self.created_data = data
        return data

    async def update(self, where, data):
        self.updated_data = data
        return data


class FakePrismaClient:
    def __init__(self):
        self.table = FakeCredentialTable()
        self.db = type("FakeDB", (), {"litellm_credentialstable": self.table})()


@pytest.mark.asyncio
async def test_start_chatgpt_device_login_returns_device_code(monkeypatch):
    fake_prisma_client = FakePrismaClient()
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", fake_prisma_client)
    monkeypatch.setattr(
        "litellm.llms.chatgpt.authenticator.Authenticator._request_device_code",
        lambda self: {
            "device_auth_id": "device-123",
            "user_code": "ABCD-EFGH",
            "interval": "5",
        },
    )

    response = await endpoints.start_chatgpt_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginStartRequest(credential_name="chatgpt-admin"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["user_code"] == "ABCD-EFGH"
    assert response["verification_url"].endswith("/codex/device")
    assert endpoints._chatgpt_device_login_flows[response["login_id"]].credential_name == "chatgpt-admin"
    assert fake_prisma_client.table.created_data is None


@pytest.mark.asyncio
async def test_poll_chatgpt_device_login_creates_credential(monkeypatch):
    litellm.credential_list = []
    fake_prisma_client = FakePrismaClient()
    login_id = "login-123"
    endpoints._chatgpt_device_login_flows[login_id] = endpoints.ChatGPTDeviceLoginState(
        credential_name="chatgpt-admin",
        overwrite_existing=False,
        user_id="admin-user",
        device_auth_id="device-123",
        user_code="ABCD-EFGH",
        interval=5,
        expires_at=time.time() + 60,
    )
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", fake_prisma_client)
    monkeypatch.setattr(
        "litellm.llms.chatgpt.authenticator.Authenticator._poll_for_authorization_code_once",
        lambda self, device_code: {
            "authorization_code": "auth-code",
            "code_verifier": "verifier",
            "code_challenge": "challenge",
        },
    )
    monkeypatch.setattr(
        "litellm.llms.chatgpt.authenticator.Authenticator._exchange_code_for_tokens",
        lambda self, code_data: {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
        },
    )
    monkeypatch.setattr(
        "litellm.proxy.credential_endpoints.endpoints.CredentialHelperUtils.encrypt_credential_values",
        lambda credential: credential,
    )

    response = await endpoints.poll_chatgpt_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginPollRequest(login_id=login_id),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["status"] == "complete"
    stored_values = json.loads(fake_prisma_client.table.created_data["credential_values"])
    assert stored_values["api_key"] == "access-token"
    assert litellm.credential_list[0].credential_name == "chatgpt-admin"
    assert litellm.credential_list[0].credential_values["chatgpt_refresh_token"] == "refresh-token"
    assert login_id not in endpoints._chatgpt_device_login_flows
