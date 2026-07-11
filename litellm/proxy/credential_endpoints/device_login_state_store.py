import asyncio
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Callable, Optional, Protocol

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


_LOCAL_DEVICE_LOGIN_STATES: dict[str, DeviceLoginState] = {}
_LOCAL_DEVICE_LOGIN_LOCKS: dict[str, asyncio.Lock] = {}
