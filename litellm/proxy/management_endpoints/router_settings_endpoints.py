"""
ROUTER SETTINGS MANAGEMENT

Endpoints for accessing router configuration and metadata

GET /router/settings - Get router configuration including available routing strategies
GET /router/fields - Get router settings field definitions without values (for UI rendering)
"""

import asyncio
import inspect
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, get_args

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.repositories.config_repository import ConfigRepository
from litellm.router import Router
from litellm.types.management_endpoints import (
    ROUTER_SETTINGS_FIELDS,
    ROUTING_STRATEGY_DESCRIPTIONS,
    RouterSettingsField,
)
from litellm.types.router import RouterModelGroupAliasItem

if TYPE_CHECKING:
    from litellm.proxy.utils import PrismaClient

router = APIRouter()

ModelGroupAliasMap = Mapping[str, str | RouterModelGroupAliasItem]
_EMPTY_ROUTER_SETTINGS: Mapping[str, object] = MappingProxyType({})
_EMPTY_MODEL_GROUP_ALIASES: ModelGroupAliasMap = MappingProxyType({})
_ROUTER_SETTINGS_ADAPTER: TypeAdapter[Mapping[str, object]] = TypeAdapter(Mapping[str, object])
_MODEL_GROUP_ALIAS_ADAPTER: TypeAdapter[ModelGroupAliasMap] = TypeAdapter(ModelGroupAliasMap)
_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_ROUTER_SETTINGS_TAGS = TypeAdapter(list[str]).validate_python(("Router Settings",))


class ModelGroupAliasItemResponse(BaseModel):
    name: str
    model: str
    hidden: bool
    enabled: bool


class ModelGroupAliasListResponse(BaseModel):
    aliases: tuple[ModelGroupAliasItemResponse, ...]
    can_edit: bool


class ModelGroupAliasCreateRequest(BaseModel):
    name: str
    model: str
    hidden: bool = False
    enabled: bool = True


class ModelGroupAliasUpdateRequest(BaseModel):
    model: str | None = None
    hidden: bool | None = None
    enabled: bool | None = None


class ModelGroupAliasResponse(BaseModel):
    name: str
    model: str
    hidden: bool
    enabled: bool
    message: str


class ModelGroupAliasDeleteResponse(BaseModel):
    name: str
    message: str


class ModelGroupAliasErrorResponse(BaseModel):
    error: str


def _is_proxy_admin(user_api_key_dict: UserAPIKeyAuth) -> bool:
    return user_api_key_dict.user_role == LitellmUserRoles.PROXY_ADMIN


def _normalize_model_group_alias_value(
    name: str,
    value: str | RouterModelGroupAliasItem,
) -> ModelGroupAliasItemResponse:
    if isinstance(value, str):
        return ModelGroupAliasItemResponse(
            name=name,
            model=value,
            hidden=False,
            enabled=True,
        )

    return ModelGroupAliasItemResponse(
        name=name,
        model=value.get("model", ""),
        hidden=bool(value.get("hidden", False)),
        enabled=bool(value.get("enabled", True)),
    )


def _normalize_model_group_alias_map(
    aliases: ModelGroupAliasMap,
) -> tuple[ModelGroupAliasItemResponse, ...]:
    return tuple(_normalize_model_group_alias_value(name, value) for name, value in aliases.items())


async def _read_router_settings_section(prisma_client: "PrismaClient") -> Mapping[str, object]:
    row = await ConfigRepository(prisma_client).get_param("router_settings")
    if row is None or row.param_value is None:
        return _EMPTY_ROUTER_SETTINGS
    return _ROUTER_SETTINGS_ADAPTER.validate_python(row.param_value)


async def _upsert_router_settings_section(prisma_client: "PrismaClient", value: Mapping[str, object]) -> None:
    await ConfigRepository(prisma_client).set_param(
        "router_settings",
        _json_router_settings(value),
    )
    from litellm.proxy.utils import invalidate_config_param

    await invalidate_config_param("router_settings")


