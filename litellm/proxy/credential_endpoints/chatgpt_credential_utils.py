import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from pydantic import TypeAdapter

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper, encrypt_value_helper
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem

CHATGPT_CREDENTIAL_PROVIDER = "chatgpt"
_CREDENTIAL_VALUES_ADAPTER = TypeAdapter(dict[str, object])
_CREDENTIAL_INFO_ADAPTER = TypeAdapter(dict[str, object])


def _credential_values(credential: CredentialItem) -> dict[str, object]:
    return _CREDENTIAL_VALUES_ADAPTER.validate_python(credential.credential_values or {})


def _credential_info(credential: CredentialItem) -> dict[str, object]:
    return _CREDENTIAL_INFO_ADAPTER.validate_python(credential.credential_info or {})


def build_chatgpt_credential_values(tokens: dict[str, str], api_base: Optional[str] = None) -> dict[str, str]:
    auth_record = Authenticator()._build_auth_record(tokens)
    values = {
        "api_key": auth_record.get("access_token"),
        "chatgpt_refresh_token": auth_record.get("refresh_token"),
        "chatgpt_id_token": auth_record.get("id_token"),
        "chatgpt_account_id": auth_record.get("account_id"),
        "chatgpt_plan_type": auth_record.get("plan_type"),
        "chatgpt_expires_at": str(auth_record["expires_at"]) if auth_record.get("expires_at") is not None else None,
        "api_base": api_base,
    }
    return {key: value for key, value in values.items() if value is not None}


@dataclass(slots=True)
class ChatGPTCredentialRefreshManager:
    authenticator: Authenticator = field(default_factory=Authenticator)
    repository_factory: Callable[[], CredentialsRepository | None] | None = None
    credential_getter: Callable[[str], CredentialItem | None] | None = None
    credential_upsert: Callable[[list[CredentialItem]], None] | None = None
    _refresh_tasks: dict[str, asyncio.Task[dict[str, object]]] = field(default_factory=dict, init=False)

    def resolve(self, credential: CredentialItem, user_id: str | None = None) -> dict[str, object]:
        values = _credential_values(credential)
        if not self._needs_refresh(values):
            return values
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aresolve(credential=credential, user_id=user_id))
        return values

    async def aresolve(self, credential: CredentialItem, user_id: str | None = None) -> dict[str, object]:
        values = _credential_values(credential)
        if not self._needs_refresh(values):
            return values
        credential_name = credential.credential_name
        task = self._refresh_tasks.get(credential_name)
        if task is None:
            task = asyncio.create_task(self._refresh(credential=credential, user_id=user_id))
            self._refresh_tasks[credential_name] = task
        try:
            return dict(await asyncio.shield(task))
        finally:
            if task.done() and self._refresh_tasks.get(credential_name) is task:
                self._refresh_tasks.pop(credential_name, None)

    async def drain(self) -> None:
        tasks = tuple(self._refresh_tasks.values())
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    def _needs_refresh(self, values: dict[str, object]) -> bool:
        return self.authenticator.is_access_token_expired(values) and bool(values.get("chatgpt_refresh_token"))

    async def _refresh(self, credential: CredentialItem, user_id: str | None) -> dict[str, object]:
        repository = self.repository_factory() if self.repository_factory is not None else None
        if repository is None:
            return await self._refresh_local(credential)
        async with repository.locked_by_name(credential.credential_name) as locked_repository:
            stored = await locked_repository.find_by_name(credential.credential_name)
            if stored is None:
                return await self._refresh_local(credential)
            decrypted = self._decrypt_credential(stored)
            values = dict(decrypted.credential_values or {})
            if self._needs_refresh(values):
                refreshed_values = await self._refresh_values(values)
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
                values = {**values, **refreshed_values}
            refreshed_credential = CredentialItem(
                credential_name=credential.credential_name,
                credential_values=values,
                credential_info=_credential_info(decrypted),
            )
        self._update_runtime(refreshed_credential)
        return dict(values)

    async def _refresh_local(self, credential: CredentialItem) -> dict[str, object]:
        current = (
            self.credential_getter(credential.credential_name) if self.credential_getter is not None else credential
        )
        selected = current or credential
        values = dict(selected.credential_values or {})
        if self._needs_refresh(values):
            values = {**values, **await self._refresh_values(values)}
        self._update_runtime(
            CredentialItem(
                credential_name=selected.credential_name,
                credential_values=values,
                credential_info=_credential_info(selected),
            )
        )
        return dict(values)

    async def _refresh_values(self, values: dict[str, object]) -> dict[str, str]:
        refresh_token = values.get("chatgpt_refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return {}
        tokens = await self.authenticator.async_refresh_tokens(refresh_token)
        return build_chatgpt_credential_values(tokens=tokens, api_base=values.get("api_base"))

    @staticmethod
    def _decrypt_credential(credential: CredentialItem) -> CredentialItem:
        values = _credential_values(credential)
        return CredentialItem(
            credential_name=credential.credential_name,
            credential_values={
                key: decrypt_value_helper(value=value, key=key) or value if isinstance(value, str) else value
                for key, value in values.items()
            },
            credential_info=_credential_info(credential),
        )

    def _update_runtime(self, credential: CredentialItem) -> None:
        if self.credential_upsert is not None:
            self.credential_upsert([credential])


def _repository_factory() -> CredentialsRepository | None:
    try:
        from litellm.proxy.proxy_server import prisma_client
    except ImportError:
        return None
    return CredentialsRepository(prisma_client) if prisma_client is not None else None


def _credential_getter(credential_name: str) -> CredentialItem | None:
    from litellm.litellm_core_utils.credential_accessor import CredentialAccessor

    return CredentialAccessor.get_credential(credential_name)


def _credential_upsert(credentials: list[CredentialItem]) -> None:
    from litellm.litellm_core_utils.credential_accessor import CredentialAccessor

    CredentialAccessor.upsert_credentials(credentials)


_CHATGPT_CREDENTIAL_REFRESH_MANAGER = ChatGPTCredentialRefreshManager(
    repository_factory=_repository_factory,
    credential_getter=_credential_getter,
    credential_upsert=_credential_upsert,
)


def refresh_chatgpt_credential_if_needed(
    credential: CredentialItem,
    user_id: Optional[str] = None,
) -> dict[str, object]:
    return _CHATGPT_CREDENTIAL_REFRESH_MANAGER.resolve(credential=credential, user_id=user_id)


async def async_refresh_chatgpt_credential_if_needed(
    credential: CredentialItem,
    user_id: Optional[str] = None,
) -> dict[str, object]:
    return await _CHATGPT_CREDENTIAL_REFRESH_MANAGER.aresolve(credential=credential, user_id=user_id)


async def drain_chatgpt_credential_refreshes() -> None:
    await _CHATGPT_CREDENTIAL_REFRESH_MANAGER.drain()
