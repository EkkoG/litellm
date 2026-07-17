import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Callable, Literal, Protocol, Sequence, cast

from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import LiteLLM_UserTable, ProxyException
from litellm.proxy.auth.ldap_auth import LDAPConfig, ldap_service_unavailable_error, load_ldap_config
from litellm.proxy.utils import PrismaClient
from litellm.types.utils import LiteLLMPydanticObjectBase

LDAP_USER_STATUS_SYNC_JOB_NAME = "ldap_user_status_sync_job"

_METADATA_ADAPTER = TypeAdapter(dict[str, object])
_ENTRY_VALUES_ADAPTER = TypeAdapter(tuple[object, ...])
_LDAP_USERS_ADAPTER = TypeAdapter(tuple[LiteLLM_UserTable, ...])
_LDAP_RESULT_ADAPTER = TypeAdapter(dict[str, object])


@dataclass(frozen=True, slots=True)
class LDAPActive:
    tag: Literal["active"]
    user_id: str
    dn: str


@dataclass(frozen=True, slots=True)
class LDAPInactive:
    tag: Literal["inactive"]
    user_id: str
    dn: str


@dataclass(frozen=True, slots=True)
class LDAPMissing:
    tag: Literal["missing"]
    user_id: str


@dataclass(frozen=True, slots=True)
class LDAPUnknown:
    tag: Literal["unknown"]
    user_id: str
    reason: str


LDAPStatusOutcome = LDAPActive | LDAPInactive | LDAPMissing | LDAPUnknown
LDAPSyncTrigger = Literal["manual", "scheduled", "startup"]


class LDAPUserStatusSyncResult(LiteLLMPydanticObjectBase):
    dry_run: bool
    trigger: LDAPSyncTrigger
    scanned: int = 0
    activated: int = 0
    deactivated: int = 0
    would_activate: int = 0
    would_deactivate: int = 0
    missing: int = 0
    unknown: int = 0
    unchanged: int = 0
    aborted: bool = False
    abort_reason: str | None = None


class _LDAPEntryAttribute(Protocol):
    @property
    def values(self) -> object: ...


class _LDAPEntry(Protocol):
    @property
    def entry_dn(self) -> object: ...


class _LDAPStatusConnection(Protocol):
    entries: Sequence[_LDAPEntry]
    result: object

    def start_tls(self) -> bool: ...

    def bind(self) -> bool: ...

    def search(
        self,
        search_base: str,
        search_filter: str,
        search_scope: object,
        attributes: Sequence[str],
        size_limit: int,
    ) -> bool: ...

    def unbind(self) -> object: ...


class LDAPStatusCollector(Protocol):
    async def __call__(
        self,
        config: LDAPConfig,
        users: Sequence[LiteLLM_UserTable],
    ) -> tuple[LDAPStatusOutcome, ...]: ...


class LDAPSyncLockManager(Protocol):
    redis_cache: object | None

    async def acquire_lock(self, cronjob_id: str, ttl: int | None = None) -> bool | None: ...

    async def release_lock(self, cronjob_id: str) -> object: ...


class LDAPUserCache(Protocol):
    async def async_delete_cache(self, key: str) -> object: ...


class LDAPUserStore(Protocol):
    async def list_ldap_users(self) -> tuple[LiteLLM_UserTable, ...]: ...

    async def update_metadata(self, updates: Sequence["_LDAPTransition"]) -> None: ...


class _PrismaUserTable(Protocol):
    async def find_many(self, *, where: dict[str, object]) -> object: ...

    async def update(self, *, where: dict[str, str], data: dict[str, str]) -> object: ...


class _PrismaTransaction(Protocol):
    litellm_usertable: _PrismaUserTable


class _PrismaDatabase(Protocol):
    litellm_usertable: _PrismaUserTable

    def tx(self) -> AbstractAsyncContextManager[_PrismaTransaction]: ...


class _PrismaClient(Protocol):
    db: _PrismaDatabase


