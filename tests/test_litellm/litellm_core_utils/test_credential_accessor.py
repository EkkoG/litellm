import asyncio
import time

import pytest

import litellm
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.llms.xai.oauth import XAIAuthRecord
from litellm.proxy.credential_endpoints.chatgpt_credential_utils import ChatGPTCredentialRefreshManager
from litellm.proxy.credential_endpoints.oauth_credential_refresh import OAuthCredentialRefreshManager
from litellm.proxy.credential_endpoints.xai_credential_utils import XAICredentialRefreshAdapter
from litellm.types.utils import CredentialItem


class _Authenticator:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started: set[str] = set()
        self.release = asyncio.Event()

    def is_access_token_expired(self, values: dict[str, object]) -> bool:
        return float(str(values["chatgpt_expires_at"])) <= time.time()

    async def async_refresh_tokens(self, refresh_token: str) -> dict[str, str]:
        self.calls.append(refresh_token)
        self.started.add(refresh_token)
        await self.release.wait()
        return {
            "access_token": f"access-{refresh_token}",
            "refresh_token": f"rotated-{refresh_token}",
            "id_token": "id-token",
        }

    def _build_auth_record(self, tokens: dict[str, str]) -> dict[str, object]:
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "id_token": tokens["id_token"],
            "expires_at": int(time.time()) + 3600,
            "account_id": None,
            "plan_type": None,
        }


class _XAIAuthenticator:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.release = asyncio.Event()

    async def async_refresh_auth_record(self, auth_record: XAIAuthRecord) -> XAIAuthRecord:
        self.calls.append(auth_record.refresh_token)
        await self.release.wait()
        return XAIAuthRecord(
            access_token=f"access-{auth_record.refresh_token}",
            refresh_token=f"rotated-{auth_record.refresh_token}",
            token_endpoint=auth_record.token_endpoint,
            expires_at=int(time.time()) + 3600,
        )


def _credential(name: str, refresh_token: str) -> CredentialItem:
    return CredentialItem(
        credential_name=name,
        credential_values={
            "api_key": f"old-{name}",
            "chatgpt_refresh_token": refresh_token,
            "chatgpt_id_token": "old-id",
            "chatgpt_expires_at": str(time.time() - 60),
        },
        credential_info={"custom_llm_provider": "chatgpt"},
    )


def _xai_credential(name: str, refresh_token: str) -> CredentialItem:
    return CredentialItem(
        credential_name=name,
        credential_values={
            "api_key": f"old-{name}",
            "xai_oauth_refresh_token": refresh_token,
            "xai_oauth_expires_at": str(time.time() - 60),
            "xai_oauth_token_endpoint": "https://auth.x.ai/oauth/token",
        },
        credential_info={"custom_llm_provider": "xai", "auth_type": "oauth_json_import"},
    )


@pytest.mark.asyncio
async def test_same_chatgpt_credential_refresh_is_single_flight() -> None:
    authenticator = _Authenticator()
    credential = _credential("chatgpt-admin", "refresh-a")
    runtime: dict[str, CredentialItem] = {credential.credential_name: credential}
    manager = ChatGPTCredentialRefreshManager(
        authenticator=authenticator,
        credential_getter=runtime.get,
        credential_upsert=lambda credentials: runtime.update({item.credential_name: item for item in credentials}),
    )

    first = asyncio.create_task(manager.aresolve(credential))
    second = asyncio.create_task(manager.aresolve(credential))
    await asyncio.sleep(0)
    authenticator.release.set()
    first_values, second_values = await asyncio.gather(first, second)

    assert authenticator.calls == ["refresh-a"]
    assert first_values == second_values
    assert first_values["api_key"] == "access-refresh-a"
    assert runtime["chatgpt-admin"].credential_values["chatgpt_refresh_token"] == "rotated-refresh-a"


