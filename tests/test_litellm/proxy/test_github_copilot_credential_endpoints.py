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


class FakeRedisCache:
    def __init__(self):
        self.values = {}

    async def async_set_cache(self, key, value, **kwargs):
        self.values[key] = value
        return True

    async def async_get_cache(self, key):
        return self.values.get(key)

    async def async_delete_cache(self, key):
        self.values.pop(key, None)


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
    login_state = await endpoints._get_github_copilot_device_login_state(response["login_id"])
    assert login_state is not None
    assert login_state.credential_name == "copilot-admin"
    await endpoints._delete_github_copilot_device_login_state(response["login_id"])
    assert fake_prisma_client.table.created_data is None


@pytest.mark.asyncio
async def test_poll_github_copilot_device_login_creates_credential(monkeypatch):
    litellm.credential_list = []
    fake_prisma_client = FakePrismaClient()
    login_id = "login-123"
    await endpoints._store_github_copilot_device_login_state(
        login_id,
        endpoints.GitHubCopilotDeviceLoginState(
            credential_name="copilot-admin",
            user_id="admin-user",
            device_code="device-123",
            interval=5,
            expires_at=time.time() + 60,
        ),
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
    assert await endpoints._get_github_copilot_device_login_state(login_id) is None


@pytest.mark.asyncio
async def test_device_login_state_is_encrypted_in_shared_cache(monkeypatch):
    fake_redis = FakeRedisCache()
    monkeypatch.setattr("litellm.proxy.proxy_server.redis_usage_cache", fake_redis)
    monkeypatch.setattr("litellm.proxy.proxy_server.master_key", "test-master-key")
    state = endpoints.GitHubCopilotDeviceLoginState(
        credential_name="copilot-admin",
        user_id="admin-user",
        device_code="device-secret",
        interval=5,
        expires_at=time.time() + 60,
    )

    await endpoints._store_github_copilot_device_login_state("login-encrypted", state)

    stored_value = next(iter(fake_redis.values.values()))
    assert isinstance(stored_value, str)
    assert "device-secret" not in stored_value
    assert "admin-user" not in stored_value
    assert await endpoints._get_github_copilot_device_login_state("login-encrypted") == state


def test_device_login_identity_hashes_api_token():
    identity = endpoints._github_copilot_device_login_user_id(UserAPIKeyAuth(token="sk-secret"))

    assert identity is not None
    assert identity != "sk-secret"


@pytest.mark.asyncio
async def test_device_login_state_can_only_be_claimed_once(monkeypatch):
    monkeypatch.setattr("litellm.proxy.proxy_server.redis_usage_cache", None)
    login_id = "login-claim"
    await endpoints._store_github_copilot_device_login_state(
        login_id,
        endpoints.GitHubCopilotDeviceLoginState(
            credential_name="copilot-admin",
            user_id="admin-user",
            device_code="device-123",
            interval=5,
            expires_at=time.time() + 60,
        ),
    )

    async with endpoints._claim_github_copilot_device_login_state(login_id) as first_claim:
        async with endpoints._claim_github_copilot_device_login_state(login_id) as second_claim:
            assert first_claim is not None
            assert second_claim is None

    await endpoints._delete_github_copilot_device_login_state(login_id)
