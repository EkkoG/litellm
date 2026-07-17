import json
from hashlib import sha256
from typing import Sequence
from unittest.mock import AsyncMock, MagicMock

import pytest

from litellm.proxy._types import LiteLLM_UserTable
from litellm.proxy.auth.ldap_auth import LDAPConfig
from litellm.proxy.auth.ldap_user_status_sync import (
    LDAPActive,
    LDAPInactive,
    LDAPMissing,
    LDAPUnknown,
    LDAPUserStatusSyncManager,
    LDAPUserStatusSyncService,
    PrismaLDAPUserStore,
    _status_for_user,
)


def _config(max_deactivation_ratio: float = 1.0) -> LDAPConfig:
    return LDAPConfig(
        ldap_enabled=True,
        ldap_url="ldaps://ldap.example.com:636",
        ldap_base_dn="dc=example,dc=com",
        ldap_access_filter="(!(pwdAccountLockedTime=*))",
        ldap_sync_enabled=True,
        ldap_sync_max_deactivation_ratio=max_deactivation_ratio,
    )


def _user(user_id: str, active: bool = True) -> LiteLLM_UserTable:
    return LiteLLM_UserTable(
        user_id=user_id,
        metadata={
            "auth_provider": "ldap",
            "ldap_username": user_id,
            "ldap_dn": f"uid={user_id},dc=example,dc=com",
            "identity_active": active,
        },
    )


class _TransactionContext:
    def __init__(self, transaction: MagicMock) -> None:
        self._transaction = transaction

    async def __aenter__(self) -> MagicMock:
        return self._transaction

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None


def test_status_for_user_recovers_moved_dn_with_stable_principal() -> None:
    class Attribute:
        values = ("directory-object-123",)

    class Entry:
        entry_dn = "uid=alice,ou=New,dc=example,dc=com"
        entryUUID = Attribute()

    class Connection:
        entries: tuple[Entry, ...] = ()
        result: object = {"result": 0}

        def start_tls(self) -> bool:
            return True

        def bind(self) -> bool:
            return True

        def search(
            self,
            search_base: str,
            search_filter: str,
            search_scope: object,
            attributes: Sequence[str],
            size_limit: int,
        ) -> bool:
            if search_filter == "(objectClass=*)":
                self.entries = ()
                self.result = {"result": 32}
                return False
            else:
                self.entries = (Entry(),)
                self.result = {"result": 0}
            return True

        def unbind(self) -> bool:
            return True

    user = _user("alice")
    user.metadata["ldap_principal_hash"] = sha256(b"directory-object-123").hexdigest()
    config = _config().model_copy(
        update={
            "ldap_user_search_filter": "(uid={username})",
            "ldap_user_id_attribute": "entryUUID",
        }
    )

    result = _status_for_user(
        connection=Connection(),
        config=config,
        user=user,
        base_scope="BASE",
        subtree_scope="SUBTREE",
        escape_filter_chars=lambda value: value,
    )

    assert isinstance(result, LDAPActive)
    assert result.dn == "uid=alice,ou=New,dc=example,dc=com"


@pytest.mark.asyncio
async def test_sync_dry_run_reports_transitions_without_writes() -> None:
    users = (_user("active"), _user("inactive"), _user("missing"), _user("unknown"))
    store = MagicMock()
    store.list_ldap_users = AsyncMock(return_value=users)
    store.update_metadata = AsyncMock()
    collector = AsyncMock(
        return_value=(
            LDAPActive(tag="active", user_id="active", dn="uid=active,dc=example,dc=com"),
            LDAPInactive(tag="inactive", user_id="inactive", dn="uid=inactive,dc=example,dc=com"),
            LDAPMissing(tag="missing", user_id="missing"),
            LDAPUnknown(tag="unknown", user_id="unknown", reason="principal_mismatch"),
        )
    )
    service = LDAPUserStatusSyncService(user_store=store, collector=collector)

    result = await service.sync(config=_config(), dry_run=True, trigger="manual")

    assert result.scanned == 4
    assert result.would_deactivate == 1
    assert result.deactivated == 0
    assert result.missing == 1
    assert result.unknown == 1
    assert result.unchanged == 3
    store.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_aborts_when_deactivation_ratio_exceeds_threshold() -> None:
    users = (_user("alice"), _user("bob"))
    store = MagicMock()
    store.list_ldap_users = AsyncMock(return_value=users)
    store.update_metadata = AsyncMock()
    collector = AsyncMock(
        return_value=(
            LDAPInactive(tag="inactive", user_id="alice", dn="uid=alice,dc=example,dc=com"),
            LDAPActive(tag="active", user_id="bob", dn="uid=bob,dc=example,dc=com"),
        )
    )
    service = LDAPUserStatusSyncService(user_store=store, collector=collector)

    result = await service.sync(config=_config(max_deactivation_ratio=0.2), dry_run=False, trigger="scheduled")

    assert result.aborted is True
    assert result.abort_reason is not None
    assert "deactivation ratio" in result.abort_reason
    store.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_can_disable_missing_users() -> None:
    store = MagicMock()
    store.list_ldap_users = AsyncMock(return_value=(_user("alice"),))
    store.update_metadata = AsyncMock()
    collector = AsyncMock(return_value=(LDAPMissing(tag="missing", user_id="alice"),))
    service = LDAPUserStatusSyncService(user_store=store, collector=collector)
    config = _config().model_copy(update={"ldap_missing_user_action": "disable"})

    result = await service.sync(config=config, dry_run=True, trigger="manual")

    assert result.missing == 1
    assert result.would_deactivate == 1
    store.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_commits_metadata_transaction_and_invalidates_user_cache() -> None:
    user = _user("alice")
    store = MagicMock()
    store.list_ldap_users = AsyncMock(return_value=(user,))
    store.update_metadata = AsyncMock()
    collector = AsyncMock(
        return_value=(LDAPInactive(tag="inactive", user_id="alice", dn="uid=alice,dc=example,dc=com"),)
    )
    cache = MagicMock()
    cache.async_delete_cache = AsyncMock()
    service = LDAPUserStatusSyncService(user_store=store, user_api_key_cache=cache, collector=collector)

    result = await service.sync(config=_config(), dry_run=False, trigger="manual")

    assert result.deactivated == 1
    store.update_metadata.assert_awaited_once()
    transition = store.update_metadata.await_args.args[0][0]
    metadata = transition.metadata
    assert metadata is not None
    assert metadata["identity_active"] is False
    assert metadata["identity_status_reason"] == "ldap_access_filter_mismatch"
    cache.async_delete_cache.assert_awaited_once_with("alice")


