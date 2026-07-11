from unittest.mock import AsyncMock, Mock

import pytest
from prisma.errors import UniqueViolationError

from litellm.proxy.credential_endpoints.credential_writer import (
    CredentialConflict,
    CredentialSaved,
    CredentialWriter,
)
from litellm.types.utils import CredentialItem


@pytest.mark.asyncio
async def test_credential_writer_encrypts_persists_and_updates_runtime_snapshot():
    repository = Mock()
    repository.find_by_name = AsyncMock(return_value=None)
    repository.create = AsyncMock()
    repository.update_by_name = AsyncMock()
    cache_upsert = Mock()
    writer = CredentialWriter(
        repository=repository,
        cache_upsert=cache_upsert,
        encrypt_value=lambda value: "ciphertext-value",
    )
    credential = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "secret-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )

    result = await writer.save(
        credential=credential,
        actor_id="admin-user",
        overwrite_existing=False,
    )

    assert isinstance(result, CredentialSaved)
    repository.create.assert_awaited_once()
    stored = repository.create.call_args.kwargs["data"]
    assert "secret-token" not in str(stored)
    assert "ciphertext-value" in str(stored)
    cache_upsert.assert_called_once_with([credential])


@pytest.mark.asyncio
async def test_credential_writer_rejects_existing_name_without_overwrite():
    existing = CredentialItem(credential_name="shared", credential_values={}, credential_info={})
    repository = Mock()
    repository.find_by_name = AsyncMock(return_value=existing)
    repository.create = AsyncMock()
    repository.update_by_name = AsyncMock()
    writer = CredentialWriter(
        repository=repository,
        cache_upsert=Mock(),
        encrypt_value=lambda value: value,
    )

    result = await writer.save(
        credential=existing,
        actor_id="admin-user",
        overwrite_existing=False,
    )

    assert isinstance(result, CredentialConflict)
    repository.create.assert_not_awaited()
    repository.update_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_credential_writer_maps_concurrent_create_race_to_conflict():
    credential = CredentialItem(credential_name="shared", credential_values={}, credential_info={})
    repository = Mock()
    repository.find_by_name = AsyncMock(return_value=None)
    repository.create = AsyncMock(
        side_effect=UniqueViolationError({}, message="Unique constraint failed")
    )
    repository.update_by_name = AsyncMock()
    writer = CredentialWriter(
        repository=repository,
        cache_upsert=Mock(),
        encrypt_value=lambda value: value,
    )

    result = await writer.save(
        credential=credential,
        actor_id="admin-user",
        overwrite_existing=False,
    )

    assert isinstance(result, CredentialConflict)


@pytest.mark.asyncio
async def test_credential_writer_maps_rename_collision_to_conflict():
    existing = CredentialItem(
        credential_name="old-name",
        credential_values={"api_key": "encrypted-old"},
        credential_info={},
    )
    repository = Mock()
    repository.find_by_name = AsyncMock(return_value=existing)
    repository.update_by_name = AsyncMock(
        side_effect=UniqueViolationError({}, message="Unique constraint failed")
    )
    writer = CredentialWriter(
        repository=repository,
        cache_upsert=Mock(),
        cache_get=Mock(return_value=None),
        cache_delete=Mock(),
        encrypt_value=lambda value: value,
    )

    result = await writer.patch(
        credential_name="old-name",
        patch=CredentialItem(
            credential_name="existing-name",
            credential_values={},
            credential_info={},
        ),
        actor_id="admin-user",
    )

    assert isinstance(result, CredentialConflict)