class PrismaLDAPUserStore:
    def __init__(self, prisma_client: object) -> None:
        self._prisma_client = cast(_PrismaClient, prisma_client)  # cast-ok: generated Prisma DB types are unavailable

    async def list_ldap_users(self) -> tuple[LiteLLM_UserTable, ...]:
        from prisma import (
            Json,  # pyright: ignore[reportUnknownVariableType]  # generated Prisma JSON wrapper is untyped
        )

        raw_users = await self._prisma_client.db.litellm_usertable.find_many(
            where={"metadata": {"path": ["auth_provider"], "equals": Json("ldap")}}
        )
        return _LDAP_USERS_ADAPTER.validate_python(raw_users)

    async def update_metadata(self, updates: Sequence["_LDAPTransition"]) -> None:
        async with self._prisma_client.db.tx() as transaction:
            for update in updates:
                await transaction.litellm_usertable.update(
                    where={"user_id": update.user_id},
                    data={"metadata": _metadata_string(update.metadata or {})},
                )


def _metadata_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return _METADATA_ADAPTER.validate_python(value)
    if isinstance(value, str):
        return _METADATA_ADAPTER.validate_json(value)
    return {}


def _metadata_string(metadata: dict[str, object]) -> str:
    return json.dumps(metadata)


def _first_entry_value(entry: _LDAPEntry, attribute_name: str) -> str | None:
    attribute = cast(  # cast-ok: ldap3 entry attributes are runtime-defined
        _LDAPEntryAttribute | None,
        getattr(entry, attribute_name, None),
    )
    if attribute is None:
        return None
    values = _ENTRY_VALUES_ADAPTER.validate_python(attribute.values)
    return str(values[0]) if values else None


def _principal_hash(entry: _LDAPEntry, config: LDAPConfig) -> str:
    dn = str(entry.entry_dn)
    principal = _first_entry_value(entry, config.ldap_user_id_attribute) if config.ldap_user_id_attribute else dn
    normalized = (principal or dn).strip().casefold()
    return sha256(normalized.encode("utf-8")).hexdigest()


def _search(
    connection: _LDAPStatusConnection,
    search_base: str,
    search_filter: str,
    search_scope: object,
    attributes: Sequence[str],
    missing_is_empty: bool = False,
) -> Sequence[_LDAPEntry]:
    search_succeeded = connection.search(
        search_base=search_base,
        search_filter=search_filter,
        search_scope=search_scope,
        attributes=attributes,
        size_limit=1,
    )
    if not search_succeeded:
        result = _LDAP_RESULT_ADAPTER.validate_python(connection.result)
        if missing_is_empty and result.get("result") == 32:
            return ()
        raise RuntimeError("LDAP search failed")
    return connection.entries


def _resolve_entry(
    connection: _LDAPStatusConnection,
    config: LDAPConfig,
    user: LiteLLM_UserTable,
    base_scope: object,
    subtree_scope: object,
    escape_filter_chars: Callable[[str], str],
) -> LDAPMissing | LDAPUnknown | _LDAPEntry:
    metadata = _metadata_dict(
        user.metadata  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # generated JSON field is untyped
    )
    stored_dn = metadata.get("ldap_dn")
    if isinstance(stored_dn, str) and stored_dn:
        entries = _search(connection, stored_dn, "(objectClass=*)", base_scope, (), missing_is_empty=True)
        if entries:
            return entries[0]

    username = metadata.get("ldap_username")
    if not isinstance(username, str) or not username:
        return LDAPMissing(tag="missing", user_id=user.user_id)
    search_base = config.ldap_search_base or config.ldap_base_dn
    if search_base is None:
        return LDAPUnknown(tag="unknown", user_id=user.user_id, reason="missing_search_base")
    search_filter = config.ldap_user_search_filter.replace("{username}", escape_filter_chars(username))
    attributes = (config.ldap_user_id_attribute,) if config.ldap_user_id_attribute else ()
    entries = _search(connection, search_base, search_filter, subtree_scope, attributes)
    if not entries:
        return LDAPMissing(tag="missing", user_id=user.user_id)
    stored_principal_hash = metadata.get("ldap_principal_hash")
    if not isinstance(stored_principal_hash, str) or _principal_hash(entries[0], config) != stored_principal_hash:
        return LDAPUnknown(tag="unknown", user_id=user.user_id, reason="principal_mismatch")
    return entries[0]