@pytest.mark.asyncio
async def test_distinct_chatgpt_credentials_refresh_independently() -> None:
    authenticator = _Authenticator()
    first_credential = _credential("credential-a", "refresh-a")
    second_credential = _credential("credential-b", "refresh-b")
    runtime = {
        first_credential.credential_name: first_credential,
        second_credential.credential_name: second_credential,
    }
    manager = ChatGPTCredentialRefreshManager(
        authenticator=authenticator,
        credential_getter=runtime.get,
        credential_upsert=lambda credentials: runtime.update({item.credential_name: item for item in credentials}),
    )

    first = asyncio.create_task(manager.aresolve(first_credential))
    second = asyncio.create_task(manager.aresolve(second_credential))
    for _ in range(5):
        await asyncio.sleep(0)
        if authenticator.started == {"refresh-a", "refresh-b"}:
            break

    assert authenticator.started == {"refresh-a", "refresh-b"}
    authenticator.release.set()
    first_values, second_values = await asyncio.gather(first, second)

    assert first_values["api_key"] == "access-refresh-a"
    assert second_values["api_key"] == "access-refresh-b"
    assert runtime["credential-a"].credential_values["chatgpt_refresh_token"] == "rotated-refresh-a"
    assert runtime["credential-b"].credential_values["chatgpt_refresh_token"] == "rotated-refresh-b"


@pytest.mark.asyncio
async def test_same_xai_credential_refresh_is_single_flight() -> None:
    authenticator = _XAIAuthenticator()
    credential = _xai_credential("xai-admin", "refresh-xai")
    runtime: dict[str, CredentialItem] = {credential.credential_name: credential}
    manager = OAuthCredentialRefreshManager(
        adapter=XAICredentialRefreshAdapter(authenticator=authenticator),
        credential_getter=runtime.get,
        credential_upsert=lambda credentials: runtime.update({item.credential_name: item for item in credentials}),
    )

    first = asyncio.create_task(manager.aresolve(credential))
    second = asyncio.create_task(manager.aresolve(credential))
    await asyncio.sleep(0)
    authenticator.release.set()
    first_values, second_values = await asyncio.gather(first, second)

    assert authenticator.calls == ["refresh-xai"]
    assert first_values == second_values
    assert first_values["api_key"] == "access-refresh-xai"
    assert runtime["xai-admin"].credential_values["xai_oauth_refresh_token"] == "rotated-refresh-xai"


@pytest.mark.asyncio
async def test_credential_accessor_refreshes_xai_values(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = _xai_credential("xai-admin", "refresh-xai")
    litellm.credential_list = [credential]
    refreshed = {**credential.credential_values, "api_key": "new-xai-access"}

    async def refresh(credential: CredentialItem, user_id: str | None = None) -> dict[str, object]:
        return refreshed

    monkeypatch.setattr(
        "litellm.proxy.credential_endpoints.xai_credential_utils.async_refresh_xai_credential_if_needed",
        refresh,
    )

    assert await CredentialAccessor.get_credential_values_async("xai-admin") == refreshed


@pytest.mark.asyncio
async def test_force_refresh_reuses_access_token_updated_by_another_request() -> None:
    authenticator = _XAIAuthenticator()
    rejected = _xai_credential("xai-admin", "refresh-a")
    current = CredentialItem(
        credential_name="xai-admin",
        credential_values={**rejected.credential_values, "api_key": "already-refreshed"},
        credential_info=rejected.credential_info,
    )
    manager = OAuthCredentialRefreshManager(
        adapter=XAICredentialRefreshAdapter(authenticator=authenticator),
        credential_getter=lambda _: current,
    )

    values = await manager.aforce_refresh(rejected, rejected_access_token="old-xai-admin")

    assert values["api_key"] == "already-refreshed"
    assert authenticator.calls == []


@pytest.mark.asyncio
async def test_static_xai_credential_is_not_managed_oauth() -> None:
    litellm.credential_list = [
        CredentialItem(
            credential_name="xai-static",
            credential_values={"api_key": "static-key"},
            credential_info={"custom_llm_provider": "xai"},
        )
    ]

    assert await CredentialAccessor.get_credential_values_async("xai-static") == {"api_key": "static-key"}
    assert await CredentialAccessor.async_force_refresh_after_unauthorized("xai-static", "static-key") is None


def test_credential_accessor_keeps_distinct_values_isolated() -> None:
    litellm.credential_list = [
        CredentialItem(
            credential_name="credential-a",
            credential_values={"api_key": "access-a"},
            credential_info={"custom_llm_provider": "chatgpt"},
        ),
        CredentialItem(
            credential_name="credential-b",
            credential_values={"api_key": "access-b"},
            credential_info={"custom_llm_provider": "chatgpt"},
        ),
    ]

    assert CredentialAccessor.get_credential_values("credential-a") == {"api_key": "access-a"}
    assert CredentialAccessor.get_credential_values("credential-b") == {"api_key": "access-b"}