def _get_model_group_aliases(router_settings: Mapping[str, object]) -> ModelGroupAliasMap:
    try:
        return _MODEL_GROUP_ALIAS_ADAPTER.validate_python(
            router_settings.get("model_group_alias", _EMPTY_MODEL_GROUP_ALIASES)
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid model_group_alias configuration") from exc


def _error_detail(message: str) -> Mapping[str, str]:
    return ModelGroupAliasErrorResponse(error=message).model_dump()


def _json_router_settings(value: Mapping[str, object]) -> JsonValue:
    return _JSON_VALUE_ADAPTER.validate_python(_ROUTER_SETTINGS_ADAPTER.validate_python(value))


def _validate_alias_target(router: Router, alias_name: str, target_model: str) -> None:
    if not target_model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_detail("Target model group is required"),
        )
    if target_model == alias_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_detail("Alias cannot point to itself"),
        )
    if target_model in router.model_group_alias:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_detail("Alias cannot point to another alias"),
        )
    if target_model not in router.model_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_detail(f"Target model group '{target_model}' not found"),
        )


class RouterSettingsResponse(BaseModel):
    fields: list[RouterSettingsField] = Field(description="List of all configurable router settings with metadata")
    current_values: dict[str, Any] = Field(description="Current values of router settings")
    routing_strategy_descriptions: dict[str, str] = Field(description="Descriptions for each routing strategy option")


class RouterFieldsResponse(BaseModel):
    fields: list[RouterSettingsField] = Field(
        description="List of all configurable router settings with metadata (without field values)"
    )
    routing_strategy_descriptions: dict[str, str] = Field(description="Descriptions for each routing strategy option")


def _get_routing_strategies_from_router_class() -> list[str]:
    """
    Dynamically extract routing strategies from the Router class __init__ method.
    """
    # Get the __init__ signature
    sig = inspect.signature(Router.__init__)

    # Get the routing_strategy parameter
    routing_strategy_param = sig.parameters.get("routing_strategy")

    if routing_strategy_param and routing_strategy_param.annotation:
        # Extract Literal values using get_args
        literal_values = get_args(routing_strategy_param.annotation)
        if literal_values:
            return list(literal_values)

    raise ValueError("Unable to extract routing strategies from Router class")


