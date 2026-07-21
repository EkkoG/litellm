import json
import time
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from litellm.llms.xai.oauth import XAIAuthRecord
from litellm.proxy._types import LitellmUserRoles, ProxyException, UserAPIKeyAuth
from litellm.proxy.credential_endpoints import endpoints
from litellm.proxy.credential_endpoints.credential_writer import CredentialConflict, CredentialSaved


def _auth_json(expires_at: int | None = None) -> str:
    return json.dumps(
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
            "token_type": "Bearer",
            "token_endpoint": "https://auth.x.ai/oauth/token",
            "expires_at": expires_at or int(time.time()) + 3600,
        }
    )


@pytest.mark.asyncio
async def test_import_xai_oauth_credential_encrypts_through_writer(monkeypatch):
    writer = Mock()
    writer.save = AsyncMock(
        return_value=CredentialSaved(
            credential=Mock(),
            created=True,
        )
    )
    monkeypatch.setattr(endpoints, "CredentialWriter", lambda repository: writer)
    monkeypatch.setattr(endpoints, "CredentialsRepository", lambda prisma: Mock())
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", Mock())

    response = await endpoints.import_xai_oauth_credential(
        body=endpoints.XAIOAuthImportRequest(
            credential_name="xai-admin",
            auth_json=_auth_json(),
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_id="admin-user",
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    assert response.success is True
    saved = writer.save.call_args.kwargs["credential"]
    assert saved.credential_name == "xai-admin"
    assert saved.credential_values == {
        "api_key": "access-token",
        "xai_oauth_refresh_token": "refresh-token",
        "xai_oauth_expires_at": str(json.loads(_auth_json())["expires_at"]),
        "xai_oauth_token_endpoint": "https://auth.x.ai/oauth/token",
    }
    assert saved.credential_info == {
        "custom_llm_provider": "xai",
        "auth_type": "oauth_json_import",
    }
    assert "id-token" not in str(saved)
    writer.save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY,
        LitellmUserRoles.ORG_ADMIN,
        LitellmUserRoles.INTERNAL_USER,
    ],
)
async def test_import_xai_oauth_credential_requires_proxy_admin(role):
    with pytest.raises(HTTPException) as exc_info:
        await endpoints.import_xai_oauth_credential(
            body=endpoints.XAIOAuthImportRequest(
                credential_name="xai-admin",
                auth_json=_auth_json(),
            ),
            user_api_key_dict=UserAPIKeyAuth(user_id="user", user_role=role),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_import_xai_oauth_credential_refreshes_expired_record(monkeypatch):
    refreshed = XAIAuthRecord(
        access_token="new-access-token",
        refresh_token="rotated-refresh-token",
        token_endpoint="https://auth.x.ai/oauth/token",
        expires_at=int(time.time()) + 7200,
    )
    authenticator = Mock()
    authenticator.async_refresh_auth_record = AsyncMock(return_value=refreshed)
    writer = Mock()
    writer.save = AsyncMock(return_value=CredentialSaved(credential=Mock(), created=True))
    monkeypatch.setattr(endpoints, "XAIOAuthAuthenticator", lambda: authenticator)
    monkeypatch.setattr(endpoints, "CredentialWriter", lambda repository: writer)
    monkeypatch.setattr(endpoints, "CredentialsRepository", lambda prisma: Mock())
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", Mock())

    await endpoints.import_xai_oauth_credential(
        body=endpoints.XAIOAuthImportRequest(
            credential_name="xai-admin",
            auth_json=_auth_json(expires_at=int(time.time()) - 60),
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_id="admin-user",
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    authenticator.async_refresh_auth_record.assert_awaited_once()
    saved = writer.save.call_args.kwargs["credential"]
    assert saved.credential_values["api_key"] == "new-access-token"
    assert saved.credential_values["xai_oauth_refresh_token"] == "rotated-refresh-token"


@pytest.mark.asyncio
async def test_import_xai_oauth_credential_rejects_conflict(monkeypatch):
    writer = Mock()
    writer.save = AsyncMock(return_value=CredentialConflict(credential_name="xai-admin"))
    monkeypatch.setattr(endpoints, "CredentialWriter", lambda repository: writer)
    monkeypatch.setattr(endpoints, "CredentialsRepository", lambda prisma: Mock())
    monkeypatch.setattr("litellm.proxy.proxy_server.prisma_client", Mock())

    with pytest.raises(HTTPException) as exc_info:
        await endpoints.import_xai_oauth_credential(
            body=endpoints.XAIOAuthImportRequest(
                credential_name="xai-admin",
                auth_json=_auth_json(),
            ),
            user_api_key_dict=UserAPIKeyAuth(
                user_id="admin-user",
                user_role=LitellmUserRoles.PROXY_ADMIN,
            ),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_generic_credential_endpoint_rejects_managed_oauth_metadata():
    with pytest.raises(ProxyException) as exc_info:
        await endpoints.create_credential(
            request=None,
            fastapi_response=None,
            credential=endpoints.CreateCredentialItem(
                credential_name="xai-admin",
                credential_values={"api_key": "unvalidated-token"},
                credential_info={"custom_llm_provider": "xai", "auth_type": "oauth_json_import"},
            ),
            user_api_key_dict=UserAPIKeyAuth(
                user_id="admin-user",
                user_role=LitellmUserRoles.PROXY_ADMIN,
            ),
        )

    assert exc_info.value.code == "400"


@pytest.mark.asyncio
async def test_import_xai_oauth_credential_rejects_oversized_json():
    with pytest.raises(HTTPException) as exc_info:
        await endpoints.import_xai_oauth_credential(
            body=endpoints.XAIOAuthImportRequest(
                credential_name="xai-admin",
                auth_json="x" * (endpoints._XAI_AUTH_JSON_MAX_BYTES + 1),
            ),
            user_api_key_dict=UserAPIKeyAuth(
                user_id="admin-user",
                user_role=LitellmUserRoles.PROXY_ADMIN,
            ),
        )

    assert exc_info.value.status_code == 413
