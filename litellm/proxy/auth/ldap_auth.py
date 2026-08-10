import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_serializer, model_validator
from starlette.concurrency import run_in_threadpool

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import LiteLLM_UserTable, LitellmUserRoles, ProxyErrorTypes, ProxyException
from litellm.proxy.management_helpers.utils import (
    get_new_internal_user_defaults,  # pyright: ignore[reportUnknownVariableType]  # legacy helper lacks a concrete return type
)
from litellm.proxy.utils import PrismaClient
from litellm.repositories.base_repository import SupportsDict, SupportsModelDump
from litellm.repositories.config_repository import ConfigRepository
from litellm.repositories.user_repository import UserRepository
from litellm.types.utils import LiteLLMPydanticObjectBase

LDAP_SETTINGS_PARAM_NAME = "ldap_settings"
LDAP_SENSITIVE_FIELDS = frozenset({"ldap_bind_password"})
LDAPMissingUserAction = Literal["ignore", "disable"]
_LDAP_MISSING_USER_ACTION_ADAPTER: TypeAdapter[LDAPMissingUserAction] = TypeAdapter(LDAPMissingUserAction)
_LDAP_SETTINGS_ADAPTER: TypeAdapter[Mapping[str, object]] = TypeAdapter(Mapping[str, object])
_LDAP_SETTINGS_DICT_ADAPTER = TypeAdapter(dict[str, object])
_LDAP_USER_RECORD_DICT_ADAPTER = TypeAdapter(dict[str, object])
_LDAP_METADATA_ADAPTER: TypeAdapter[Mapping[str, object]] = TypeAdapter(Mapping[str, object])
_LDAP_ENTRY_VALUES_ADAPTER: TypeAdapter[tuple[object, ...]] = TypeAdapter(tuple[object, ...])
_LDAP_PATH_LIST_ADAPTER = TypeAdapter(list[str])
_DB_RECORD_PAIRS_ADAPTER: TypeAdapter[tuple[tuple[str, object], ...]] = TypeAdapter(tuple[tuple[str, object], ...])


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True, populate_by_name=True)


class _LDAPConfigRecord(_FrozenModel):
    param_value: object | None = None


class _InternalUserDefaults(_FrozenModel):
    models: tuple[str, ...]
    user_id: str
    user_email: str | None = None
    user_role: str | None = None
    max_budget: float | None = None
    budget_duration: str | None = None


class _LDAPUserMetadata(_FrozenModel):
    model_config = ConfigDict(extra="allow", frozen=True, from_attributes=True)

    auth_provider: Literal["ldap"]
    ldap_username: str
    ldap_dn: str
    ldap_principal_hash: str
    identity_active: bool
    identity_status: Literal["active"]
    identity_status_reason: Literal["login_verified"]
    identity_status_checked_at: str


class _UserIdWhere(_FrozenModel):
    user_id: str


class _ConfigParamWhere(_FrozenModel):
    param_name: str


class _LDAPMetadataPathPredicate(_FrozenModel):
    path: tuple[str, ...]
    equals: object

    @field_serializer("path")
    def serialize_path(self, value: tuple[str, ...]) -> Sequence[str]:
        return _LDAP_PATH_LIST_ADAPTER.validate_python(value)


class _LDAPMetadataFilter(_FrozenModel):
    metadata: _LDAPMetadataPathPredicate


class _LDAPIdentityWhere(_FrozenModel):
    filters: tuple[_LDAPMetadataFilter, ...] = Field(alias="OR")

    @field_serializer("filters")
    def serialize_filters(self, value: tuple[_LDAPMetadataFilter, ...]) -> Sequence[_LDAPMetadataFilter]:
        return TypeAdapter(list[_LDAPMetadataFilter]).validate_python(value)


class _LDAPUserCreateData(_FrozenModel):
    models: tuple[str, ...]
    user_id: str
    user_email: str | None = None
    user_role: str | None = None
    max_budget: float | None = None
    budget_duration: str | None = None
    user_alias: str
    metadata: str


class _LDAPUserUpdateData(_FrozenModel):
    user_alias: str
    metadata: str
    user_role: LitellmUserRoles | None = None
    user_email: str | None = None


class _LDAPUserUpsertData(_FrozenModel):
    create: _LDAPUserCreateData
    update: _LDAPUserUpdateData


