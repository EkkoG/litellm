import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol, TypedDict, runtime_checkable

from prisma.errors import RecordNotFoundError
from prisma.models import LiteLLM_DeviceLoginState
from pydantic import TypeAdapter

from litellm.caching.redis_cache import RedisCache
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper, encrypt_value_helper
from litellm.proxy.credential_endpoints.device_login_flow import DeviceLoginState

DEVICE_LOGIN_CACHE_PREFIX = "device_login"
_DEVICE_LOGIN_STATE_ADAPTER = TypeAdapter(DeviceLoginState)


class RedisDeviceLoginCache(Protocol):
    async def async_set_cache(self, key: str, value: str, **kwargs: object) -> object: ...

    async def async_get_cache(self, key: str) -> object: ...

    async def async_delete_cache(self, key: str) -> object: ...


class DeviceLoginLockManager(Protocol):
    async def acquire_lock(self, cronjob_id: str, ttl: Optional[int] = None) -> Optional[bool]: ...

    async def release_lock(self, cronjob_id: str) -> object: ...


class DatabaseDeviceLoginStateKey(TypedDict):
    login_id: str


class DatabaseDeviceLoginStateCreate(TypedDict):
    login_id: str
    encrypted_state: str
    expires_at: datetime


class DatabaseDeviceLoginStateUpdate(TypedDict, total=False):
    encrypted_state: str
    expires_at: datetime


class DatabaseDeviceLoginStateUpsert(TypedDict):
    create: DatabaseDeviceLoginStateCreate
    update: DatabaseDeviceLoginStateUpdate


class DatabaseDeviceLoginStateDateTimeFilter(TypedDict, total=False):
    lte: datetime


class DatabaseDeviceLoginStateWhere(TypedDict, total=False):
    login_id: str
    expires_at: DatabaseDeviceLoginStateDateTimeFilter


@runtime_checkable
class DatabaseDeviceLoginStateTable(Protocol):
    async def upsert(
        self,
        *,
        where: DatabaseDeviceLoginStateKey,
        data: DatabaseDeviceLoginStateUpsert,
    ) -> LiteLLM_DeviceLoginState: ...

    async def find_unique(
        self,
        *,
        where: DatabaseDeviceLoginStateKey,
    ) -> Optional[LiteLLM_DeviceLoginState]: ...

    async def delete(
        self,
        *,
        where: DatabaseDeviceLoginStateKey,
    ) -> LiteLLM_DeviceLoginState: ...

    async def delete_many(self, *, where: DatabaseDeviceLoginStateWhere) -> int: ...


def _build_device_login_lock_manager(redis_cache: RedisDeviceLoginCache) -> DeviceLoginLockManager:
    from litellm.proxy.db.db_transaction_queue.pod_lock_manager import PodLockManager

    if not isinstance(redis_cache, RedisCache):
        raise TypeError("Redis device login claims require the LiteLLM RedisCache implementation")
    return PodLockManager(redis_cache=redis_cache)


class RedisDeviceLoginStateStore:
    def __init__(
        self,
        redis_cache: RedisDeviceLoginCache,
        lock_manager_factory: Callable[[RedisDeviceLoginCache], DeviceLoginLockManager] = (
            _build_device_login_lock_manager
        ),
    ) -> None:
        self._redis_cache = redis_cache
        self._lock_manager_factory = lock_manager_factory

    @staticmethod
    def _key(login_id: str) -> str:
        return f"{DEVICE_LOGIN_CACHE_PREFIX}:{login_id}"

    async def save(self, state: DeviceLoginState) -> None:
        ttl = max(1, int(state.challenge.expires_at - time.time()))
        serialized = json.dumps(asdict(state))
        encrypted = encrypt_value_helper(serialized)
        await self._redis_cache.async_set_cache(self._key(state.login_id), encrypted, ttl=ttl)

    async def get(self, login_id: str) -> Optional[DeviceLoginState]:
        encrypted = await self._redis_cache.async_get_cache(self._key(login_id))
        if not isinstance(encrypted, str):
            return None
        decrypted = decrypt_value_helper(encrypted, key="device_login_state", exception_type="debug")
        if not decrypted:
            return None
        state = _DEVICE_LOGIN_STATE_ADAPTER.validate_json(decrypted)
        if state.challenge.expires_at <= time.time():
            await self.delete(login_id)
            return None
        return state

    async def delete(self, login_id: str) -> None:
        await self._redis_cache.async_delete_cache(self._key(login_id))

    @asynccontextmanager
    async def claim(self, login_id: str) -> AsyncGenerator[Optional[DeviceLoginState], None]:
        lock_manager = self._lock_manager_factory(self._redis_cache)
        lock_id = f"device_login:{login_id}"
        acquired = await lock_manager.acquire_lock(lock_id, ttl=30)
        if not acquired:
            yield None
            return
        state = await self.get(login_id)
        if state is not None:
            await self.delete(login_id)
        try:
            yield state
        except Exception:
            if state is not None:
                await self.save(state)
            raise
        finally:
            await lock_manager.release_lock(lock_id)


class DatabaseDeviceLoginStateStore:
    def __init__(
        self,
        table: DatabaseDeviceLoginStateTable,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._table = table
        self._clock = clock

    @staticmethod
    def _decode(record: Optional[LiteLLM_DeviceLoginState]) -> Optional[DeviceLoginState]:
        encrypted = getattr(record, "encrypted_state", None)
        if not isinstance(encrypted, str):
            return None
        decrypted = decrypt_value_helper(encrypted, key="device_login_state", exception_type="debug")
        if not decrypted:
            return None
        return _DEVICE_LOGIN_STATE_ADAPTER.validate_json(decrypted)

    async def save(self, state: DeviceLoginState) -> None:
        encrypted = encrypt_value_helper(json.dumps(asdict(state)))
        expires_at = datetime.fromtimestamp(state.challenge.expires_at, tz=timezone.utc)
        await self._table.delete_many(
            where={"expires_at": {"lte": datetime.fromtimestamp(self._clock(), tz=timezone.utc)}}
        )
        create: DatabaseDeviceLoginStateCreate = {
            "login_id": state.login_id,
            "encrypted_state": encrypted,
            "expires_at": expires_at,
        }
        update: DatabaseDeviceLoginStateUpdate = {
            "encrypted_state": encrypted,
            "expires_at": expires_at,
        }
        await self._table.upsert(
            where={"login_id": state.login_id},
            data={"create": create, "update": update},
        )

    async def get(self, login_id: str) -> Optional[DeviceLoginState]:
        record = await self._table.find_unique(where={"login_id": login_id})
        state = self._decode(record)
        if state is None:
            return None
        if state.challenge.expires_at <= self._clock():
            await self.delete(login_id)
            return None
        return state

    async def delete(self, login_id: str) -> None:
        await self._table.delete_many(where={"login_id": login_id})

    @asynccontextmanager
    async def claim(self, login_id: str) -> AsyncGenerator[Optional[DeviceLoginState], None]:
        try:
            record = await self._table.delete(where={"login_id": login_id})
        except RecordNotFoundError:
            yield None
            return
        state = self._decode(record)
        if state is None or state.challenge.expires_at <= self._clock():
            yield None
            return
        try:
            yield state
        except Exception:
            await self.save(state)
            raise
