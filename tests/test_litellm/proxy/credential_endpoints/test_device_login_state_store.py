import asyncio
from datetime import datetime, timezone
from typing import Optional

import pytest
from prisma.models import LiteLLM_DeviceLoginState

from litellm.proxy.credential_endpoints.device_login_flow import DeviceLoginState, ProviderChallenge
from litellm.proxy.credential_endpoints.device_login_state_store import (
    DatabaseDeviceLoginStateKey,
    DatabaseDeviceLoginStateMutation,
    DatabaseDeviceLoginStateStore,
    DatabaseDeviceLoginStateUpsert,
    DatabaseDeviceLoginStateWhere,
)


class _DatabaseTable:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.records: dict[str, LiteLLM_DeviceLoginState] = {}
        self.lease_renewed = asyncio.Event()
        self.block_renewal = False
        self.renewal_started = asyncio.Event()
        self.allow_renewal = asyncio.Event()

    async def upsert(
        self,
        *,
        where: DatabaseDeviceLoginStateKey,
        data: DatabaseDeviceLoginStateUpsert,
    ) -> LiteLLM_DeviceLoginState:
        async with self._lock:
            current = self.records.get(where["login_id"])
            if current is None:
                create = data["create"]
                now = datetime.now(timezone.utc)
                record = LiteLLM_DeviceLoginState(
                    login_id=create["login_id"],
                    encrypted_state=create["encrypted_state"],
                    expires_at=create["expires_at"],
                    claim_token=None,
                    claimed_until=None,
                    created_at=now,
                    updated_at=now,
                )
            else:
                update = data["update"]
                record = current.model_copy(
                    update={
                        "encrypted_state": update.get("encrypted_state", current.encrypted_state),
                        "expires_at": update.get("expires_at", current.expires_at),
                        "claim_token": update.get("claim_token", current.claim_token),
                        "claimed_until": update.get("claimed_until", current.claimed_until),
                    }
                )
            self.records[record.login_id] = record
            return record

    async def find_unique(
        self,
        *,
        where: DatabaseDeviceLoginStateKey,
    ) -> Optional[LiteLLM_DeviceLoginState]:
        async with self._lock:
            return self.records.get(where["login_id"])

    async def update_many(
        self,
        *,
        data: DatabaseDeviceLoginStateMutation,
        where: DatabaseDeviceLoginStateWhere,
    ) -> int:
        login_id = where.get("login_id")
        if not isinstance(login_id, str):
            return 0
        expected_token = where.get("claim_token")
        claimed_until = data.get("claimed_until")
        is_renewal = (
            isinstance(expected_token, str)
            and isinstance(claimed_until, datetime)
            and "claim_token" not in data
            and "encrypted_state" not in data
        )
        if self.block_renewal and is_renewal:
            self.renewal_started.set()
            await self.allow_renewal.wait()
        async with self._lock:
            current = self.records.get(login_id)
            if current is None:
                return 0
            if isinstance(expected_token, str) and current.claim_token != expected_token:
                return 0
            claim_token = data.get("claim_token")
            encrypted_state = data.get("encrypted_state")
            expires_at = data.get("expires_at")
            if isinstance(encrypted_state, str) and isinstance(expires_at, datetime):
                self.records[login_id] = current.model_copy(
                    update={
                        "encrypted_state": encrypted_state,
                        "expires_at": expires_at,
                        "claim_token": claim_token if "claim_token" in data else current.claim_token,
                        "claimed_until": claimed_until if "claimed_until" in data else current.claimed_until,
                    }
                )
                return 1
            if (
                not isinstance(expected_token, str)
                and isinstance(claim_token, str)
                and isinstance(claimed_until, datetime)
            ):
                claimed_at = claimed_until.timestamp() - 30
                if current.expires_at.timestamp() <= claimed_at:
                    return 0
                if current.claimed_until is not None and current.claimed_until.timestamp() > claimed_at:
                    return 0
                self.records[login_id] = current.model_copy(
                    update={"claim_token": claim_token, "claimed_until": claimed_until}
                )
                return 1
            if isinstance(expected_token, str) and isinstance(claimed_until, datetime):
                self.records[login_id] = current.model_copy(update={"claimed_until": claimed_until})
                self.lease_renewed.set()
                return 1
            if isinstance(expected_token, str) and "claim_token" in data and claim_token is None:
                self.records[login_id] = current.model_copy(update={"claim_token": None, "claimed_until": None})
                return 1
            return 0

    async def delete_many(self, *, where: DatabaseDeviceLoginStateWhere) -> int:
        async with self._lock:
            login_id = where.get("login_id")
            if isinstance(login_id, str):
                expected_token = where.get("claim_token")
                current = self.records.get(login_id)
                if isinstance(expected_token, str) and (current is None or current.claim_token != expected_token):
                    return 0
                return 1 if self.records.pop(login_id, None) is not None else 0
            expires_at = where.get("expires_at")
            if not isinstance(expires_at, dict):
                return 0
            cutoff = expires_at.get("lte")
            if not isinstance(cutoff, datetime):
                return 0
            expired_ids = tuple(record_id for record_id, record in self.records.items() if record.expires_at <= cutoff)
            for record_id in expired_ids:
                self.records.pop(record_id)
            return len(expired_ids)


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _ControlledSleeper:
    def __init__(self) -> None:
        self._gates: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    async def __call__(self, delay: float) -> None:
        gate = asyncio.Event()
        await self._gates.put(gate)
        await gate.wait()

    async def release_next(self) -> None:
        gate = await self._gates.get()
        gate.set()


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.proxy.proxy_server.master_key", "test-master-key")