@runtime_checkable
class _LDAPConnection(Protocol):
    @property
    def entries(self) -> Sequence[object]: ...

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


@runtime_checkable
class _LDAPTLSConnection(Protocol):
    def start_tls(self) -> bool: ...


@runtime_checkable
class _LDAPEntry(Protocol):
    @property
    def entry_dn(self) -> object: ...


@runtime_checkable
class _LDAPAttributeValues(Protocol):
    @property
    def values(self) -> object: ...


@runtime_checkable
class _LDAPAttributeValue(Protocol):
    @property
    def value(self) -> object: ...


class _ConfigTable(Protocol):
    async def find_unique(self, *, where: Mapping[str, object]) -> object | None: ...


@runtime_checkable
class _ConfigRepositoryBoundary(Protocol):
    @property
    def table(self) -> _ConfigTable: ...


class _UserTable(Protocol):
    async def find_unique(self, *, where: Mapping[str, object]) -> object | None: ...

    async def find_first(self, *, where: Mapping[str, object]) -> object | None: ...

    async def upsert(
        self,
        *,
        where: Mapping[str, object],
        data: Mapping[str, object],
    ) -> object | None: ...


@runtime_checkable
class _UserRepositoryBoundary(Protocol):
    @property
    def table(self) -> _UserTable: ...


def _config_table(repository: object) -> _ConfigTable:
    if not isinstance(repository, _ConfigRepositoryBoundary):
        raise TypeError("LDAP configuration repository does not expose a table")
    return repository.table


def _user_table(repository: object) -> _UserTable:
    if not isinstance(repository, _UserRepositoryBoundary):
        raise TypeError("LDAP user repository does not expose a table")
    return repository.table


def _ldap_connection(value: object) -> _LDAPConnection:
    if not isinstance(value, _LDAPConnection):
        raise TypeError("LDAP client returned an incompatible connection")
    return value


def _start_tls(connection: _LDAPConnection) -> bool:
    if not isinstance(connection, _LDAPTLSConnection):
        raise TypeError("LDAP connection does not support StartTLS")
    return connection.start_tls()


def _record_mapping(record: object) -> Mapping[str, object]:
    if isinstance(record, Mapping):
        return _LDAP_METADATA_ADAPTER.validate_python(record)
    if isinstance(record, SupportsModelDump):
        return _LDAP_METADATA_ADAPTER.validate_python(record.model_dump())
    if isinstance(record, SupportsDict):
        return _LDAP_METADATA_ADAPTER.validate_python(record.dict())
    pairs = _DB_RECORD_PAIRS_ADAPTER.validate_python(record)
    return MappingProxyType({key: value for key, value in pairs})


def ldap_service_unavailable_error(stage: str, ldap_url: str, error: BaseException) -> ProxyException:
    try:
        parsed_url = urlsplit(ldap_url if "://" in ldap_url else f"ldap://{ldap_url}")
        target = parsed_url.hostname or "configured LDAP server"
    except ValueError:
        target = "configured LDAP server"
    verbose_proxy_logger.warning("LDAP %s connection failed for %s: %s", stage, target, type(error).__name__)
    return ProxyException(
        message="LDAP directory service is temporarily unavailable.",
        type=ProxyErrorTypes.auth_error,
        param="ldap_connection",
        code=503,
    )


