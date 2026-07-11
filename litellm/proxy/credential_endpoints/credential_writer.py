import json
from dataclasses import dataclass
from typing import Callable, Optional, Union

from prisma.errors import UniqueViolationError
from pydantic import BaseModel

from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.proxy.common_utils.encrypt_decrypt_utils import encrypt_value_helper
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem


class _TypedCredential(BaseModel):
    credential_name: str
    credential_values: dict[str, str]
    credential_info: dict[str, object]


def _validate_credential(credential: CredentialItem) -> _TypedCredential:
    return _TypedCredential.model_validate(credential, from_attributes=True)


@dataclass(frozen=True, slots=True)
class CredentialSaved:
    credential: CredentialItem
    created: bool


@dataclass(frozen=True, slots=True)
class CredentialConflict:
    credential_name: str


@dataclass(frozen=True, slots=True)
class CredentialNotFound:
    credential_name: str


CredentialSaveResult = Union[CredentialSaved, CredentialConflict, CredentialNotFound]


def encrypt_credential_values(
    credential: CredentialItem,
    new_encryption_key: Optional[str] = None,
) -> CredentialItem:
    typed_credential = _validate_credential(credential)
    return CredentialItem(
        credential_name=typed_credential.credential_name,
        credential_values={
            key: encrypt_value_helper(value, new_encryption_key)
            for key, value in typed_credential.credential_values.items()
        },
        credential_info=typed_credential.credential_info,
    )


class CredentialWriter:
    def __init__(
        self,
        repository: CredentialsRepository,
        cache_upsert: Callable[[list[CredentialItem]], None] = CredentialAccessor.upsert_credentials,
        cache_get: Callable[[str], Optional[CredentialItem]] = CredentialAccessor.get_credential,
        cache_delete: Callable[[str], None] = CredentialAccessor.delete_credential,
        encrypt_value: Callable[[str], str] = encrypt_value_helper,
    ) -> None:
        self._repository = repository
        self._cache_upsert = cache_upsert
        self._cache_get = cache_get
        self._cache_delete = cache_delete
        self._encrypt_value = encrypt_value

    async def exists(self, credential_name: str) -> bool:
        return await self._repository.find_by_name(credential_name) is not None

    async def save(
        self,
        credential: CredentialItem,
        actor_id: Optional[str],
        overwrite_existing: bool,
    ) -> CredentialSaveResult:
        typed_credential = _validate_credential(credential)
        existing = await self._repository.find_by_name(typed_credential.credential_name)
        if existing is not None and not overwrite_existing:
            return CredentialConflict(credential_name=typed_credential.credential_name)

        encrypted_values = {
            key: self._encrypt_value(value) for key, value in typed_credential.credential_values.items()
        }
        data: dict[str, object] = {
            "credential_name": typed_credential.credential_name,
            "credential_values": json.dumps(encrypted_values),
            "credential_info": json.dumps(typed_credential.credential_info),
        }
        if existing is None:
            try:
                await self._repository.create(
                    data={
                        **data,
                        "created_by": actor_id,
                        "updated_by": actor_id,
                    }
                )
            except UniqueViolationError:
                return CredentialConflict(credential_name=typed_credential.credential_name)
        else:
            await self._repository.update_by_name(
                typed_credential.credential_name,
                data={
                    **data,
                    "updated_by": actor_id,
                },
            )
        self._cache_upsert([credential])
        return CredentialSaved(credential=credential, created=existing is None)

    async def patch(
        self,
        credential_name: str,
        patch: CredentialItem,
        actor_id: Optional[str],
    ) -> CredentialSaveResult:
        existing = await self._repository.find_by_name(credential_name)
        if existing is None:
            return CredentialNotFound(credential_name=credential_name)
        typed_existing = _validate_credential(existing)
        typed_patch = _validate_credential(patch)
        new_name = typed_patch.credential_name or credential_name
        encrypted_patch_values = {
            key: self._encrypt_value(value) for key, value in typed_patch.credential_values.items()
        }
        stored_values = {**typed_existing.credential_values, **encrypted_patch_values}
        stored_info = {**typed_existing.credential_info, **typed_patch.credential_info}
        try:
            await self._repository.update_by_name(
                credential_name,
                data={
                    "credential_name": new_name,
                    "credential_values": json.dumps(stored_values),
                    "credential_info": json.dumps(stored_info),
                    "updated_by": actor_id,
                },
            )
        except UniqueViolationError:
            return CredentialConflict(credential_name=new_name)
        runtime_existing = self._cache_get(credential_name)
        if runtime_existing is not None:
            typed_runtime_existing = _validate_credential(runtime_existing)
            runtime_credential = CredentialItem(
                credential_name=new_name,
                credential_values={
                    **typed_runtime_existing.credential_values,
                    **typed_patch.credential_values,
                },
                credential_info={
                    **typed_runtime_existing.credential_info,
                    **typed_patch.credential_info,
                },
            )
            if new_name != credential_name:
                self._cache_delete(credential_name)
            self._cache_upsert([runtime_credential])
        return CredentialSaved(credential=patch, created=False)

    async def delete(self, credential_name: str) -> None:
        await self._repository.delete_by_name(credential_name)
        self._cache_delete(credential_name)
