import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.credential_endpoints import endpoints
from litellm.proxy.credential_endpoints.device_login_flow import (
    DeviceLoginCompleted,
    DeviceLoginStarted,
    DeviceLoginState,
    InMemoryDeviceLoginStateStore,
    ProviderChallenge,
)
from litellm.proxy.credential_endpoints.device_login_state_store import RedisDeviceLoginStateStore


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


class FakeLockManager:
    async def acquire_lock(self, cronjob_id, ttl=None):
        return True

    async def release_lock(self, cronjob_id):
        return None


@pytest.mark.asyncio
async def test_start_github_copilot_device_login_maps_flow_result(monkeypatch):
    flow = Mock()
    flow.start = AsyncMock(
        return_value=DeviceLoginStarted(
            login_id="login-123",
            verification_url="https://github.com/login/device",
            user_code="ABCD-EFGH",
            interval_seconds=5,
            expires_at=12345.0,
        )
    )
    monkeypatch.setattr(endpoints, "_build_device_login_flow", lambda: flow)

    response = await endpoints.start_github_copilot_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginStartRequest(credential_name="copilot-admin"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["verification_url"] == "https://github.com/login/device"
    flow.start.assert_awaited_once_with(
        provider="github_copilot",
        credential_name="copilot-admin",
        owner_id="admin-user",
        overwrite_existing=False,
        api_base=None,
    )


@pytest.mark.asyncio
async def test_poll_github_copilot_device_login_maps_completed_result(monkeypatch):
    flow = Mock()
    flow.poll = AsyncMock(return_value=DeviceLoginCompleted(credential_name="copilot-admin"))
    monkeypatch.setattr(endpoints, "_build_device_login_flow", lambda: flow)

    response = await endpoints.poll_github_copilot_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginPollRequest(login_id="login-123"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response == {"success": True, "status": "complete", "credential_name": "copilot-admin"}


@pytest.mark.asyncio
async def test_device_login_state_is_encrypted_in_shared_cache(monkeypatch):
    fake_redis = FakeRedisCache()
    monkeypatch.setattr("litellm.proxy.proxy_server.master_key", "test-master-key")
    store = RedisDeviceLoginStateStore(fake_redis, lock_manager_factory=lambda _: FakeLockManager())
    state = DeviceLoginState(
        login_id="login-encrypted",
        owner_id="admin-user",
        credential_name="copilot-admin",
        overwrite_existing=False,
        challenge=ProviderChallenge(
            provider="github_copilot",
            verification_url="https://github.com/login/device",
            user_code="ABCD-EFGH",
            interval_seconds=5,
            expires_at=time.time() + 60,
            payload={"device_code": "device-secret"},
        ),
        next_poll_at=time.time() + 5,
    )

    await store.save(state)

    stored_value = next(iter(fake_redis.values.values()))
    assert "device-secret" not in stored_value
    assert "admin-user" not in stored_value
    assert await store.get("login-encrypted") == state


@pytest.mark.asyncio
async def test_redis_device_login_claim_removes_state_before_external_work(monkeypatch):
    fake_redis = FakeRedisCache()
    monkeypatch.setattr("litellm.proxy.proxy_server.master_key", "test-master-key")
    store = RedisDeviceLoginStateStore(fake_redis, lock_manager_factory=lambda _: FakeLockManager())
    state = DeviceLoginState(
        login_id="login-atomic-claim",
        owner_id="admin-user",
        credential_name="copilot-admin",
        overwrite_existing=False,
        challenge=ProviderChallenge(
            provider="github_copilot",
            verification_url="https://github.com/login/device",
            user_code="ABCD-EFGH",
            interval_seconds=5,
            expires_at=time.time() + 60,
            payload={"device_code": "device-secret"},
        ),
        next_poll_at=time.time() + 5,
    )
    await store.save(state)

    async with store.claim(state.login_id) as claimed:
        assert claimed == state
        assert await store.get(state.login_id) is None

    assert await store.get(state.login_id) is None


def test_device_login_identity_hashes_api_token():
    identity = endpoints._device_login_owner_id(UserAPIKeyAuth(token="sk-secret"))

    assert identity != "sk-secret"


@pytest.mark.asyncio
async def test_device_login_state_can_only_be_claimed_once():
    states = {}
    locks: dict[str, asyncio.Lock] = {}
    store = InMemoryDeviceLoginStateStore(states, locks)
    state = DeviceLoginState(
        login_id="login-claim",
        owner_id="admin-user",
        credential_name="copilot-admin",
        overwrite_existing=False,
        challenge=ProviderChallenge(
            provider="github_copilot",
            verification_url="https://github.com/login/device",
            user_code="ABCD-EFGH",
            interval_seconds=5,
            expires_at=time.time() + 60,
            payload={"device_code": "device-123"},
        ),
        next_poll_at=time.time() + 5,
    )
    await store.save(state)

    async with store.claim("login-claim") as first_claim:
        async with store.claim("login-claim") as second_claim:
            assert first_claim == state
            assert second_claim is None