class LDAPConfig(LiteLLMPydanticObjectBase):
    ldap_enabled: bool = Field(default=False, description="Enable LDAP login for the Admin UI")
    ldap_url: str | None = Field(default=None, description="LDAP server URL, for example ldap://host:389")
    ldap_base_dn: str | None = Field(default=None, description="Base DN used to search for users")
    ldap_search_base: str | None = Field(default=None, description="Optional search base. Defaults to base DN")
    ldap_bind_dn: str | None = Field(default=None, description="Service account DN used to search users")
    ldap_bind_password: str | None = Field(default=None, description="Service account password")
    ldap_user_search_filter: str = Field(
        default="(|(uid={username})(sAMAccountName={username})(userPrincipalName={username}))",
        description="LDAP user search filter. The {username} placeholder is escaped before use",
    )
    ldap_access_filter: str | None = Field(
        default=None,
        description="Optional LDAP filter that eligible users must match, evaluated against the user entry",
    )
    ldap_email_attribute: str = Field(default="mail", description="LDAP attribute used as the LiteLLM user email")
    ldap_user_id_attribute: str | None = Field(
        default=None,
        description="Immutable LDAP attribute used as the LiteLLM identity, for example objectGUID or entryUUID",
    )
    ldap_display_name_attribute: str = Field(
        default="displayName",
        description="LDAP attribute used as the LiteLLM user display name",
    )
    ldap_group_attribute: str = Field(default="memberOf", description="LDAP attribute containing user group DNs")
    ldap_admin_group_dn: str | None = Field(
        default=None,
        description="LDAP group DN whose members should become LiteLLM proxy admins",
    )
    ldap_use_ssl: bool = Field(default=False, description="Connect to LDAP with SSL")
    ldap_start_tls: bool = Field(default=False, description="Upgrade LDAP connection with StartTLS before bind")
    ldap_allow_insecure: bool = Field(
        default=False,
        description="Allow LDAP bind without SSL or StartTLS. Not recommended outside isolated development environments",
    )
    ldap_sync_enabled: bool = Field(default=False, description="Periodically synchronize LDAP user access status")
    ldap_sync_interval_seconds: int = Field(
        default=300,
        ge=60,
        description="LDAP user status synchronization interval in seconds",
    )
    ldap_sync_run_on_startup: bool = Field(
        default=False,
        description="Run LDAP user status synchronization once when the proxy starts",
    )
    ldap_sync_lock_ttl_seconds: int = Field(
        default=900,
        gt=0,
        description="Redis lock lifetime for a single LDAP synchronization run",
    )
    ldap_missing_user_action: LDAPMissingUserAction = Field(
        default="ignore",
        description="Action when a previously synchronized LDAP user no longer exists",
    )
    ldap_sync_max_deactivation_ratio: float = Field(
        default=0.2,
        ge=0,
        le=1,
        description="Abort synchronization when the deactivation ratio exceeds this value",
    )

    @model_validator(mode="after")
    def validate_sync_configuration(self) -> "LDAPConfig":
        if self.ldap_sync_enabled and not self.ldap_access_filter:
            raise ValueError("ldap_access_filter is required when LDAP user status synchronization is enabled")
        return self


@dataclass(frozen=True, slots=True)
class LDAPDirectoryUser:
    username: str
    dn: str
    email: str | None
    display_name: str | None
    principal_id: str | None = None
    groups: tuple[str, ...] = ()
    user_role: LitellmUserRoles = LitellmUserRoles.INTERNAL_USER

    @property
    def principal_hash(self) -> str:
        normalized_principal = (self.principal_id or self.dn).strip().casefold()
        return sha256(normalized_principal.encode("utf-8")).hexdigest()

    @property
    def user_id(self) -> str:
        return f"ldap:{self.principal_hash}"

    @property
    def legacy_user_id(self) -> str:
        identifier = self.email or self.username
        return f"ldap:{identifier.casefold()}"


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in frozenset({"1", "true", "yes", "on"})


