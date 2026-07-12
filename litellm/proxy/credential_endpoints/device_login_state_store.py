import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol, TypedDict, runtime_checkable

from prisma.models import LiteLLM_DeviceLoginState
from pydantic import TypeAdapter

from litellm.caching.redis_cache import RedisCache
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper, encrypt_value_helper
from litellm.proxy.credential_endpoints.device_login_flow import DeviceLoginState

DEVICE_LOGIN_CACHE_PREFIX = "device_login"
DEVICE_LOGIN_CLAIM_TTL_SECONDS = 30
DEVICE_LOGIN_CLAIM_RENEWAL_SECONDS = 10
_DEVICE_LOGIN_STATE_ADAPTER = TypeAdapter(DeviceLoginState)


class RedisDeviceLoginCache(Protocol):
    async def async_set_cache(self, key: str, value: str, **kwargs: object) -> object: ...

    async def async_get_cache(self, key: str) -> object: ...

    async def async_delete_cache(self, key: str) -> object: ...


class DeviceLoginLockManager(Protocol):
    async def acquire_lock(self, cronjob_id: str, ttl: Optional[int] = None) -> Optional[bool]: ...

    async def release_lock(self, cronjob_id: str) -> object: ...


class DeviceLoginClaimOwner(Protocol):
    def cancel(self) -> bool: ...

    def cancelling(self) -> int: ...


@dataclass(frozen=True, slots=True)
class DatabaseDeviceLoginClaim:
    token: str
    owner: DeviceLoginClaimOwner
    closing: asyncio.Event


class DatabaseDeviceLoginStateKey(TypedDict):
    login_id: str


class DatabaseDeviceLoginStateCreate(TypedDict):
    login_id: str
    encrypted_state: str
    expires_at: datetime


class DatabaseDeviceLoginStateUpdate(TypedDict, total=False):
    encrypted_state: str
    expires_at: datetime
    claim_token: Optional[str]
    claimed_until: Optional[datetime]


class DatabaseDeviceLoginStateUpsert(TypedDict):
    create: DatabaseDeviceLoginStateCreate
    update: DatabaseDeviceLoginStateUpdate


class DatabaseDeviceLoginStateMutation(TypedDict, total=False):
    encrypted_state: str
    expires_at: datetime
    claim_token: Optional[str]
    claimed_until: Optional[datetime]


class DatabaseDeviceLoginStateDateTimeFilter(TypedDict, total=False):
    gt: datetime
    lte: datetime


class DatabaseDeviceLoginStateLeaseFilter(TypedDict, total=False):
    claimed_until: Optional[DatabaseDeviceLoginStateDateTimeFilter]


class DatabaseDeviceLoginStateWhere(TypedDict, total=False):
    login_id: str
    expires_at: DatabaseDeviceLoginStateDateTimeFilter
    claim_token: str
    OR: list[DatabaseDeviceLoginStateLeaseFilter]


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

    async def update_many(
        self,
        *,
        data: DatabaseDeviceLoginStateMutation,
        where: DatabaseDeviceLoginStateWhere,
    ) -> int: ...

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
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._table = table
        self._clock = clock
        self._sleeper = sleeper
        self._claim: ContextVar[Optional[DatabaseDeviceLoginClaim]] = ContextVar("device_login_claim", default=None)

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
        claim = self._claim.get()
        if claim is not None:
            updated = await self._table.update_many(
                data={"encrypted_state": encrypted, "expires_at": expires_at},
                where={"login_id": state.login_id, "claim_token": claim.token},
            )
            if updated != 1:
                claim.owner.cancel()
            return
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
            "claim_token": None,
            "claimed_until": None,
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
        claim = self._claim.get()
        where: DatabaseDeviceLoginStateWhere = {"login_id": login_id}
        if claim is None:
            await self._table.delete_many(where=where)
            return
        claim.closing.set()
        where["claim_token"] = claim.token
        deleted = await self._table.delete_many(where=where)
        if deleted != 1:
            claim.owner.cancel()

    async def _renew_claim(self, login_id: str, claim: DatabaseDeviceLoginClaim) -> None:
        while True:
            await self._sleeper(DEVICE_LOGIN_CLAIM_RENEWAL_SECONDS)
            now = datetime.fromtimestamp(self._clock(), tz=timezone.utc)
            try:
                renewed = await self._table.update_many(
                    data={"claimed_until": now + timedelta(seconds=DEVICE_LOGIN_CLAIM_TTL_SECONDS)},
                    where={"login_id": login_id, "claim_token": claim.token},
                )
            except Exception:
                if not claim.closing.is_set():
                    claim.owner.cancel()
                return
            if renewed != 1:
                if not claim.closing.is_set():
                    claim.owner.cancel()
                return

    @staticmethod
    async def _stop_renewal(renewal_task: asyncio.Task[None], owner: DeviceLoginClaimOwner) -> None:
        renewal_task.cancel()
        try:
            await renewal_task
        except asyncio.CancelledError:
            if owner.cancelling() > 0:
                raise
        if owner.cancelling() > 0:
            raise asyncio.CancelledError

    @asynccontextmanager
    async def claim(self, login_id: str) -> AsyncGenerator[Optional[DeviceLoginState], None]:
        now = datetime.fromtimestamp(self._clock(), tz=timezone.utc)
        claim_token = str(uuid.uuid4())
        claimed = await self._table.update_many(
            data={
                "claim_token": claim_token,
                "claimed_until": now + timedelta(seconds=DEVICE_LOGIN_CLAIM_TTL_SECONDS),
            },
            where={
                "login_id": login_id,
                "expires_at": {"gt": now},
                "OR": [
                    {"claimed_until": None},
                    {"claimed_until": {"lte": now}},
                ],
            },
        )
        if claimed != 1:
            yield None
            return
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("Device login claim requires an asyncio task")
        claim = DatabaseDeviceLoginClaim(token=claim_token, owner=owner, closing=asyncio.Event())
        claim_context = self._claim.set(claim)
        renewal_task = asyncio.create_task(self._renew_claim(login_id, claim))
        try:
            record = await self._table.find_unique(where={"login_id": login_id})
            state = self._decode(record)
            yield state
        finally:
            claim.closing.set()
            try:
                await self._stop_renewal(renewal_task, owner)
            finally:
                self._claim.reset(claim_context)
                await self._table.update_many(
                    data={"claim_token": None, "claimed_until": None},
                    where={"login_id": login_id, "claim_token": claim_token},
                )