def _collect_ldap_user_statuses(
    config: LDAPConfig,
    users: Sequence[LiteLLM_UserTable],
) -> tuple[LDAPStatusOutcome, ...]:
    if not config.ldap_url or not config.ldap_base_dn or not config.ldap_access_filter:
        raise ValueError("LDAP URL, base DN, and access filter are required for user status synchronization")
    if not config.ldap_allow_insecure and not config.ldap_use_ssl and not config.ldap_start_tls:
        raise ValueError("LDAP user status synchronization requires SSL or StartTLS")
    try:
        from ldap3 import BASE, NONE, SUBTREE, Connection, Server
        from ldap3.core.exceptions import (
            LDAPException,  # pyright: ignore[reportMissingModuleSource]  # ldap3 is untyped
        )
        from ldap3.utils.conv import escape_filter_chars
    except ImportError as error:
        raise RuntimeError("LDAP user status synchronization requires ldap3") from error

    server = Server(config.ldap_url, get_info=NONE, use_ssl=config.ldap_use_ssl)
    connection = cast(  # cast-ok: ldap3 is untyped; access is constrained by _LDAPStatusConnection
        _LDAPStatusConnection,
        Connection(
            server,
            user=config.ldap_bind_dn,
            password=config.ldap_bind_password,
            auto_bind=False,
        ),
    )
    try:
        if config.ldap_start_tls and not connection.start_tls():
            raise RuntimeError("LDAP StartTLS failed")
        if not connection.bind():
            raise RuntimeError("LDAP service account bind failed")
        outcomes = tuple(
            _status_for_user(
                connection=connection,
                config=config,
                user=user,
                base_scope=BASE,
                subtree_scope=SUBTREE,
                escape_filter_chars=escape_filter_chars,
            )
            for user in users
        )
    except (LDAPException, RuntimeError) as error:
        raise ldap_service_unavailable_error("user status synchronization", config.ldap_url, error) from error
    finally:
        try:
            connection.unbind()
        except LDAPException as error:
            verbose_proxy_logger.debug("LDAP user status sync cleanup failed: %s", type(error).__name__)
    return outcomes


def _status_for_user(
    connection: _LDAPStatusConnection,
    config: LDAPConfig,
    user: LiteLLM_UserTable,
    base_scope: object,
    subtree_scope: object,
    escape_filter_chars: Callable[[str], str],
) -> LDAPStatusOutcome:
    resolved = _resolve_entry(connection, config, user, base_scope, subtree_scope, escape_filter_chars)
    if isinstance(resolved, (LDAPMissing, LDAPUnknown)):
        return resolved
    dn = str(resolved.entry_dn)
    access_entries = _search(connection, dn, config.ldap_access_filter or "", base_scope, ())
    if access_entries:
        return LDAPActive(tag="active", user_id=user.user_id, dn=dn)
    return LDAPInactive(tag="inactive", user_id=user.user_id, dn=dn)


async def collect_ldap_user_statuses(
    config: LDAPConfig,
    users: Sequence[LiteLLM_UserTable],
) -> tuple[LDAPStatusOutcome, ...]:
    return await run_in_threadpool(_collect_ldap_user_statuses, config, users)