def _load_ldap_config_from_env() -> LDAPConfig:
    ldap_url = os.getenv("LDAP_URL")
    return LDAPConfig(
        ldap_enabled=_parse_bool(os.getenv("LDAP_ENABLED"), default=bool(ldap_url)),
        ldap_url=ldap_url,
        ldap_base_dn=os.getenv("LDAP_BASE_DN"),
        ldap_search_base=os.getenv("LDAP_SEARCH_BASE"),
        ldap_bind_dn=os.getenv("LDAP_BIND_DN") or os.getenv("LDAP_BIND_USER"),
        ldap_bind_password=os.getenv("LDAP_BIND_PASSWORD"),
        ldap_user_search_filter=os.getenv(
            "LDAP_USER_SEARCH_FILTER",
            "(|(uid={username})(sAMAccountName={username})(userPrincipalName={username}))",
        ),
        ldap_access_filter=os.getenv("LDAP_ACCESS_FILTER"),
        ldap_email_attribute=os.getenv("LDAP_EMAIL_ATTRIBUTE", "mail"),
        ldap_user_id_attribute=os.getenv("LDAP_USER_ID_ATTRIBUTE"),
        ldap_display_name_attribute=os.getenv("LDAP_DISPLAY_NAME_ATTRIBUTE", "displayName"),
        ldap_group_attribute=os.getenv("LDAP_GROUP_ATTRIBUTE", "memberOf"),
        ldap_admin_group_dn=os.getenv("LDAP_ADMIN_GROUP_DN"),
        ldap_use_ssl=_parse_bool(os.getenv("LDAP_USE_SSL"), default=False),
        ldap_start_tls=_parse_bool(os.getenv("LDAP_START_TLS"), default=False),
        ldap_allow_insecure=_parse_bool(os.getenv("LDAP_ALLOW_INSECURE"), default=False),
        ldap_sync_enabled=_parse_bool(os.getenv("LDAP_SYNC_ENABLED"), default=False),
        ldap_sync_interval_seconds=int(os.getenv("LDAP_SYNC_INTERVAL_SECONDS", "300")),
        ldap_sync_run_on_startup=_parse_bool(os.getenv("LDAP_SYNC_RUN_ON_STARTUP"), default=False),
        ldap_sync_lock_ttl_seconds=int(os.getenv("LDAP_SYNC_LOCK_TTL_SECONDS", "900")),
        ldap_missing_user_action=_LDAP_MISSING_USER_ACTION_ADAPTER.validate_python(
            os.getenv("LDAP_MISSING_USER_ACTION", "ignore")
        ),
        ldap_sync_max_deactivation_ratio=float(os.getenv("LDAP_SYNC_MAX_DEACTIVATION_RATIO", "0.2")),
    )


def _parse_db_param_value(param_value: object) -> Mapping[str, object]:
    if param_value is None:
        return MappingProxyType({})
    if isinstance(param_value, str):
        return _LDAP_SETTINGS_ADAPTER.validate_json(param_value)
    return _LDAP_SETTINGS_ADAPTER.validate_python(param_value)


def _parse_user_metadata(value: object) -> Mapping[str, object]:
    try:
        if isinstance(value, str):
            return _LDAP_METADATA_ADAPTER.validate_json(value)
        return _LDAP_METADATA_ADAPTER.validate_python(value or MappingProxyType({}))
    except (TypeError, ValueError, ValidationError):
        return MappingProxyType({})


async def load_ldap_config(prisma_client: PrismaClient | None) -> LDAPConfig:
    if prisma_client is None:
        return _load_ldap_config_from_env()

    repository: object = ConfigRepository(prisma_client)
    record = await _config_table(repository).find_unique(
        where=_ConfigParamWhere(param_name=LDAP_SETTINGS_PARAM_NAME).model_dump(mode="json")
    )
    if record is None:
        return _load_ldap_config_from_env()
    config_record = _LDAPConfigRecord.model_validate(record)
    if not config_record.param_value:
        return _load_ldap_config_from_env()

    settings = _parse_db_param_value(config_record.param_value)
    from litellm.proxy.proxy_server import proxy_config

    decrypted_settings: object = proxy_config._decrypt_db_variables(  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownVariableType]  # legacy decryptor is untyped and intentionally avoids mutating the environment
        _LDAP_SETTINGS_DICT_ADAPTER.validate_python(settings)
    )
    return LDAPConfig.model_validate(decrypted_settings)


async def is_ldap_configured(prisma_client: PrismaClient | None = None) -> bool:
    config = await load_ldap_config(prisma_client)
    return is_ldap_config_enabled(config)


def is_ldap_config_enabled(config: LDAPConfig) -> bool:
    return bool(config.ldap_enabled and config.ldap_url and config.ldap_base_dn)


def _entry_values(entry: object, attribute_name: str) -> tuple[str, ...]:
    attribute: object = getattr(entry, attribute_name, None)  # pyright: ignore[reportAny]  # ldap3 exposes schema-defined attributes dynamically
    if attribute is None:
        return ()
    if isinstance(attribute, _LDAPAttributeValues):
        raw_values = attribute.values
        if isinstance(raw_values, (list, tuple, set)):
            values = _LDAP_ENTRY_VALUES_ADAPTER.validate_python(raw_values)
            return tuple(str(value) for value in values if value is not None)
        if raw_values is not None:
            return (str(raw_values),)
    if isinstance(attribute, _LDAPAttributeValue):
        value = attribute.value
        return (str(value),) if value is not None else ()
    return ()


