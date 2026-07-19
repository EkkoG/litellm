import asyncio
import time

import pytest

import litellm
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.proxy.credential_endpoints.chatgpt_credential_utils import ChatGPTCredentialRefreshManager
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