def _state(*, expires_at: float, login_id: str = "login-123") -> DeviceLoginState:
    return DeviceLoginState(
        login_id=login_id,
        owner_id="owner-123",
        credential_name="managed-chatgpt",
        overwrite_existing=False,
        challenge=ProviderChallenge(
            provider="chatgpt",
            verification_url="https://example.com/device",
            user_code="ABCD-EFGH",
            interval_seconds=5,
            expires_at=expires_at,
            payload={"device_auth_id": "secret-device-auth-id"},
        ),
        next_poll_at=100.0,
    )


@pytest.mark.asyncio
async def test_database_device_login_state_is_shared_and_encrypted():
    table = _DatabaseTable()
    first_store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    second_store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)

    await first_store.save(state)

    assert "secret-device-auth-id" not in table.records[state.login_id].encrypted_state
    assert await second_store.get(state.login_id) == state


@pytest.mark.asyncio
async def test_database_device_login_allows_only_one_concurrent_claim_and_keeps_state_durable():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)
    await store.save(state)
    first_claim_started = asyncio.Event()
    release_first_claim = asyncio.Event()

    async def hold_first_claim() -> Optional[DeviceLoginState]:
        async with store.claim(state.login_id) as claimed_state:
            first_claim_started.set()
            await release_first_claim.wait()
            return claimed_state

    first_claim_task = asyncio.create_task(hold_first_claim())
    await first_claim_started.wait()

    async with store.claim(state.login_id) as second_claim:
        assert second_claim is None
        assert table.records[state.login_id].encrypted_state

    release_first_claim.set()
    assert await first_claim_task == state
    assert table.records[state.login_id].claim_token is None
    assert table.records[state.login_id].claimed_until is None


@pytest.mark.asyncio
async def test_database_device_login_renews_active_claim_before_lease_expires():
    table = _DatabaseTable()
    clock = _Clock(100.0)
    sleeper = _ControlledSleeper()
    store = DatabaseDeviceLoginStateStore(table, clock=clock, sleeper=sleeper)
    state = _state(expires_at=1000.0)
    await store.save(state)
    first_claim_started = asyncio.Event()
    release_first_claim = asyncio.Event()

    async def hold_first_claim() -> None:
        async with store.claim(state.login_id) as claimed_state:
            assert claimed_state == state
            first_claim_started.set()
            await release_first_claim.wait()

    first_claim_task = asyncio.create_task(hold_first_claim())
    await first_claim_started.wait()
    clock.now = 125.0
    await sleeper.release_next()
    await table.lease_renewed.wait()
    clock.now = 131.0

    async with store.claim(state.login_id) as second_claim:
        assert second_claim is None

    release_first_claim.set()
    await first_claim_task


@pytest.mark.asyncio
async def test_database_device_login_fences_stale_claim_writes():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0, sleeper=_ControlledSleeper())
    state = _state(expires_at=1000.0)
    await store.save(state)

    original_encrypted_state = table.records[state.login_id].encrypted_state

    async def stale_save() -> None:
        async with store.claim(state.login_id) as claimed_state:
            assert claimed_state == state
            table.records[state.login_id] = table.records[state.login_id].model_copy(
                update={
                    "claim_token": "new-claim",
                    "claimed_until": datetime.fromtimestamp(200.0, tz=timezone.utc),
                }
            )
            await store.save(state)

    with pytest.raises(asyncio.CancelledError):
        await stale_save()

    assert table.records[state.login_id].claim_token == "new-claim"
    assert table.records[state.login_id].encrypted_state == original_encrypted_state