def _entry_first_value(entry: object, attribute_name: str) -> str | None:
    values = _entry_values(entry, attribute_name)
    return values[0] if values else None


def _ldap_user_role(groups: Sequence[str], admin_group_dn: str | None) -> LitellmUserRoles:
    if not admin_group_dn:
        return LitellmUserRoles.INTERNAL_USER
    normalized_groups = frozenset(group.strip().casefold() for group in groups)
    if admin_group_dn.strip().casefold() in normalized_groups:
        return LitellmUserRoles.PROXY_ADMIN
    return LitellmUserRoles.INTERNAL_USER


def _bind_ldap_directory(connection: _LDAPConnection, config: LDAPConfig) -> None:
    if config.ldap_start_tls and not _start_tls(connection):
        raise ProxyException(
            message="LDAP directory connection could not establish StartTLS.",
            type=ProxyErrorTypes.auth_error,
            param="ldap_start_tls",
            code=503,
        )
    if not connection.bind():
        raise ProxyException(
            message="LDAP directory service account authentication failed.",
            type=ProxyErrorTypes.auth_error,
            param="ldap_bind_dn",
            code=503,
        )


def _find_ldap_directory_user(
    connection: _LDAPConnection,
    config: LDAPConfig,
    username: str,
    subtree_scope: object,
    base_scope: object,
    escape_filter_chars: Callable[[str], str],
) -> tuple[str, str | None, str | None, tuple[str, ...], str | None] | None:
    search_filter = config.ldap_user_search_filter.replace("{username}", escape_filter_chars(username))
    attributes = tuple(
        frozenset(
            attribute
            for attribute in (
                config.ldap_email_attribute,
                config.ldap_display_name_attribute,
                config.ldap_group_attribute,
                config.ldap_user_id_attribute,
            )
            if attribute is not None
        )
    )
    if (
        not connection.search(
            search_base=config.ldap_search_base or config.ldap_base_dn or "",
            search_filter=search_filter,
            search_scope=subtree_scope,
            attributes=attributes,
            size_limit=1,
        )
        or not connection.entries
    ):
        return None
    entry = connection.entries[0]
    if not isinstance(entry, _LDAPEntry):
        raise TypeError("LDAP search result does not expose an entry DN")
    user_dn = str(entry.entry_dn)
    if config.ldap_access_filter and (
        not connection.search(
            search_base=user_dn,
            search_filter=config.ldap_access_filter,
            search_scope=base_scope,
            attributes=(),
            size_limit=1,
        )
        or not connection.entries
    ):
        return None
    email = _entry_first_value(entry, config.ldap_email_attribute)
    display_name = _entry_first_value(entry, config.ldap_display_name_attribute)
    groups = _entry_values(entry, config.ldap_group_attribute)
    principal_id = (
        _entry_first_value(entry, config.ldap_user_id_attribute) if config.ldap_user_id_attribute else user_dn
    )
    return user_dn, email, display_name, groups, principal_id


def _bind_ldap_user(connection: _LDAPConnection, config: LDAPConfig) -> bool:
    if config.ldap_start_tls and not _start_tls(connection):
        raise ProxyException(
            message="LDAP user connection could not establish StartTLS.",
            type=ProxyErrorTypes.auth_error,
            param="ldap_start_tls",
            code=503,
        )
    return connection.bind()


