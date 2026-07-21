import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import TypeAdapter

from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper, encrypt_value_helper
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem

_CREDENTIAL_VALUES_ADAPTER = TypeAdapter(dict[str, object])
_CREDENTIAL_INFO_ADAPTER = TypeAdapter(dict[str, object])


class OAuthCredentialRefreshAdapter(Protocol):
    def needs_refresh(self, values: Mapping[str, object]) -> bool: ...

    async def refresh_values(self, values: Mapping[str, object]) -> dict[str, str]: ...


@dataclass(slots=True)
class OAuthCredentialRefreshManager:
    adapter: OAuthCredentialRefreshAdapter
    repository_factory: Callable[[], CredentialsRepository | None] | None = None
    credential_getter: Callable[[str], CredentialItem | None] | None = None
    credential_upsert: Callable[[list[CredentialItem]], None] | None = None
    _refresh_tasks: dict[str, asyncio.Task[dict[str, object]]] = field(default_factory=dict, init=False)

    def resolve(self, credential: CredentialItem, user_id: str | None = None) -> dict[str, object]:
        values = self._credential_values(credential)
        if not self.adapter.needs_refresh(values):
            return values
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aresolve(credential=credential, user_id=user_id))
        return values

    async def aresolve(self, credential: CredentialItem, user_id: str | None = None) -> dict[str, object]:
        values = self._credential_values(credential)
        if not self.adapter.needs_refresh(values):
            return values
        return await self._run_single_flight(credential=credential, user_id=user_id)

    def force_refresh(
        self,
        credential: CredentialItem,
        rejected_access_token: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.aforce_refresh(
                    credential=credential,
                    rejected_access_token=rejected_access_token,
                    user_id=user_id,
                )
            )
        raise RuntimeError("Synchronous credential refresh cannot run inside an event loop")

    async def aforce_refresh(
        self,
        credential: CredentialItem,
        rejected_access_token: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        return await self._run_single_flight(
            credential=credential,
            user_id=user_id,
            rejected_access_token=rejected_access_token,
        )

    async def _run_single_flight(
        self,
        credential: CredentialItem,
        user_id: str | None,
        rejected_access_token: str | None = None,
    ) -> dict[str, object]:
        credential_name = credential.credential_name
        existing_task = self._refresh_tasks.get(credential_name)
        task = existing_task or asyncio.create_task(
            self._refresh(
                credential=credential,
                user_id=user_id,
                rejected_access_token=rejected_access_token,
            )
        )
        if existing_task is None:
            self._refresh_tasks = {**self._refresh_tasks, credential_name: task}
        try:
            return dict(await asyncio.shield(task))
        finally:
            if task.done() and self._refresh_tasks.get(credential_name) is task:
                self._refresh_tasks = {
                    name: active_task for name, active_task in self._refresh_tasks.items() if name != credential_name
                }

    async def drain(self) -> None:
        tasks = tuple(self._refresh_tasks.values())
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    async def _refresh(
        self,
        credential: CredentialItem,
        user_id: str | None,
        rejected_access_token: str | None = None,
    ) -> dict[str, object]:
        repository = self.repository_factory() if self.repository_factory is not None else None
        if repository is None:
            return await self._refresh_local(credential, rejected_access_token)
        async with repository.locked_by_name(credential.credential_name) as locked_repository:
            stored = await locked_repository.find_by_name(credential.credential_name)
            if stored is None:
                return await self._refresh_local(credential, rejected_access_token)
            decrypted = self._decrypt_credential(stored)
            stored_values = self._credential_values(decrypted)
            should_refresh = (
                stored_values.get("api_key") == rejected_access_token
                if rejected_access_token is not None
                else self.adapter.needs_refresh(stored_values)
            )
            refreshed_values = await self.adapter.refresh_values(stored_values) if should_refresh else {}
            if refreshed_values:
                updated = await locked_repository.merge_update_by_name(
                    credential.credential_name,
                    new_name=credential.credential_name,
                    credential_values_patch={
                        key: encrypt_value_helper(value) for key, value in refreshed_values.items()
                    },
                    credential_info_patch={},
                    updated_by=user_id or "litellm_proxy",
                )
                if not updated:
                    raise ValueError(f"Credential not found: {credential.credential_name}")
            resolved_values = {**stored_values, **refreshed_values}
            refreshed_credential = CredentialItem(
                credential_name=credential.credential_name,
                credential_values=resolved_values,
                credential_info=self._credential_info(decrypted),
            )
        self._update_runtime(refreshed_credential)
        return resolved_values

    async def _refresh_local(
        self,
        credential: CredentialItem,
        rejected_access_token: str | None = None,
    ) -> dict[str, object]:
        current = (
            self.credential_getter(credential.credential_name) if self.credential_getter is not None else credential
        )
        selected = current or credential
        values = self._credential_values(selected)
        should_refresh = (
            values.get("api_key") == rejected_access_token
            if rejected_access_token is not None
            else self.adapter.needs_refresh(values)
        )
        refreshed_values = await self.adapter.refresh_values(values) if should_refresh else {}
        resolved_values = {**values, **refreshed_values}
        self._update_runtime(
            CredentialItem(
                credential_name=selected.credential_name,
                credential_values=resolved_values,
                credential_info=self._credential_info(selected),
            )
        )
        return resolved_values

    @staticmethod
    def _credential_values(credential: CredentialItem) -> dict[str, object]:
        return _CREDENTIAL_VALUES_ADAPTER.validate_python(credential.model_dump()["credential_values"])

    @staticmethod
    def _credential_info(credential: CredentialItem) -> dict[str, object]:
        return _CREDENTIAL_INFO_ADAPTER.validate_python(credential.model_dump()["credential_info"])

    @classmethod
    def _decrypt_credential(cls, credential: CredentialItem) -> CredentialItem:
        values = cls._credential_values(credential)
        return CredentialItem(
            credential_name=credential.credential_name,
            credential_values={
                key: decrypt_value_helper(value=value, key=key) or value if isinstance(value, str) else value
                for key, value in values.items()
            },
            credential_info=cls._credential_info(credential),
        )

    def _update_runtime(self, credential: CredentialItem) -> None:
        if self.credential_upsert is not None:
            self.credential_upsert([credential])