@pytest.mark.asyncio
async def test_sync_directory_failure_preserves_all_users() -> None:
    store = MagicMock()
    store.list_ldap_users = AsyncMock(return_value=(_user("alice"),))
    store.update_metadata = AsyncMock()
    collector = AsyncMock(side_effect=RuntimeError("directory unavailable"))
    service = LDAPUserStatusSyncService(user_store=store, collector=collector)

    result = await service.sync(config=_config(), dry_run=False, trigger="scheduled")

    assert result.aborted is True
    assert result.abort_reason == "LDAP directory query failed"
    store.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_prisma_store_uses_json_filter_and_transaction() -> None:
    from prisma import Json
    from prisma.models import LiteLLM_UserTable as PrismaUserTable

    prisma = MagicMock()
    prisma.db.litellm_usertable.find_many = AsyncMock(
        return_value=(
            PrismaUserTable(
                user_id="alice",
                teams=[],
                spend=0.0,
                models=[],
                metadata='{"auth_provider":"ldap","ldap_username":"alice"}',
                allowed_cache_controls=[],
                policies=[],
                model_spend="{}",
                model_max_budget="{}",
            ),
        )
    )
    transaction = MagicMock()
    transaction.litellm_usertable.update = AsyncMock()
    prisma.db.tx.return_value = _TransactionContext(transaction)
    store = PrismaLDAPUserStore(prisma)

    users = await store.list_ldap_users()
    transition = MagicMock(user_id="alice", metadata={"identity_active": False})
    await store.update_metadata((transition,))

    where = prisma.db.litellm_usertable.find_many.await_args.kwargs["where"]
    value = where["metadata"]["equals"]
    assert isinstance(value, Json)
    assert value.data == "ldap"
    assert users[0].user_id == "alice"
    assert users[0].metadata == {"auth_provider": "ldap", "ldap_username": "alice"}
    update_data = transaction.litellm_usertable.update.await_args.kwargs["data"]
    assert json.loads(update_data["metadata"])["identity_active"] is False


@pytest.mark.asyncio
async def test_manager_skips_when_another_pod_holds_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "litellm.proxy.auth.ldap_user_status_sync.load_ldap_config",
        AsyncMock(return_value=_config()),
    )
    lock_manager = MagicMock()
    lock_manager.redis_cache = object()
    lock_manager.acquire_lock = AsyncMock(return_value=False)
    lock_manager.release_lock = AsyncMock()
    manager = LDAPUserStatusSyncManager(prisma_client=MagicMock(), pod_lock_manager=lock_manager)

    result = await manager.run(dry_run=False, trigger="scheduled")

    assert result.aborted is True
    assert result.abort_reason == "another LDAP user status synchronization is running"
    lock_manager.release_lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_manager_skips_stale_scheduled_job_after_sync_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "litellm.proxy.auth.ldap_user_status_sync.load_ldap_config",
        AsyncMock(return_value=_config().model_copy(update={"ldap_sync_enabled": False})),
    )
    lock_manager = MagicMock()
    lock_manager.redis_cache = object()
    lock_manager.acquire_lock = AsyncMock(return_value=True)
    manager = LDAPUserStatusSyncManager(prisma_client=MagicMock(), pod_lock_manager=lock_manager)

    result = await manager.run(dry_run=False, trigger="scheduled")

    assert result.aborted is True
    assert result.abort_reason == "LDAP user status synchronization is disabled"
    lock_manager.acquire_lock.assert_not_awaited()
