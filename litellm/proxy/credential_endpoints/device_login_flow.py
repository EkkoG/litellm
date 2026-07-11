import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import AsyncContextManager, Callable, Literal, Optional, Protocol, Union

from litellm.proxy.credential_endpoints.credential_writer import CredentialConflict, CredentialWriter
from litellm.types.utils import CredentialItem

DeviceLoginProvider = Literal["chatgpt", "github_copilot"]


@dataclass(frozen=True, slots=True)
class ProviderChallenge:
    provider: DeviceLoginProvider
    verification_url: str
    user_code: str
    interval_seconds: int
    expires_at: float
    payload: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProviderPending:
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class ProviderAuthorized:
    credential: CredentialItem


@dataclass(frozen=True, slots=True)
class ProviderFailed:
    status_code: int
    detail: str


ProviderStartResult = Union[ProviderChallenge, ProviderFailed]
ProviderPollResult = Union[ProviderPending, ProviderAuthorized, ProviderFailed]


class DeviceAuthorizationProvider(Protocol):
    async def begin(self, api_base: Optional[str]) -> ProviderStartResult: ...

    async def poll(self, challenge: ProviderChallenge, credential_name: str) -> ProviderPollResult: ...


@dataclass(frozen=True, slots=True)
class DeviceLoginState:
    login_id: str
    owner_id: str
    credential_name: str
    overwrite_existing: bool
    challenge: ProviderChallenge
    next_poll_at: float


class DeviceLoginStateStore(Protocol):
    async def save(self, state: DeviceLoginState) -> None: ...

    async def get(self, login_id: str) -> Optional[DeviceLoginState]: ...

    async def delete(self, login_id: str) -> None: ...

    def claim(self, login_id: str) -> AsyncContextManager[Optional[DeviceLoginState]]: ...


class InMemoryDeviceLoginStateStore:
    def __init__(
        self,
        states: dict[str, DeviceLoginState],
        locks: dict[str, asyncio.Lock],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._states = states
        self._locks = locks
        self._clock = clock

    async def save(self, state: DeviceLoginState) -> None:
        self._states[state.login_id] = state

    async def get(self, login_id: str) -> Optional[DeviceLoginState]:
        state = self._states.get(login_id)
        if state is not None and state.challenge.expires_at <= self._clock():
            await self.delete(login_id)
            return None
        return state

    async def delete(self, login_id: str) -> None:
        self._states.pop(login_id, None)

    @asynccontextmanager
    async def claim(self, login_id: str) -> AsyncGenerator[Optional[DeviceLoginState], None]:
        lock = self._locks.setdefault(login_id, asyncio.Lock())
        if lock.locked():
            yield None
            return
        await lock.acquire()
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
            lock.release()
            if login_id not in self._states:
                self._locks.pop(login_id, None)


@dataclass(frozen=True, slots=True)
class DeviceLoginStarted:
    login_id: str
    verification_url: str
    user_code: str
    interval_seconds: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class DeviceLoginPending:
    retry_after_seconds: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class DeviceLoginCompleted:
    credential_name: str


@dataclass(frozen=True, slots=True)
class DeviceLoginFailed:
    status_code: int
    detail: str


DeviceLoginStartResult = Union[DeviceLoginStarted, DeviceLoginFailed]
DeviceLoginPollResult = Union[DeviceLoginPending, DeviceLoginCompleted, DeviceLoginFailed]


class DeviceLoginFlow:
    def __init__(
        self,
        providers: Mapping[DeviceLoginProvider, DeviceAuthorizationProvider],
        state_store: DeviceLoginStateStore,
        credential_writer: CredentialWriter,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._providers = providers
        self._state_store = state_store
        self._credential_writer = credential_writer
        self._clock = clock

    async def start(
        self,
        provider: DeviceLoginProvider,
        credential_name: str,
        owner_id: str,
        overwrite_existing: bool,
        api_base: Optional[str],
    ) -> DeviceLoginStartResult:
        if not overwrite_existing and await self._credential_writer.exists(credential_name):
            return DeviceLoginFailed(status_code=409, detail="Credential already exists")
        provider_adapter = self._providers[provider]
        challenge = await provider_adapter.begin(api_base)
        if isinstance(challenge, ProviderFailed):
            return DeviceLoginFailed(status_code=challenge.status_code, detail=challenge.detail)
        login_id = str(uuid.uuid4())
        await self._state_store.save(
            DeviceLoginState(
                login_id=login_id,
                owner_id=owner_id,
                credential_name=credential_name,
                overwrite_existing=overwrite_existing,
                challenge=challenge,
                next_poll_at=self._clock() + challenge.interval_seconds,
            )
        )
        return DeviceLoginStarted(
            login_id=login_id,
            verification_url=challenge.verification_url,
            user_code=challenge.user_code,
            interval_seconds=challenge.interval_seconds,
            expires_at=challenge.expires_at,
        )

    async def poll(
        self,
        login_id: str,
        owner_id: str,
        ignore_poll_interval: bool = False,
    ) -> DeviceLoginPollResult:
        async with self._state_store.claim(login_id) as state:
            if state is None:
                return DeviceLoginFailed(status_code=404, detail="Device login not found, busy, or expired")
            if state.owner_id != owner_id:
                await self._state_store.save(state)
                return DeviceLoginFailed(status_code=403, detail="Device login belongs to another user")
            now = self._clock()
            if not ignore_poll_interval and now < state.next_poll_at:
                await self._state_store.save(state)
                return DeviceLoginPending(
                    retry_after_seconds=max(1, int(state.next_poll_at - now)),
                    expires_at=state.challenge.expires_at,
                )
            provider = self._providers[state.challenge.provider]
            provider_result = await provider.poll(state.challenge, state.credential_name)
            if isinstance(provider_result, ProviderPending):
                await self._state_store.save(
                    replace(
                        state,
                        next_poll_at=now + provider_result.retry_after_seconds,
                    )
                )
                return DeviceLoginPending(
                    retry_after_seconds=provider_result.retry_after_seconds,
                    expires_at=state.challenge.expires_at,
                )
            if isinstance(provider_result, ProviderFailed):
                await self._state_store.delete(login_id)
                return DeviceLoginFailed(
                    status_code=provider_result.status_code,
                    detail=provider_result.detail,
                )
            save_result = await self._credential_writer.save(
                credential=provider_result.credential,
                actor_id=state.owner_id,
                overwrite_existing=state.overwrite_existing,
            )
            if isinstance(save_result, CredentialConflict):
                await self._state_store.delete(login_id)
                return DeviceLoginFailed(status_code=409, detail="Credential already exists")
            await self._state_store.delete(login_id)
            return DeviceLoginCompleted(credential_name=provider_result.credential.credential_name)
