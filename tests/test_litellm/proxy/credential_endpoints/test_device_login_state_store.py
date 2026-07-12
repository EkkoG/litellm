import asyncio
from datetime import datetime, timezone
from typing import Optional

import pytest
from prisma.errors import RecordNotFoundError
from prisma.models import LiteLLM_DeviceLoginState

from litellm.proxy.credential_endpoints.device_login_flow import DeviceLoginState, ProviderChallenge
from litellm.proxy.credential_endpoints.device_login_state_store import (
    DatabaseDeviceLoginStateKey,
    DatabaseDeviceLoginStateStore,
    DatabaseDeviceLoginStateUpsert,
    DatabaseDeviceLoginStateWhere,
)


class _DatabaseTable:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.records: dict[str, LiteLLM_DeviceLoginState] = {}

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
                    created_at=now,
                    updated_at=now,
                )
            else:
                update = data["update"]
                record = current.model_copy(
                    update={
                        "encrypted_state": update.get("encrypted_state", current.encrypted_state),
                        "expires_at": update.get("expires_at", current.expires_at),
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

    async def delete(
        self,
        *,
        where: DatabaseDeviceLoginStateKey,
    ) -> LiteLLM_DeviceLoginState:
        async with self._lock:
            record = self.records.pop(where["login_id"], None)
            if record is None:
                raise RecordNotFoundError({}, message="device login state not found")
            return record

    async def delete_many(self, *, where: DatabaseDeviceLoginStateWhere) -> int:
        async with self._lock:
            login_id = where.get("login_id")
            if isinstance(login_id, str):
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


async def _claim_once(store: DatabaseDeviceLoginStateStore, login_id: str) -> Optional[DeviceLoginState]:
    async with store.claim(login_id) as state:
        return state


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
async def test_database_device_login_allows_only_one_concurrent_claim():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)
    await store.save(state)

    claims = await asyncio.gather(
        _claim_once(store, state.login_id),
        _claim_once(store, state.login_id),
    )

    assert sum(claim is not None for claim in claims) == 1
    assert next(claim for claim in claims if claim is not None) == state
    assert state.login_id not in table.records


@pytest.mark.asyncio
async def test_database_device_login_restores_state_after_regular_exception():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=1000.0)
    await store.save(state)

    with pytest.raises(RuntimeError, match="provider failed"):
        async with store.claim(state.login_id) as claimed_state:
            assert claimed_state == state
            raise RuntimeError("provider failed")

    assert await store.get(state.login_id) == state


@pytest.mark.asyncio
async def test_database_device_login_discards_expired_state():
    table = _DatabaseTable()
    store = DatabaseDeviceLoginStateStore(table, clock=lambda: 100.0)
    state = _state(expires_at=99.0)
    await store.save(state)

    async with store.claim(state.login_id) as claimed_state:
        assert claimed_state is None

    assert state.login_id not in table.records
