import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Sequence
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import LiteLLM_UserTable, LitellmUserRoles, ProxyErrorTypes, ProxyException
from litellm.proxy.management_helpers.utils import get_new_internal_user_defaults
from litellm.proxy.utils import PrismaClient
from litellm.repositories.config_repository import ConfigRepository
from litellm.repositories.user_repository import UserRepository
from litellm.types.utils import LiteLLMPydanticObjectBase

LDAP_SETTINGS_PARAM_NAME = "ldap_settings"
LDAP_SENSITIVE_FIELDS = {"ldap_bind_password"}
_LDAP_MISSING_USER_ACTION_ADAPTER = TypeAdapter(Literal["ignore", "disable"])
_LDAP_METADATA_ADAPTER = TypeAdapter(dict[str, object])


class _LDAPConnection(Protocol):
    entries: Sequence[object]

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
    ldap_url: Optional[str] = Field(default=None, description="LDAP server URL, for example ldap://host:389")
    ldap_base_dn: Optional[str] = Field(default=None, description="Base DN used to search for users")
    ldap_search_base: Optional[str] = Field(default=None, description="Optional search base. Defaults to base DN")
    ldap_bind_dn: Optional[str] = Field(default=None, description="Service account DN used to search users")
    ldap_bind_password: Optional[str] = Field(default=None, description="Service account password")
    ldap_user_search_filter: str = Field(
        default="(|(uid={username})(sAMAccountName={username})(userPrincipalName={username}))",
        description="LDAP user search filter. The {username} placeholder is escaped before use",
    )
    ldap_access_filter: str | None = Field(
        default=None,
        description="Optional LDAP filter that eligible users must match, evaluated against the user entry",
    )
    ldap_email_attribute: str = Field(default="mail", description="LDAP attribute used as the LiteLLM user email")
    ldap_user_id_attribute: Optional[str] = Field(
        default=None,
        description="Immutable LDAP attribute used as the LiteLLM identity, for example objectGUID or entryUUID",
    )
    ldap_display_name_attribute: str = Field(
        default="displayName",
        description="LDAP attribute used as the LiteLLM user display name",
    )
    ldap_group_attribute: str = Field(default="memberOf", description="LDAP attribute containing user group DNs")
    ldap_admin_group_dn: Optional[str] = Field(
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
    ldap_missing_user_action: Literal["ignore", "disable"] = Field(
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


@dataclass
class LDAPDirectoryUser:
    username: str
    dn: str
    email: Optional[str]
    display_name: Optional[str]
    principal_id: Optional[str] = None
    groups: List[str] = field(default_factory=list)
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


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


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


def _parse_db_param_value(param_value: Any) -> Dict[str, Any]:
    if param_value is None:
        return {}
    if isinstance(param_value, str):
        return json.loads(param_value)
    return dict(param_value)


def _parse_user_metadata(value: object) -> dict[str, object]:
    try:
        if isinstance(value, str):
            return _LDAP_METADATA_ADAPTER.validate_json(value)
        return _LDAP_METADATA_ADAPTER.validate_python(value or {})
    except (TypeError, ValueError, ValidationError):
        return {}


async def load_ldap_config(prisma_client: Optional[PrismaClient]) -> LDAPConfig:
    if prisma_client is None:
        return _load_ldap_config_from_env()

    record = await ConfigRepository(prisma_client).table.find_unique(where={"param_name": LDAP_SETTINGS_PARAM_NAME})
    if record is None or not getattr(record, "param_value", None):
        return _load_ldap_config_from_env()

    settings = _parse_db_param_value(record.param_value)
    from litellm.proxy.proxy_server import proxy_config

    decrypted_settings = proxy_config._decrypt_db_variables(settings)
    return LDAPConfig(**decrypted_settings)


async def is_ldap_configured(prisma_client: Optional[PrismaClient] = None) -> bool:
    config = await load_ldap_config(prisma_client)
    return is_ldap_config_enabled(config)


def is_ldap_config_enabled(config: LDAPConfig) -> bool:
    return bool(config.ldap_enabled and config.ldap_url and config.ldap_base_dn)


def _entry_values(entry: Any, attribute_name: str) -> List[str]:
    attr = getattr(entry, attribute_name, None)
    if attr is None:
        return []
    values = getattr(attr, "values", None)
    if values is None:
        value = getattr(attr, "value", None)
        return [str(value)] if value is not None else []
    if isinstance(values, (list, tuple, set)):
        return [str(value) for value in values if value is not None]
    return [str(values)]


def _entry_first_value(entry: Any, attribute_name: str) -> Optional[str]:
    values = _entry_values(entry, attribute_name)
    return values[0] if values else None


def _ldap_user_role(groups: List[str], admin_group_dn: str | None) -> LitellmUserRoles:
    if not admin_group_dn:
        return LitellmUserRoles.INTERNAL_USER
    normalized_groups = frozenset(group.strip().casefold() for group in groups)
    if admin_group_dn.strip().casefold() in normalized_groups:
        return LitellmUserRoles.PROXY_ADMIN
    return LitellmUserRoles.INTERNAL_USER


def _bind_ldap_directory(connection: _LDAPConnection, config: LDAPConfig) -> None:
    if config.ldap_start_tls and not connection.start_tls():
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
) -> tuple[str, str | None, str | None, List[str], str | None] | None:
    search_filter = config.ldap_user_search_filter.replace("{username}", escape_filter_chars(username))
    attributes = list(
        {
            config.ldap_email_attribute,
            config.ldap_display_name_attribute,
            config.ldap_group_attribute,
            *([config.ldap_user_id_attribute] if config.ldap_user_id_attribute else []),
        }
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
    user_dn = str(getattr(entry, "entry_dn"))
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
    if config.ldap_start_tls and not connection.start_tls():
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
) -> Optional[LDAPDirectoryUser]:
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
    bind_conn = Connection(
        server,
        user=config.ldap_bind_dn,
        password=config.ldap_bind_password,
        auto_bind=False,
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

    user_conn = Connection(server, user=user_dn, password=password, auto_bind=False)
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


async def _resolve_ldap_user_id(
    user_repository: UserRepository,
    directory_user: LDAPDirectoryUser,
) -> str:
    from prisma import Json  # pyright: ignore[reportUnknownVariableType]  # generated Prisma JSON wrapper is untyped

    stable_user = await user_repository.table.find_unique(where={"user_id": directory_user.user_id})
    if stable_user is not None:
        return str(getattr(stable_user, "user_id", None) or stable_user["user_id"])

    legacy_user = await user_repository.table.find_unique(where={"user_id": directory_user.legacy_user_id})
    if legacy_user is not None:
        return str(getattr(legacy_user, "user_id", None) or legacy_user["user_id"])

    metadata_user = await user_repository.table.find_first(
        where={
            "OR": [
                {"metadata": {"path": ["ldap_principal_hash"], "equals": Json(directory_user.principal_hash)}},
                {"metadata": {"path": ["ldap_dn"], "equals": Json(directory_user.dn)}},
            ]
        }
    )
    if metadata_user is not None:
        return str(getattr(metadata_user, "user_id", None) or metadata_user["user_id"])
    return directory_user.user_id


async def _sync_ldap_user(
    prisma_client: PrismaClient,
    directory_user: LDAPDirectoryUser,
    sync_user_role: bool = False,
) -> LiteLLM_UserTable:
    user_repository = UserRepository(prisma_client)
    resolved_user_id = await _resolve_ldap_user_id(user_repository, directory_user)
    existing_user = await user_repository.table.find_unique(where={"user_id": resolved_user_id})
    existing_metadata_value = getattr(existing_user, "metadata", None) if existing_user is not None else None
    if isinstance(existing_user, dict):
        existing_metadata_value = existing_user.get("metadata")
    existing_metadata = _parse_user_metadata(existing_metadata_value)
    checked_at = datetime.now(timezone.utc).isoformat()
    ldap_metadata = json.dumps(
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
    create_data = get_new_internal_user_defaults(
        user_id=resolved_user_id,
        user_email=directory_user.email,
    )
    if sync_user_role:
        create_data["user_role"] = directory_user.user_role
    create_data["user_alias"] = directory_user.display_name or directory_user.username
    create_data["metadata"] = ldap_metadata

    update_data: Dict[str, Any] = {
        "user_alias": directory_user.display_name or directory_user.username,
        "metadata": ldap_metadata,
    }
    if sync_user_role:
        update_data["user_role"] = directory_user.user_role
    if directory_user.email is not None:
        update_data["user_email"] = directory_user.email

    row = await user_repository.table.upsert(
        where={"user_id": resolved_user_id},
        data={
            "create": create_data,
            "update": update_data,
        },
    )
    if isinstance(row, LiteLLM_UserTable):
        return row
    user_model = user_repository._to_model(row)
    if user_model is not None:
        return user_model
    return LiteLLM_UserTable(**create_data)


async def authenticate_ldap_user(
    username: str,
    password: str,
    prisma_client: Optional[PrismaClient],
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
