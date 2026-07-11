from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.credential_endpoints import endpoints
from litellm.proxy.credential_endpoints.device_login_flow import DeviceLoginCompleted, DeviceLoginStarted


@pytest.mark.asyncio
async def test_start_chatgpt_device_login_maps_flow_result(monkeypatch):
    flow = Mock()
    flow.start = AsyncMock(
        return_value=DeviceLoginStarted(
            login_id="login-123",
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            interval_seconds=5,
            expires_at=12345.0,
        )
    )
    monkeypatch.setattr(endpoints, "_build_device_login_flow", lambda: flow)

    response = await endpoints.start_chatgpt_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginStartRequest(credential_name="chatgpt-admin"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response == {
        "success": True,
        "login_id": "login-123",
        "verification_url": "https://auth.openai.com/codex/device",
        "user_code": "ABCD-EFGH",
        "interval": 5,
        "expires_at": 12345.0,
    }
    flow.start.assert_awaited_once_with(
        provider="chatgpt",
        credential_name="chatgpt-admin",
        owner_id="admin-user",
        overwrite_existing=False,
        api_base=None,
    )


@pytest.mark.asyncio
async def test_poll_chatgpt_device_login_maps_completed_result(monkeypatch):
    flow = Mock()
    flow.poll = AsyncMock(return_value=DeviceLoginCompleted(credential_name="chatgpt-admin"))
    monkeypatch.setattr(endpoints, "_build_device_login_flow", lambda: flow)

    response = await endpoints.poll_chatgpt_device_login(
        request=None,
        fastapi_response=None,
        body=endpoints.ChatGPTDeviceLoginPollRequest(login_id="login-123"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response == {"success": True, "status": "complete", "credential_name": "chatgpt-admin"}
    flow.poll.assert_awaited_once_with(login_id="login-123", owner_id="admin-user")


@pytest.mark.asyncio
async def test_device_login_overwrite_requires_writable_admin_role():
    with pytest.raises(HTTPException) as exc_info:
        await endpoints.start_chatgpt_device_login(
            request=None,
            fastapi_response=None,
            body=endpoints.ChatGPTDeviceLoginStartRequest(
                credential_name="chatgpt-admin",
                overwrite_existing=True,
            ),
            user_api_key_dict=UserAPIKeyAuth(
                user_id="view-only-admin",
                user_role=LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY,
            ),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_chatgpt_device_login_rejects_private_api_base():
    with pytest.raises(HTTPException) as exc_info:
        await endpoints.start_chatgpt_device_login(
            request=None,
            fastapi_response=None,
            body=endpoints.ChatGPTDeviceLoginStartRequest(
                credential_name="chatgpt-admin",
                api_base="http://127.0.0.1:4000",
            ),
            user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
        )

    assert exc_info.value.status_code == 400


def test_device_login_requires_shared_state_for_multiple_workers(monkeypatch):
    monkeypatch.setenv("NUM_WORKERS", "2")
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", Mock())
    monkeypatch.setattr("litellm.proxy.proxy_server.redis_usage_cache", None)

    with pytest.raises(HTTPException) as exc_info:
        endpoints._build_device_login_flow()

    assert exc_info.value.status_code == 503


def test_device_login_requires_shared_state_by_default(monkeypatch):
    monkeypatch.delenv("NUM_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("LITELLM_ALLOW_IN_MEMORY_DEVICE_LOGIN", raising=False)
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", Mock())
    monkeypatch.setattr("litellm.proxy.proxy_server.redis_usage_cache", None)

    with pytest.raises(HTTPException) as exc_info:
        endpoints._build_device_login_flow()

    assert exc_info.value.status_code == 503


def test_device_login_allows_explicit_single_process_development_store(monkeypatch):
    monkeypatch.setenv("LITELLM_ALLOW_IN_MEMORY_DEVICE_LOGIN", "true")
    monkeypatch.setenv("NUM_WORKERS", "1")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", Mock())
    monkeypatch.setattr("litellm.proxy.proxy_server.redis_usage_cache", None)

    flow = endpoints._build_device_login_flow()

    assert flow is not None
