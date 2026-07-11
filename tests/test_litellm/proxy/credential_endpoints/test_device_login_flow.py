import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from litellm.proxy.credential_endpoints.device_login_flow import (
    DeviceLoginCompleted,
    DeviceLoginFailed,
    DeviceLoginFlow,
    DeviceLoginPending,
    DeviceLoginStarted,
    InMemoryDeviceLoginStateStore,
    ProviderAuthorized,
    ProviderChallenge,
    ProviderPending,
)
from litellm.proxy.credential_endpoints.credential_writer import CredentialConflict, CredentialSaved
from litellm.types.utils import CredentialItem


class FakeProvider:
    def __init__(self) -> None:
        self.begin = AsyncMock(
            return_value=ProviderChallenge(
                provider="chatgpt",
                verification_url="https://example.com/device",
                user_code="ABCD-EFGH",
                interval_seconds=5,
                expires_at=time.time() + 900,
                payload={"device_auth_id": "device-123", "user_code": "ABCD-EFGH"},
            )
        )
        self.poll = AsyncMock(return_value=ProviderPending(retry_after_seconds=5))


@pytest.mark.asyncio
async def test_device_login_can_start_and_poll_from_different_flow_instances():
    shared_states = {}
    shared_locks = {}
    provider = FakeProvider()
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    writer.save = AsyncMock()
    first_flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=InMemoryDeviceLoginStateStore(shared_states, shared_locks),
        credential_writer=writer,
    )
    second_flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=InMemoryDeviceLoginStateStore(shared_states, shared_locks),
        credential_writer=writer,
    )

    started = await first_flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="actor-hash",
        overwrite_existing=False,
        api_base=None,
    )

    assert isinstance(started, DeviceLoginStarted)
    pending = await second_flow.poll(
        login_id=started.login_id,
        owner_id="actor-hash",
        ignore_poll_interval=True,
    )
    assert isinstance(pending, DeviceLoginPending)
    provider.poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_device_login_completes_through_credential_writer():
    provider = FakeProvider()
    credential = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "access-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    provider.poll.return_value = ProviderAuthorized(credential=credential)
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    writer.save = AsyncMock(return_value=Mock())
    flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=InMemoryDeviceLoginStateStore({}, {}),
        credential_writer=writer,
    )
    started = await flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="actor-hash",
        overwrite_existing=False,
        api_base=None,
    )
    assert isinstance(started, DeviceLoginStarted)

    completed = await flow.poll(
        login_id=started.login_id,
        owner_id="actor-hash",
        ignore_poll_interval=True,
    )

    assert isinstance(completed, DeviceLoginCompleted)
    writer.save.assert_awaited_once_with(
        credential=credential,
        actor_id="actor-hash",
        overwrite_existing=False,
    )


@pytest.mark.asyncio
async def test_device_login_rejects_a_different_owner_without_polling_provider():
    provider = FakeProvider()
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    store = InMemoryDeviceLoginStateStore({}, {})
    flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=store,
        credential_writer=writer,
    )
    started = await flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="owner-one",
        overwrite_existing=False,
        api_base=None,
    )
    assert isinstance(started, DeviceLoginStarted)

    result = await flow.poll(
        login_id=started.login_id,
        owner_id="owner-two",
        ignore_poll_interval=True,
    )

    assert isinstance(result, DeviceLoginFailed)
    assert result.status_code == 403
    provider.poll.assert_not_awaited()
    assert await store.get(started.login_id) is not None


@pytest.mark.asyncio
async def test_device_login_enforces_server_side_poll_interval():
    provider = FakeProvider()
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=InMemoryDeviceLoginStateStore({}, {}, clock=lambda: 100.0),
        credential_writer=writer,
        clock=lambda: 100.0,
    )
    started = await flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="actor-hash",
        overwrite_existing=False,
        api_base=None,
    )
    assert isinstance(started, DeviceLoginStarted)

    result = await flow.poll(login_id=started.login_id, owner_id="actor-hash")

    assert isinstance(result, DeviceLoginPending)
    assert result.retry_after_seconds == 5
    provider.poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_login_restores_claimed_state_when_poll_raises():
    provider = FakeProvider()
    provider.poll.side_effect = RuntimeError("provider unavailable")
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    store = InMemoryDeviceLoginStateStore({}, {})
    flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=store,
        credential_writer=writer,
    )
    started = await flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="actor-hash",
        overwrite_existing=False,
        api_base=None,
    )
    assert isinstance(started, DeviceLoginStarted)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await flow.poll(
            login_id=started.login_id,
            owner_id="actor-hash",
            ignore_poll_interval=True,
        )

    assert await store.get(started.login_id) is not None


@pytest.mark.asyncio
async def test_device_login_allows_only_one_concurrent_poll():
    provider = FakeProvider()
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    credential = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "access-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )

    async def delayed_poll(challenge, credential_name):
        poll_started.set()
        await release_poll.wait()
        return ProviderAuthorized(credential=credential)

    provider.poll.side_effect = delayed_poll
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    writer.save = AsyncMock(return_value=CredentialSaved(credential=credential, created=True))
    flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=InMemoryDeviceLoginStateStore({}, {}),
        credential_writer=writer,
    )
    started = await flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="actor-hash",
        overwrite_existing=False,
        api_base=None,
    )
    assert isinstance(started, DeviceLoginStarted)

    first_poll = asyncio.create_task(
        flow.poll(login_id=started.login_id, owner_id="actor-hash", ignore_poll_interval=True)
    )
    await poll_started.wait()
    second_result = await flow.poll(
        login_id=started.login_id,
        owner_id="actor-hash",
        ignore_poll_interval=True,
    )
    release_poll.set()
    first_result = await first_poll

    assert isinstance(first_result, DeviceLoginCompleted)
    assert isinstance(second_result, DeviceLoginFailed)
    writer.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_device_login_deletes_consumed_state_when_final_write_conflicts():
    provider = FakeProvider()
    credential = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "access-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    provider.poll.return_value = ProviderAuthorized(credential=credential)
    writer = Mock()
    writer.exists = AsyncMock(return_value=False)
    writer.save = AsyncMock(return_value=CredentialConflict(credential_name="managed-chatgpt"))
    store = InMemoryDeviceLoginStateStore({}, {})
    flow = DeviceLoginFlow(
        providers={"chatgpt": provider},
        state_store=store,
        credential_writer=writer,
    )
    started = await flow.start(
        provider="chatgpt",
        credential_name="managed-chatgpt",
        owner_id="actor-hash",
        overwrite_existing=False,
        api_base=None,
    )
    assert isinstance(started, DeviceLoginStarted)

    result = await flow.poll(
        login_id=started.login_id,
        owner_id="actor-hash",
        ignore_poll_interval=True,
    )

    assert isinstance(result, DeviceLoginFailed)
    assert result.status_code == 409
    assert await store.get(started.login_id) is None