@pytest.mark.asyncio
async def test_database_device_login_fences_stale_claim_deletes():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0, sleeper=_ControlledSleeper())
    state = _state(expires_at=1000.0)
    await store.save(state)

    async def stale_delete() -> None:
        async with store.claim(state.login_id) as claimed_state:
            assert claimed_state == state
            table.records[state.login_id] = table.records[state.login_id].model_copy(
                update={
                    "claim_token": "new-claim",
                    "claimed_until": datetime.fromtimestamp(200.0, tz=timezone.utc),
                }
            )
            await store.delete(state.login_id)

    with pytest.raises(asyncio.CancelledError):
        await stale_delete()

    assert table.records[state.login_id].claim_token == "new-claim"


@pytest.mark.asyncio
async def test_database_device_login_save_does_not_race_with_heartbeat():
    table = _DatabaseTable()
    table.block_renewal = True
    sleeper = _ControlledSleeper()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0, sleeper=sleeper)
    state = _state(expires_at=1000.0)
    await store.save(state)

    async with store.claim(state.login_id) as claimed_state:
        assert claimed_state == state
        await sleeper.release_next()
        await table.renewal_started.wait()
        await store.save(state)
        assert table.records[state.login_id].claim_token is not None
        table.allow_renewal.set()
        await table.lease_renewed.wait()


@pytest.mark.asyncio
async def test_database_device_login_delete_does_not_get_cancelled_by_inflight_heartbeat():
    table = _DatabaseTable()
    table.block_renewal = True
    sleeper = _ControlledSleeper()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0, sleeper=sleeper)
    state = _state(expires_at=1000.0)
    await store.save(state)

    async with store.claim(state.login_id) as claimed_state:
        assert claimed_state == state
        await sleeper.release_next()
        await table.renewal_started.wait()
        await store.delete(state.login_id)
        table.allow_renewal.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state.login_id not in table.records


@pytest.mark.asyncio
async def test_database_device_login_heartbeat_lease_loss_cancels_owner():
    table = _DatabaseTable()
    sleeper = _ControlledSleeper()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0, sleeper=sleeper)
    state = _state(expires_at=1000.0)
    await store.save(state)
    claim_started = asyncio.Event()

    async def hold_claim() -> None:
        async with store.claim(state.login_id) as claimed_state:
            assert claimed_state == state
            claim_started.set()
            await asyncio.Event().wait()

    claim_task = asyncio.create_task(hold_claim())
    await claim_started.wait()
    table.records[state.login_id] = table.records[state.login_id].model_copy(update={"claim_token": "new-claim"})
    await sleeper.release_next()

    with pytest.raises(asyncio.CancelledError):
        await claim_task


@pytest.mark.asyncio
async def test_database_device_login_releases_claim_when_poll_is_cancelled():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)
    await store.save(state)
    claim_started = asyncio.Event()

    async def wait_while_claimed() -> None:
        async with store.claim(state.login_id) as claimed_state:
            assert claimed_state == state
            claim_started.set()
            await asyncio.Event().wait()

    claim_task = asyncio.create_task(wait_while_claimed())
    await claim_started.wait()
    claim_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await claim_task

    async with store.claim(state.login_id) as reclaimed_state:
        assert reclaimed_state == state


@pytest.mark.asyncio
async def test_database_device_login_reclaims_expired_lease():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)
    await store.save(state)
    table.records[state.login_id] = table.records[state.login_id].model_copy(
        update={
            "claim_token": "abandoned-claim",
            "claimed_until": datetime.fromtimestamp(99.0, tz=timezone.utc),
        }
    )

    async with store.claim(state.login_id) as reclaimed_state:
        assert reclaimed_state == state


@pytest.mark.asyncio
async def test_database_device_login_save_clears_existing_lease():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)
    await store.save(state)
    table.records[state.login_id] = table.records[state.login_id].model_copy(
        update={
            "claim_token": "active-claim",
            "claimed_until": datetime.fromtimestamp(130.0, tz=timezone.utc),
        }
    )

    await store.save(state)

    assert table.records[state.login_id].claim_token is None
    assert table.records[state.login_id].claimed_until is None


@pytest.mark.asyncio
async def test_database_device_login_discards_expired_state():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=99.0)
    await store.save(state)

    assert await store.get(state.login_id) is None
    assert state.login_id not in table.records


@pytest.mark.asyncio
async def test_database_device_login_save_removes_other_expired_rows():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    expired_state = _state(expires_at=99.0, login_id="expired-login")
    current_state = _state(expires_at=200.0, login_id="current-login")
    await store.save(expired_state)

    await store.save(current_state)

    assert tuple(table.records) == (current_state.login_id,)