def _authenticate_ldap_credentials(
    config: LDAPConfig,
    username: str,
    password: str,
) -> LDAPDirectoryUser | None:
    if not config.ldap_allow_insecure and not config.ldap_use_ssl and not config.ldap_start_tls:
        raise ProxyException(
            message="LDAP authentication requires SSL or StartTLS unless insecure LDAP is explicitly enabled.",
            type=ProxyErrorTypes.auth_error,
            param="ldap_allow_insecure",
            code=400,
        )
    try:
        from ldap3 import BASE, NONE, SUBTREE, Connection, Server
        from ldap3.core.exceptions import (
            LDAPException,  # pyright: ignore[reportMissingModuleSource]  # ldap3 is untyped
        )
        from ldap3.utils.conv import escape_filter_chars
    except ImportError as e:
        raise ProxyException(
            message="LDAP login requires the `ldap3` package. Install LiteLLM proxy with ldap3 support.",
            type=ProxyErrorTypes.auth_error,
            param="ldap3",
            code=500,
        ) from e

    def safe_unbind(connection: _LDAPConnection, stage: str) -> None:
        try:
            connection.unbind()
        except LDAPException as e:
            verbose_proxy_logger.debug("LDAP %s connection cleanup failed: %s", stage, type(e).__name__)

    if not config.ldap_url or not config.ldap_base_dn:
        return None

    server = Server(config.ldap_url, get_info=NONE, use_ssl=config.ldap_use_ssl)
    bind_conn = _ldap_connection(
        Connection(
            server,
            user=config.ldap_bind_dn,
            password=config.ldap_bind_password,
            auto_bind=False,
        )
    )
    try:
        _bind_ldap_directory(bind_conn, config)
        directory_user = _find_ldap_directory_user(
            connection=bind_conn,
            config=config,
            username=username,
            subtree_scope=SUBTREE,
            base_scope=BASE,
            escape_filter_chars=escape_filter_chars,
        )
        if directory_user is None:
            return None
        user_dn, email, display_name, groups, principal_id = directory_user
    except LDAPException as e:
        raise ldap_service_unavailable_error("directory lookup", config.ldap_url, e) from e
    finally:
        safe_unbind(bind_conn, "directory lookup")

    user_conn = _ldap_connection(Connection(server, user=user_dn, password=password, auto_bind=False))
    try:
        if not _bind_ldap_user(user_conn, config):
            return None
    except LDAPException as e:
        raise ldap_service_unavailable_error("user authentication", config.ldap_url, e) from e
    finally:
        safe_unbind(user_conn, "user authentication")

    return LDAPDirectoryUser(
        username=username,
        dn=user_dn,
        email=email,
        display_name=display_name,
        principal_id=principal_id,
        groups=groups,
        user_role=_ldap_user_role(groups, config.ldap_admin_group_dn),
    )


def _prisma_json(value: str) -> object:
    from prisma import Json  # pyright: ignore[reportUnknownVariableType]  # generated Prisma JSON wrapper is untyped

    json_value: object = Json(value)
    return json_value


async def _resolve_ldap_user_id(
    user_repository: object,
    directory_user: LDAPDirectoryUser,
) -> str:
    user_table = _user_table(user_repository)
    stable_user = await user_table.find_unique(
        where=_UserIdWhere(user_id=directory_user.user_id).model_dump(mode="json")
    )
    if stable_user is not None:
        return str(_record_mapping(stable_user)["user_id"])

    legacy_user = await user_table.find_unique(
        where=_UserIdWhere(user_id=directory_user.legacy_user_id).model_dump(mode="json")
    )
    if legacy_user is not None:
        return str(_record_mapping(legacy_user)["user_id"])

    metadata_where = _LDAPIdentityWhere(
        OR=(
            _LDAPMetadataFilter(
                metadata=_LDAPMetadataPathPredicate(
                    path=("ldap_principal_hash",),
                    equals=_prisma_json(directory_user.principal_hash),
                )
            ),
            _LDAPMetadataFilter(
                metadata=_LDAPMetadataPathPredicate(
                    path=("ldap_dn",),
                    equals=_prisma_json(directory_user.dn),
                )
            ),
        )
    )
    metadata_user = await user_table.find_first(where=metadata_where.model_dump(mode="python", by_alias=True))
    if metadata_user is not None:
        return str(_record_mapping(metadata_user)["user_id"])
    return directory_user.user_id


def _ldap_metadata(directory_user: LDAPDirectoryUser, existing_metadata: Mapping[str, object]) -> str:
    checked_at = datetime.now(timezone.utc).isoformat()
    metadata = _LDAPUserMetadata.model_validate(
        MappingProxyType(
            {
                **existing_metadata,
                "auth_provider": "ldap",
                "ldap_username": directory_user.username,
                "ldap_dn": directory_user.dn,
                "ldap_principal_hash": directory_user.principal_hash,
                "identity_active": True,
                "identity_status": "active",
                "identity_status_reason": "login_verified",
                "identity_status_checked_at": checked_at,
            }
        )
    )
    return json.dumps(metadata.model_dump())