class LDAPUserStatusSyncService:
    def __init__(
        self,
        user_store: LDAPUserStore,
        user_api_key_cache: LDAPUserCache | None = None,
        collector: LDAPStatusCollector = collect_ldap_user_statuses,
    ) -> None:
        self._user_store = user_store
        self._user_api_key_cache = user_api_key_cache
        self._collector = collector

    async def sync(
        self,
        config: LDAPConfig,
        dry_run: bool,
        trigger: LDAPSyncTrigger,
    ) -> LDAPUserStatusSyncResult:
        if not config.ldap_access_filter:
            return LDAPUserStatusSyncResult(
                dry_run=dry_run,
                trigger=trigger,
                aborted=True,
                abort_reason="ldap_access_filter is not configured",
            )
        typed_users = await self._user_store.list_ldap_users()
        try:
            outcomes = await self._collector(config, typed_users)
        except (ProxyException, RuntimeError, TypeError, ValueError, ValidationError) as error:
            verbose_proxy_logger.error("LDAP user status sync aborted: %s", type(error).__name__)
            return LDAPUserStatusSyncResult(
                dry_run=dry_run,
                trigger=trigger,
                scanned=len(typed_users),
                aborted=True,
                abort_reason="LDAP directory query failed",
            )

        users_by_id = {user.user_id: user for user in typed_users}
        transitions = tuple(
            _build_transition(users_by_id[outcome.user_id], outcome, config)
            for outcome in outcomes
            if outcome.user_id in users_by_id
        )
        deactivation_count = sum(1 for transition in transitions if transition.deactivates)
        deactivation_ratio = deactivation_count / len(typed_users) if typed_users else 0
        summary = _summarize_sync(
            dry_run=dry_run,
            trigger=trigger,
            outcomes=outcomes,
            transitions=transitions,
        )
        if deactivation_ratio > config.ldap_sync_max_deactivation_ratio:
            return summary.model_copy(
                update={
                    "aborted": True,
                    "abort_reason": (
                        f"deactivation ratio {deactivation_ratio:.3f} exceeds configured maximum "
                        f"{config.ldap_sync_max_deactivation_ratio:.3f}"
                    ),
                }
            )
        if dry_run:
            return summary

        writable_transitions = tuple(transition for transition in transitions if transition.metadata is not None)
        await self._user_store.update_metadata(writable_transitions)
        if self._user_api_key_cache is not None:
            for transition in writable_transitions:
                await self._user_api_key_cache.async_delete_cache(transition.user_id)
        verbose_proxy_logger.info("LDAP user status sync completed: %s", summary.model_dump_json())
        return summary


class LDAPUserStatusSyncManager:
    def __init__(
        self,
        prisma_client: PrismaClient,
        user_api_key_cache: LDAPUserCache | None = None,
        pod_lock_manager: LDAPSyncLockManager | None = None,
    ) -> None:
        self._prisma_client = prisma_client
        self._pod_lock_manager = pod_lock_manager
        self._service = LDAPUserStatusSyncService(
            user_store=PrismaLDAPUserStore(prisma_client),
            user_api_key_cache=user_api_key_cache,
        )

    async def run(
        self,
        dry_run: bool,
        trigger: LDAPSyncTrigger,
    ) -> LDAPUserStatusSyncResult:
        try:
            config = await load_ldap_config(self._prisma_client)
        except Exception as error:  # noqa: BLE001  # config loading crosses generated Prisma and decryption boundaries
            verbose_proxy_logger.error("LDAP user status sync configuration failed: %s", type(error).__name__)
            return LDAPUserStatusSyncResult(
                dry_run=dry_run,
                trigger=trigger,
                aborted=True,
                abort_reason="LDAP synchronization configuration is invalid",
            )
        if trigger != "manual" and not config.ldap_sync_enabled:
            return LDAPUserStatusSyncResult(
                dry_run=dry_run,
                trigger=trigger,
                aborted=True,
                abort_reason="LDAP user status synchronization is disabled",
            )
        lock_acquired = False
        if self._pod_lock_manager is not None and self._pod_lock_manager.redis_cache is not None:
            lock_acquired = bool(
                await self._pod_lock_manager.acquire_lock(
                    cronjob_id=LDAP_USER_STATUS_SYNC_JOB_NAME,
                    ttl=config.ldap_sync_lock_ttl_seconds,
                )
            )
            if not lock_acquired:
                return LDAPUserStatusSyncResult(
                    dry_run=dry_run,
                    trigger=trigger,
                    aborted=True,
                    abort_reason="another LDAP user status synchronization is running",
                )
        else:
            verbose_proxy_logger.warning(
                "LDAP user status sync is running without Redis coordination; each proxy pod may execute the job"
            )
        try:
            return await self._service.sync(config=config, dry_run=dry_run, trigger=trigger)
        finally:
            if lock_acquired and self._pod_lock_manager is not None:
                await self._pod_lock_manager.release_lock(cronjob_id=LDAP_USER_STATUS_SYNC_JOB_NAME)

    async def run_scheduled_sync(self) -> LDAPUserStatusSyncResult:
        return await self.run(dry_run=False, trigger="scheduled")

    async def run_startup_sync(self) -> LDAPUserStatusSyncResult:
        return await self.run(dry_run=False, trigger="startup")