@router.get(
    "/router/settings",
    tags=["Router Settings"],
    dependencies=[Depends(user_api_key_auth)],
    response_model=RouterSettingsResponse,
)
async def get_router_settings(
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    Get router configuration and available settings.

    Returns:
    - fields: List of all configurable router settings with their metadata (type, description, default, options)
              The routing_strategy field includes available options extracted from the Router class
    - current_values: Current values of router settings from config
    """
    from litellm.proxy.proxy_server import llm_router, proxy_config

    try:
        # Get available routing strategies dynamically from Router class
        available_routing_strategies = _get_routing_strategies_from_router_class()

        # Get router settings fields from types file
        router_fields = [field.model_copy(deep=True) for field in ROUTER_SETTINGS_FIELDS]

        # Populate routing_strategy field with available options and descriptions
        for field in router_fields:
            if field.field_name == "routing_strategy":
                field.options = available_routing_strategies
                break

        # Try to get router settings from config
        config = await proxy_config.get_config()
        router_settings_from_config = config.get("router_settings", {})

        current_values: dict[str, Any] = {}
        if llm_router is not None:
            # Router exposes routing groups as private `_routing_groups`; the
            # generic `hasattr` loop below would miss them.
            current_values["routing_groups"] = [group.model_dump() for group in llm_router._routing_groups.values()]
            for field in router_fields:
                if field.field_name == "routing_groups":
                    continue
                if hasattr(llm_router, field.field_name):
                    value = getattr(llm_router, field.field_name)
                    current_values[field.field_name] = value

        # Merge with config values (config takes precedence)
        current_values.update(router_settings_from_config)

        # Update field values with current values
        for field in router_fields:
            if field.field_name in current_values:
                field.field_value = current_values[field.field_name]

        return RouterSettingsResponse(
            fields=router_fields,
            current_values=current_values,
            routing_strategy_descriptions=ROUTING_STRATEGY_DESCRIPTIONS,
        )
    except Exception as e:
        verbose_proxy_logger.error(f"Error fetching router settings: {e!s}")
        raise


@router.get(
    "/router/fields",
    tags=["Router Settings"],
    dependencies=[Depends(user_api_key_auth)],
    response_model=RouterFieldsResponse,
)
async def get_router_fields(
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    Get router settings field definitions without values.

    Returns only the field metadata (type, description, default, options) without
    populating field_value. This is useful for UI components that need to know
    what fields to render, but will get the actual values from a different endpoint.

    Returns:
    - fields: List of all configurable router settings with their metadata (type, description, default, options)
              The routing_strategy field includes available options extracted from the Router class
              Note: field_value will be None for all fields
    - routing_strategy_descriptions: Descriptions for each routing strategy option
    """
    try:
        # Get available routing strategies dynamically from Router class
        available_routing_strategies = _get_routing_strategies_from_router_class()

        # Get router settings fields from types file
        router_fields = [field.model_copy(deep=True) for field in ROUTER_SETTINGS_FIELDS]

        # Populate routing_strategy field with available options
        for field in router_fields:
            if field.field_name == "routing_strategy":
                field.options = available_routing_strategies
                break

        # Ensure field_value is None for all fields (don't populate values)
        for field in router_fields:
            field.field_value = None

        return RouterFieldsResponse(
            fields=router_fields,
            routing_strategy_descriptions=ROUTING_STRATEGY_DESCRIPTIONS,
        )
    except Exception as e:
        verbose_proxy_logger.error(f"Error fetching router fields: {e!s}")
        raise


@router.get(
    "/router/model-group-aliases",
    tags=_ROUTER_SETTINGS_TAGS,
    response_model=ModelGroupAliasListResponse,
)
async def list_model_group_aliases(
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> ModelGroupAliasListResponse:
    from litellm.proxy.proxy_server import llm_router

    if llm_router is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error_detail("Router not initialized"),
        )

    aliases = _normalize_model_group_alias_map(llm_router.model_group_alias)
    return ModelGroupAliasListResponse(
        aliases=aliases,
        can_edit=_is_proxy_admin(user_api_key_dict),
    )


@router.post(
    "/router/model-group-aliases",
    tags=_ROUTER_SETTINGS_TAGS,
    response_model=ModelGroupAliasResponse,
)
async def create_model_group_alias(
    data: ModelGroupAliasCreateRequest,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> ModelGroupAliasResponse:
    from litellm.proxy.proxy_server import llm_router, prisma_client, proxy_config, proxy_logging_obj

    if not _is_proxy_admin(user_api_key_dict):
        raise HTTPException(status_code=403, detail="Only proxy admins can update model group aliases")
    if prisma_client is None:
        raise HTTPException(status_code=400, detail="No DB Connected")
    if llm_router is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error_detail("Router not initialized"),
        )

    alias_name = data.name.strip()
    target_model = data.model.strip()

    if not alias_name:
        raise HTTPException(status_code=400, detail="Alias name is required")
    if alias_name in llm_router.model_names:
        raise HTTPException(status_code=400, detail="Alias name cannot match an existing model group")
    if alias_name in llm_router.model_group_alias:
        raise HTTPException(status_code=400, detail="Alias already exists")

    _validate_alias_target(llm_router, alias_name, target_model)

    existing_router_settings = await _read_router_settings_section(prisma_client)
    existing_aliases = _get_model_group_aliases(existing_router_settings)
    updated_aliases = _MODEL_GROUP_ALIAS_ADAPTER.validate_python(
        MappingProxyType(
            {
                **existing_aliases,
                alias_name: RouterModelGroupAliasItem(
                    model=target_model,
                    hidden=data.hidden,
                    enabled=data.enabled,
                ),
            }
        )
    )
    updated_router_settings = MappingProxyType({**existing_router_settings, "model_group_alias": updated_aliases})

    await _upsert_router_settings_section(prisma_client, updated_router_settings)
    llm_router.set_model_group_aliases(updated_aliases)
    await proxy_config.add_deployment(prisma_client=prisma_client, proxy_logging_obj=proxy_logging_obj)

    from litellm.proxy.proxy_server import create_config_audit_log

    asyncio.create_task(
        create_config_audit_log(
            "router_settings",
            "updated",
            _json_router_settings(existing_router_settings),
            _json_router_settings(updated_router_settings),
            user_api_key_dict,
        )
    )

    return ModelGroupAliasResponse(
        name=alias_name,
        model=target_model,
        hidden=data.hidden,
        enabled=data.enabled,
        message="Model group alias created successfully",
    )


@router.patch(
    "/router/model-group-aliases/{alias_name}",
    tags=_ROUTER_SETTINGS_TAGS,
    response_model=ModelGroupAliasResponse,
)
async def update_model_group_alias(
    alias_name: str,
    data: ModelGroupAliasUpdateRequest,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> ModelGroupAliasResponse:
    from litellm.proxy.proxy_server import llm_router, prisma_client, proxy_config, proxy_logging_obj

    if not _is_proxy_admin(user_api_key_dict):
        raise HTTPException(status_code=403, detail="Only proxy admins can update model group aliases")
    if prisma_client is None:
        raise HTTPException(status_code=400, detail="No DB Connected")
    if llm_router is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error_detail("Router not initialized"),
        )

    if alias_name not in llm_router.model_group_alias:
        raise HTTPException(status_code=404, detail="Alias not found")

    existing_router_settings = await _read_router_settings_section(prisma_client)
    existing_aliases = _get_model_group_aliases(existing_router_settings)
    if alias_name not in existing_aliases:
        raise HTTPException(status_code=404, detail="Alias not found")

    current_value = existing_aliases[alias_name]
    current_item = _normalize_model_group_alias_value(alias_name, current_value)

    update_fields = data.model_dump(exclude_unset=True)
    next_model = data.model.strip() if "model" in update_fields and data.model is not None else current_item.model
    next_hidden = data.hidden if "hidden" in update_fields and data.hidden is not None else current_item.hidden
    next_enabled = data.enabled if "enabled" in update_fields and data.enabled is not None else current_item.enabled

    if next_enabled:
        _validate_alias_target(llm_router, alias_name, next_model)

    updated_aliases = _MODEL_GROUP_ALIAS_ADAPTER.validate_python(
        MappingProxyType(
            {
                **existing_aliases,
                alias_name: RouterModelGroupAliasItem(
                    model=next_model,
                    hidden=next_hidden,
                    enabled=next_enabled,
                ),
            }
        )
    )
    updated_router_settings = MappingProxyType({**existing_router_settings, "model_group_alias": updated_aliases})

    await _upsert_router_settings_section(prisma_client, updated_router_settings)
    llm_router.set_model_group_aliases(updated_aliases)
    await proxy_config.add_deployment(prisma_client=prisma_client, proxy_logging_obj=proxy_logging_obj)

    from litellm.proxy.proxy_server import create_config_audit_log

    asyncio.create_task(
        create_config_audit_log(
            "router_settings",
            "updated",
            _json_router_settings(existing_router_settings),
            _json_router_settings(updated_router_settings),
            user_api_key_dict,
        )
    )

    return ModelGroupAliasResponse(
        name=alias_name,
        model=next_model,
        hidden=next_hidden,
        enabled=next_enabled,
        message="Model group alias updated successfully",
    )


@router.delete(
    "/router/model-group-aliases/{alias_name}",
    tags=_ROUTER_SETTINGS_TAGS,
    response_model=ModelGroupAliasDeleteResponse,
)
async def delete_model_group_alias(
    alias_name: str,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> ModelGroupAliasDeleteResponse:
    from litellm.proxy.proxy_server import llm_router, prisma_client, proxy_config, proxy_logging_obj

    if not _is_proxy_admin(user_api_key_dict):
        raise HTTPException(status_code=403, detail="Only proxy admins can update model group aliases")
    if prisma_client is None:
        raise HTTPException(status_code=400, detail="No DB Connected")
    if llm_router is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error_detail("Router not initialized"),
        )

    existing_router_settings = await _read_router_settings_section(prisma_client)
    existing_aliases = _get_model_group_aliases(existing_router_settings)
    if alias_name not in existing_aliases:
        raise HTTPException(status_code=404, detail="Alias not found")

    updated_aliases = _MODEL_GROUP_ALIAS_ADAPTER.validate_python(
        MappingProxyType({name: value for name, value in existing_aliases.items() if name != alias_name})
    )
    updated_router_settings = MappingProxyType({**existing_router_settings, "model_group_alias": updated_aliases})

    await _upsert_router_settings_section(prisma_client, updated_router_settings)
    llm_router.set_model_group_aliases(updated_aliases)
    await proxy_config.add_deployment(prisma_client=prisma_client, proxy_logging_obj=proxy_logging_obj)

    from litellm.proxy.proxy_server import create_config_audit_log

    asyncio.create_task(
        create_config_audit_log(
            "router_settings",
            "updated",
            _json_router_settings(existing_router_settings),
            _json_router_settings(updated_router_settings),
            user_api_key_dict,
        )
    )

    return ModelGroupAliasDeleteResponse(
        name=alias_name,
        message="Model group alias deleted successfully",
    )
