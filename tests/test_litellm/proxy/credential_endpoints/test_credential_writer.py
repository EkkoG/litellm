from unittest.mock import AsyncMock, Mock

import pytest
from prisma.errors import UniqueViolationError

from litellm.proxy.credential_endpoints.credential_writer import (
    CredentialConflict,
    CredentialNotFound,
    CredentialSaved,
    CredentialWriter,
    update_db_credential,
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


def test_update_db_credential_reencrypts_values_with_new_key(monkeypatch):
    encrypted_values = []

    def fake_encrypt(value: str, new_encryption_key=None) -> str:
        encrypted_values.append((value, new_encryption_key))
        return f"cipher::{len(value)}"

    monkeypatch.setattr(
        "litellm.proxy.credential_endpoints.credential_writer.encrypt_value_helper",
        fake_encrypt,
    )
    db_credential = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "old-ciphertext", "account_id": "old-account-ciphertext"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    decrypted_patch = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "plain-token", "account_id": "plain-account"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )

    merged = update_db_credential(
        db_credential=db_credential,
        updated_patch=decrypted_patch,
        new_encryption_key="new-master-key",
    )

    assert encrypted_values == [
        ("plain-token", "new-master-key"),
        ("plain-account", "new-master-key"),
    ]
    assert merged.credential_values == {
        "api_key": "cipher::11",
        "account_id": "cipher::13",
    }
    assert "plain-token" not in str(merged.credential_values)
    assert merged.credential_info == {"custom_llm_provider": "chatgpt"}
    assert merged.credential_name == "managed-chatgpt"


def test_update_db_credential_preserves_stored_fields_not_in_patch(monkeypatch):
    monkeypatch.setattr(
        "litellm.proxy.credential_endpoints.credential_writer.encrypt_value_helper",
        lambda value, new_encryption_key=None: f"cipher::{len(value)}",
    )
    db_credential = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "old-ciphertext", "refresh_token": "old-refresh-ciphertext"},
        credential_info={"custom_llm_provider": "chatgpt", "plan": "pro"},
    )
    decrypted_patch = CredentialItem(
        credential_name="managed-chatgpt",
        credential_values={"api_key": "plain-token"},
        credential_info={},
    )

    merged = update_db_credential(
        db_credential=db_credential,
        updated_patch=decrypted_patch,
        new_encryption_key=None,
    )

    assert merged.credential_values["api_key"] == "cipher::11"
    assert merged.credential_values["refresh_token"] == "old-refresh-ciphertext"
    assert merged.credential_info["plan"] == "pro"


@pytest.mark.asyncio
async def test_credential_writer_uses_atomic_json_merge_when_supported():
    repository = Mock()
    repository.supports_json_merge_update = True
    repository.merge_update_by_name = AsyncMock(return_value=True)
    repository.find_by_name = AsyncMock()
    repository.update_by_name = AsyncMock()
    writer = CredentialWriter(
        repository=repository,
        cache_upsert=Mock(),
        cache_get=Mock(return_value=None),
        encrypt_value=lambda value: f"cipher::{len(value)}",
    )

    result = await writer.patch(
        credential_name="managed-chatgpt",
        patch=CredentialItem(
            credential_name="managed-chatgpt",
            credential_values={"api_key": "plain-token"},
            credential_info={"daily_quota_snapshot": {"date": "2026-07-15"}},
        ),
        actor_id="litellm_proxy",
    )

    assert isinstance(result, CredentialSaved)
    repository.merge_update_by_name.assert_awaited_once()
    merge_kwargs = repository.merge_update_by_name.call_args.kwargs
    assert merge_kwargs["credential_values_patch"] == {"api_key": "cipher::11"}
    assert merge_kwargs["credential_info_patch"] == {"daily_quota_snapshot": {"date": "2026-07-15"}}
    assert merge_kwargs["updated_by"] == "litellm_proxy"
    repository.find_by_name.assert_not_awaited()
    repository.update_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_credential_writer_json_merge_maps_missing_credential_to_not_found():
    repository = Mock()
    repository.supports_json_merge_update = True
    repository.merge_update_by_name = AsyncMock(return_value=False)
    writer = CredentialWriter(
        repository=repository,
        cache_upsert=Mock(),
        cache_get=Mock(return_value=None),
        encrypt_value=lambda value: value,
    )

    result = await writer.patch(
        credential_name="ghost",
        patch=CredentialItem(credential_name="ghost", credential_values={"k": "v"}, credential_info={}),
        actor_id="litellm_proxy",
    )

    assert isinstance(result, CredentialNotFound)


def test_credentials_repository_merge_update_sql():
    """The atomic merge must issue a single jsonb ||-based UPDATE scoped to the
    credential name, so concurrent patches to disjoint keys cannot overwrite
    each other."""
    import asyncio
    import json as _json

    from litellm.repositories.credentials_repository import CredentialsRepository

    executed = []

    class _FakeDB:
        async def execute_raw(self, query, *params):
            executed.append((query, params))
            return 1

    class _FakePrisma:
        db = _FakeDB()

    repository = CredentialsRepository(_FakePrisma())
    assert repository.supports_json_merge_update is True

    updated = asyncio.run(
        repository.merge_update_by_name(
            "managed-chatgpt",
            new_name="managed-chatgpt",
            credential_values_patch={"api_key": "cipher"},
            credential_info_patch={"daily_quota_snapshot": {"date": "2026-07-15"}},
            updated_by="litellm_proxy",
        )
    )

    assert updated is True
    query, params = executed[0]
    assert 'UPDATE "LiteLLM_CredentialsTable"' in query
    assert "|| $3::jsonb" in query
    assert "|| $4::jsonb" in query
    assert "WHERE credential_name = $1" in query
    assert params == (
        "managed-chatgpt",
        "managed-chatgpt",
        _json.dumps({"api_key": "cipher"}),
        _json.dumps({"daily_quota_snapshot": {"date": "2026-07-15"}}),
        "litellm_proxy",
    )