def _create_ldap_user_data(
    directory_user: LDAPDirectoryUser,
    resolved_user_id: str,
    ldap_metadata: str,
    sync_user_role: bool,
) -> _LDAPUserCreateData:
    raw_defaults: object = get_new_internal_user_defaults(  # pyright: ignore[reportUnknownVariableType]  # the result is validated immediately into a frozen model
        user_id=resolved_user_id,
        user_email=directory_user.email,
    )
    defaults = _InternalUserDefaults.model_validate(raw_defaults)
    return _LDAPUserCreateData(
        models=defaults.models,
        user_id=defaults.user_id,
        user_email=defaults.user_email,
        user_role=directory_user.user_role if sync_user_role else defaults.user_role,
        max_budget=defaults.max_budget,
        budget_duration=defaults.budget_duration,
        user_alias=directory_user.display_name or directory_user.username,
        metadata=ldap_metadata,
    )


def _user_model_from_record(record: object, fallback: _LDAPUserCreateData) -> LiteLLM_UserTable:
    if isinstance(record, LiteLLM_UserTable):
        return record
    row = _record_mapping(record)
    metadata_value = row.get("metadata")
    normalized_row = (
        MappingProxyType({**row, "metadata": _parse_user_metadata(metadata_value)})
        if isinstance(metadata_value, str)
        else row
    )
    return LiteLLM_UserTable.model_validate(
        _LDAP_USER_RECORD_DICT_ADAPTER.validate_python(
            normalized_row or fallback.model_dump(mode="json", exclude_none=True)
        )
    )


async def _sync_ldap_user(
    prisma_client: PrismaClient,
    directory_user: LDAPDirectoryUser,
    sync_user_role: bool = False,
) -> LiteLLM_UserTable:
    repository: object = UserRepository(prisma_client)
    user_table = _user_table(repository)
    resolved_user_id = await _resolve_ldap_user_id(repository, directory_user)
    existing_user = await user_table.find_unique(where=_UserIdWhere(user_id=resolved_user_id).model_dump(mode="json"))
    existing_metadata: Mapping[str, object] = (
        _parse_user_metadata(_record_mapping(existing_user).get("metadata"))
        if existing_user is not None
        else MappingProxyType({})
    )
    ldap_metadata = _ldap_metadata(directory_user, existing_metadata)
    create_data = _create_ldap_user_data(directory_user, resolved_user_id, ldap_metadata, sync_user_role)
    update_data = _LDAPUserUpdateData(
        user_alias=directory_user.display_name or directory_user.username,
        metadata=ldap_metadata,
        user_role=directory_user.user_role if sync_user_role else None,
        user_email=directory_user.email,
    )
    upsert_data = _LDAPUserUpsertData(create=create_data, update=update_data)
    row = await user_table.upsert(
        where=_UserIdWhere(user_id=resolved_user_id).model_dump(mode="json"),
        data=upsert_data.model_dump(mode="json", exclude_none=True),
    )
    if row is None:
        return LiteLLM_UserTable.model_validate(create_data.model_dump(mode="json", exclude_none=True))
    return _user_model_from_record(row, create_data)


async def authenticate_ldap_user(
    username: str,
    password: str,
    prisma_client: PrismaClient | None,
) -> LiteLLM_UserTable:
    if prisma_client is None:
        raise ProxyException(
            message="Database connection is required for LDAP login.",
            type=ProxyErrorTypes.auth_error,
            param="DATABASE_URL",
            code=500,
        )
    if not password:
        raise ProxyException(
            message="Invalid LDAP credentials.",
            type=ProxyErrorTypes.auth_error,
            param="invalid_credentials",
            code=401,
        )

    config = await load_ldap_config(prisma_client)
    if not is_ldap_config_enabled(config):
        raise ProxyException(
            message="LDAP login is not configured.",
            type=ProxyErrorTypes.auth_error,
            param="ldap_settings",
            code=401,
        )

    directory_user = await run_in_threadpool(_authenticate_ldap_credentials, config, username, password)
    if directory_user is None:
        raise ProxyException(
            message="Invalid LDAP credentials.",
            type=ProxyErrorTypes.auth_error,
            param="invalid_credentials",
            code=401,
        )

    return await _sync_ldap_user(
        prisma_client=prisma_client,
        directory_user=directory_user,
        sync_user_role=bool(config.ldap_admin_group_dn),
    )