@dataclass(frozen=True, slots=True)
class _LDAPTransition:
    user_id: str
    metadata: dict[str, object] | None
    activates: bool
    deactivates: bool


def _build_transition(
    user: LiteLLM_UserTable,
    outcome: LDAPStatusOutcome,
    config: LDAPConfig,
) -> _LDAPTransition:
    metadata = _metadata_dict(
        user.metadata  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # generated JSON field is untyped
    )
    was_active = metadata.get("identity_active") is not False
    checked_at = datetime.now(timezone.utc).isoformat()
    match outcome:
        case LDAPActive(dn=dn):
            updated = {
                **metadata,
                "ldap_dn": dn,
                "identity_active": True,
                "identity_status": "active",
                "identity_status_reason": "ldap_sync_verified",
                "identity_status_checked_at": checked_at,
            }
            return _LDAPTransition(user.user_id, updated, activates=not was_active, deactivates=False)
        case LDAPInactive(dn=dn):
            updated = {
                **metadata,
                "ldap_dn": dn,
                "identity_active": False,
                "identity_status": "inactive",
                "identity_status_reason": "ldap_access_filter_mismatch",
                "identity_status_checked_at": checked_at,
            }
            return _LDAPTransition(user.user_id, updated, activates=False, deactivates=was_active)
        case LDAPMissing():
            if config.ldap_missing_user_action == "ignore":
                return _LDAPTransition(user.user_id, None, activates=False, deactivates=False)
            updated = {
                **metadata,
                "identity_active": False,
                "identity_status": "inactive",
                "identity_status_reason": "ldap_user_missing",
                "identity_status_checked_at": checked_at,
            }
            return _LDAPTransition(user.user_id, updated, activates=False, deactivates=was_active)
        case LDAPUnknown():
            return _LDAPTransition(user.user_id, None, activates=False, deactivates=False)


def _summarize_sync(
    dry_run: bool,
    trigger: LDAPSyncTrigger,
    outcomes: Sequence[LDAPStatusOutcome],
    transitions: Sequence[_LDAPTransition],
) -> LDAPUserStatusSyncResult:
    activated = sum(1 for transition in transitions if transition.activates)
    deactivated = sum(1 for transition in transitions if transition.deactivates)
    return LDAPUserStatusSyncResult(
        dry_run=dry_run,
        trigger=trigger,
        scanned=len(outcomes),
        activated=0 if dry_run else activated,
        deactivated=0 if dry_run else deactivated,
        would_activate=activated if dry_run else 0,
        would_deactivate=deactivated if dry_run else 0,
        missing=sum(1 for outcome in outcomes if isinstance(outcome, LDAPMissing)),
        unknown=sum(1 for outcome in outcomes if isinstance(outcome, LDAPUnknown)),
        unchanged=sum(1 for transition in transitions if not transition.activates and not transition.deactivates),
    )
