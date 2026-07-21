"""
CRUD endpoints for storing reusable credentials.
"""

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel

import litellm
from litellm._logging import verbose_proxy_logger
from litellm.litellm_core_utils.litellm_logging import _get_masked_values
from litellm.litellm_core_utils.url_utils import SSRFError, validate_url
from litellm.llms.chatgpt.device_authorization import ChatGPTDeviceAuthorizationProvider
from litellm.llms.github_copilot.device_authorization import GitHubCopilotDeviceAuthorizationProvider
from litellm.llms.xai.oauth import XAIAuthRecord, XAIOAuthAuthenticator, XAIOAuthError, parse_xai_auth_json
from litellm.proxy._types import CommonProxyErrors, LitellmUserRoles, UserAPIKeyAuth, hash_token
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.common_utils.timezone_utils import get_budget_reset_timezone, get_current_budget_date
from litellm.proxy.credential_endpoints.chatgpt_credential_utils import (
    async_refresh_chatgpt_credential_if_needed,
)
from litellm.proxy.credential_endpoints.chatgpt_quota_history import (
    ChatGPTQuotaHistoryDays,
    ChatGPTQuotaHistoryReader,
    ChatGPTQuotaHistoryResponse,
    ChatGPTQuotaHistoryTable,
    DatabaseChatGPTQuotaHistoryStore,
    merge_chatgpt_quota_snapshots,
)
from litellm.proxy.credential_endpoints.chatgpt_subscription import (
    ChatGPTDailyQuotaSnapshot,
    ChatGPTResetCreditConsumeRequest,
    ChatGPTResetCreditConsumeResponse,
    ChatGPTSubscriptionStatus,
    consume_chatgpt_rate_limit_reset_credit,
    get_chatgpt_access_token,
    get_chatgpt_account_id,
    get_chatgpt_daily_quota_snapshot,
    get_chatgpt_plan_label,
    is_chatgpt_credential,
    query_chatgpt_subscription_status,
)
from litellm.proxy.credential_endpoints.credential_writer import (
    CredentialConflict,
    CredentialNotFound,
    CredentialWriter,
)
from litellm.proxy.credential_endpoints.device_login_flow import (
    DeviceLoginFailed,
    DeviceLoginFlow,
    DeviceLoginPending,
    DeviceLoginProvider,
    DeviceLoginStateStore,
)
from litellm.proxy.credential_endpoints.device_login_state_store import (
    DatabaseDeviceLoginStateStore,
    DatabaseDeviceLoginStateTable,
    RedisDeviceLoginCache,
    RedisDeviceLoginStateStore,
)
from litellm.proxy.utils import PrismaClient, handle_exception_on_proxy
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.repositories.table_repositories import ChatGPTQuotaSnapshotRepository
from litellm.types.utils import CreateCredentialItem, CredentialItem

router = APIRouter()


class ChatGPTDeviceLoginStartRequest(BaseModel):
    credential_name: str
    api_base: str | None = None
    overwrite_existing: bool = False


class ChatGPTDeviceLoginPollRequest(BaseModel):
    login_id: str


class ChatGPTCredentialAuth(BaseModel):
    credential_name: str
    access_token: str
    account_id: str | None = None
    plan_label: str | None = None
    daily_snapshot: ChatGPTDailyQuotaSnapshot | None = None


class XAIOAuthImportRequest(BaseModel):
    credential_name: str
    auth_json: str
    overwrite_existing: bool = False


class XAIOAuthImportResponse(BaseModel):
    success: Literal[True]
    credential_name: str
    expires_at: int
    status: Literal["active"]


_XAI_AUTH_JSON_MAX_BYTES = 64 * 1024


def _require_proxy_admin(user_api_key_dict: UserAPIKeyAuth) -> None:
    if user_api_key_dict.user_role not in (LitellmUserRoles.PROXY_ADMIN, LitellmUserRoles.PROXY_ADMIN.value):
        raise HTTPException(status_code=403, detail="Proxy Admin role is required")


def _reject_managed_oauth_metadata(credential_info: dict[object, object]) -> None:
    if credential_info.get("auth_type") == "oauth_json_import":
        raise HTTPException(status_code=400, detail="Use the xAI OAuth import endpoint for managed credentials")


def _xai_credential_values(auth_record: XAIAuthRecord) -> dict[str, str]:
    return {
        "api_key": auth_record.access_token,
        "xai_oauth_refresh_token": auth_record.refresh_token,
        "xai_oauth_expires_at": str(auth_record.expires_at),
        "xai_oauth_token_endpoint": auth_record.token_endpoint,
    }


@router.post(
    "/credentials/xai/oauth/import",
    dependencies=[Depends(user_api_key_auth)],
    response_model=XAIOAuthImportResponse,
    tags=["credential management"],
)
async def import_xai_oauth_credential(
    body: XAIOAuthImportRequest,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
) -> XAIOAuthImportResponse:
    from litellm.proxy.proxy_server import prisma_client

    _require_proxy_admin(user_api_key_dict)
    if len(body.auth_json.encode("utf-8")) > _XAI_AUTH_JSON_MAX_BYTES:
        raise HTTPException(status_code=413, detail="xAI OAuth credential JSON exceeds 64 KiB")
    if prisma_client is None:
        raise HTTPException(status_code=500, detail={"error": CommonProxyErrors.db_not_connected_error.value})
    try:
        auth_record = parse_xai_auth_json(body.auth_json)
        if auth_record.expires_at <= int(time.time()):
            auth_record = await XAIOAuthAuthenticator().async_refresh_auth_record(auth_record)
    except XAIOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    credential = CredentialItem(
        credential_name=body.credential_name,
        credential_values=_xai_credential_values(auth_record),
        credential_info={"custom_llm_provider": "xai", "auth_type": "oauth_json_import"},
    )
    result = await CredentialWriter(CredentialsRepository(prisma_client)).save(
        credential=credential,
        actor_id=user_api_key_dict.user_id,
        overwrite_existing=body.overwrite_existing,
    )
    if isinstance(result, CredentialConflict):
        raise HTTPException(status_code=409, detail="Credential already exists")
    return XAIOAuthImportResponse(
        success=True,
        credential_name=body.credential_name,
        expires_at=auth_record.expires_at,
        status="active",
    )


@router.post(
    "/credentials",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def create_credential(
    request: Request,
    fastapi_response: Response,
    credential: CreateCredentialItem,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    [BETA] endpoint. This might change unexpectedly.
    Stores credential in DB.
    Reloads credentials in memory.
    """
    from litellm.proxy.proxy_server import llm_router, prisma_client

    try:
        _reject_managed_oauth_metadata(credential.credential_info)
        if prisma_client is None:
            raise HTTPException(
                status_code=500,
                detail={"error": CommonProxyErrors.db_not_connected_error.value},
            )
        if credential.model_id:
            if llm_router is None:
                raise HTTPException(
                    status_code=500,
                    detail="LLM router not found. Please ensure you have a valid router instance.",
                )
            # get model from router
            model = llm_router.get_deployment(credential.model_id)
            if model is None:
                raise HTTPException(status_code=404, detail="Model not found")
            credential_values = llm_router.get_deployment_credentials(credential.model_id)
            if credential_values is None:
                raise HTTPException(status_code=404, detail="Model not found")
            credential.credential_values = credential_values

        if credential.credential_values is None:
            raise HTTPException(
                status_code=400,
                detail="Credential values are required. Unable to infer credential values from model ID.",
            )
        processed_credential = CredentialItem(
            credential_name=credential.credential_name,
            credential_values=credential.credential_values,
            credential_info=credential.credential_info,
        )
        result = await CredentialWriter(CredentialsRepository(prisma_client)).save(
            credential=processed_credential,
            actor_id=user_api_key_dict.user_id,
            overwrite_existing=False,
        )
        if isinstance(result, CredentialConflict):
            raise HTTPException(status_code=409, detail="Credential already exists")

        return {"success": True, "message": "Credential created successfully"}
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


def _device_login_owner_id(user_api_key_dict: UserAPIKeyAuth) -> str:
    if user_api_key_dict.user_id:
        return user_api_key_dict.user_id
    if user_api_key_dict.key_alias:
        return user_api_key_dict.key_alias
    if user_api_key_dict.token:
        return hash_token(user_api_key_dict.token)
    raise HTTPException(status_code=401, detail="Unable to identify device login owner")


def _build_device_login_state_store(
    prisma_client: PrismaClient,
    redis_usage_cache: RedisDeviceLoginCache | None,
) -> DeviceLoginStateStore:
    if redis_usage_cache is not None:
        return RedisDeviceLoginStateStore(redis_usage_cache)
    table = prisma_client.writer_db.litellm_deviceloginstate
    if not isinstance(table, DatabaseDeviceLoginStateTable):
        raise TypeError("Prisma client device login state delegate has an invalid type")
    return DatabaseDeviceLoginStateStore(table)


def _build_device_login_flow() -> DeviceLoginFlow:
    from litellm.proxy.proxy_server import prisma_client, redis_usage_cache

    if prisma_client is None:
        raise HTTPException(status_code=500, detail={"error": CommonProxyErrors.db_not_connected_error.value})
    state_store = _build_device_login_state_store(prisma_client, redis_usage_cache)
    return DeviceLoginFlow(
        providers={
            "chatgpt": ChatGPTDeviceAuthorizationProvider(),
            "github_copilot": GitHubCopilotDeviceAuthorizationProvider(),
        },
        state_store=state_store,
        credential_writer=CredentialWriter(CredentialsRepository(prisma_client)),
    )


def _validate_device_login_request(body: ChatGPTDeviceLoginStartRequest, user_api_key_dict: UserAPIKeyAuth) -> None:
    if body.overwrite_existing and user_api_key_dict.user_role != LitellmUserRoles.PROXY_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role is required to overwrite a credential")
    if body.api_base is None:
        return
    try:
        validate_url(body.api_base)
    except SSRFError as exc:
        raise HTTPException(status_code=400, detail="Invalid or unsafe API base URL") from exc


async def _start_device_login(
    provider: DeviceLoginProvider,
    body: ChatGPTDeviceLoginStartRequest,
    user_api_key_dict: UserAPIKeyAuth,
):
    _validate_device_login_request(body, user_api_key_dict)
    result = await _build_device_login_flow().start(
        provider=provider,
        credential_name=body.credential_name,
        owner_id=_device_login_owner_id(user_api_key_dict),
        overwrite_existing=body.overwrite_existing,
        api_base=body.api_base,
    )
    if isinstance(result, DeviceLoginFailed):
        raise HTTPException(status_code=result.status_code, detail=result.detail)
    return {
        "success": True,
        "login_id": result.login_id,
        "verification_url": result.verification_url,
        "user_code": result.user_code,
        "interval": result.interval_seconds,
        "expires_at": result.expires_at,
    }


async def _poll_device_login(
    body: ChatGPTDeviceLoginPollRequest,
    user_api_key_dict: UserAPIKeyAuth,
):
    result = await _build_device_login_flow().poll(
        login_id=body.login_id,
        owner_id=_device_login_owner_id(user_api_key_dict),
    )
    if isinstance(result, DeviceLoginFailed):
        raise HTTPException(status_code=result.status_code, detail=result.detail)
    if isinstance(result, DeviceLoginPending):
        return {
            "success": True,
            "status": "pending",
            "interval": result.retry_after_seconds,
            "retry_after_seconds": result.retry_after_seconds,
            "expires_at": result.expires_at,
        }
    return {
        "success": True,
        "status": "complete",
        "credential_name": result.credential_name,
    }


@router.post(
    "/credentials/chatgpt/device/start",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def start_chatgpt_device_login(
    request: Request,
    fastapi_response: Response,
    body: ChatGPTDeviceLoginStartRequest,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    return await _start_device_login("chatgpt", body, user_api_key_dict)


@router.post(
    "/credentials/chatgpt/device/poll",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def poll_chatgpt_device_login(
    request: Request,
    fastapi_response: Response,
    body: ChatGPTDeviceLoginPollRequest,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    return await _poll_device_login(body, user_api_key_dict)


@router.post(
    "/credentials/github_copilot/device/start",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def start_github_copilot_device_login(
    request: Request,
    fastapi_response: Response,
    body: ChatGPTDeviceLoginStartRequest,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    return await _start_device_login("github_copilot", body, user_api_key_dict)


@router.post(
    "/credentials/github_copilot/device/poll",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def poll_github_copilot_device_login(
    request: Request,
    fastapi_response: Response,
    body: ChatGPTDeviceLoginPollRequest,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    return await _poll_device_login(body, user_api_key_dict)


def get_chatgpt_quota_history_store() -> ChatGPTQuotaHistoryReader | None:
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        return None
    table: object = ChatGPTQuotaSnapshotRepository(prisma_client).table
    if not isinstance(table, ChatGPTQuotaHistoryTable):
        raise RuntimeError("ChatGPT quota history table is unavailable")
    return DatabaseChatGPTQuotaHistoryStore(table)


@router.get(
    "/credentials",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def get_credentials(
    request: Request,
    fastapi_response: Response,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    [BETA] endpoint. This might change unexpectedly.
    """
    try:
        masked_credentials = [
            {
                "credential_name": credential.credential_name,
                "credential_values": _get_masked_values(credential.credential_values),
                "credential_info": credential.credential_info,
            }
            for credential in litellm.credential_list
        ]
        return {"success": True, "credentials": masked_credentials}
    except Exception as e:
        return handle_exception_on_proxy(e)


@router.get(
    "/credentials/by_name/{credential_name:path}",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
    response_model=CredentialItem,
)
async def get_credential_by_name(
    request: Request,
    fastapi_response: Response,
    credential_name: str = Path(..., description="The credential name, percent-decoded; may contain slashes"),
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    [BETA] endpoint. This might change unexpectedly.
    """
    try:
        for credential in litellm.credential_list:
            if credential.credential_name == credential_name:
                masked_credential = CredentialItem(
                    credential_name=credential.credential_name,
                    credential_values=_get_masked_values(
                        credential.credential_values,
                        unmasked_length=4,
                        number_of_asterisks=4,
                    ),
                    credential_info=credential.credential_info,
                )
                return masked_credential
        raise HTTPException(
            status_code=404,
            detail="Credential not found. Got credential name: " + credential_name,
        )
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


@router.get(
    "/credentials/by_model/{model_id}",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
    response_model=CredentialItem,
)
async def get_credential_by_model(
    request: Request,
    fastapi_response: Response,
    model_id: str = Path(..., description="The model ID to look up credentials for"),
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    [BETA] endpoint. This might change unexpectedly.
    """
    from litellm.proxy.proxy_server import llm_router

    try:
        if llm_router is None:
            raise HTTPException(status_code=500, detail="LLM router not found")
        model = llm_router.get_deployment(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found")
        credential_values = llm_router.get_deployment_credentials(model_id)
        if credential_values is None:
            raise HTTPException(status_code=404, detail="Model not found")
        masked_credential_values = _get_masked_values(
            credential_values,
            unmasked_length=4,
            number_of_asterisks=4,
        )
        credential = CredentialItem(
            credential_name="{}-credential-{}".format(model.model_name, model_id),
            credential_values=masked_credential_values,
            credential_info={},
        )
        return credential
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


@router.get(
    "/credentials/{credential_name:path}/chatgpt/subscription",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
    response_model=ChatGPTSubscriptionStatus,
)
async def get_chatgpt_credential_subscription(
    request: Request,
    fastapi_response: Response,
    credential_name: Annotated[
        str,
        Path(description="The ChatGPT credential name, percent-decoded; may contain slashes"),
    ],
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
):
    try:
        auth = await _get_chatgpt_credential_auth(credential_name, user_api_key_dict)
        return await query_chatgpt_subscription_status(
            credential_name=auth.credential_name,
            access_token=auth.access_token,
            account_id=auth.account_id,
            plan_label=auth.plan_label,
            daily_snapshot=auth.daily_snapshot,
        )
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


@router.get(
    "/credentials/{credential_name:path}/chatgpt/quota-history",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
    response_model=ChatGPTQuotaHistoryResponse,
)
async def get_chatgpt_credential_quota_history(
    request: Request,
    fastapi_response: Response,
    credential_name: Annotated[
        str,
        Path(description="The ChatGPT credential name, percent-decoded; may contain slashes"),
    ],
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    history_store: Annotated[ChatGPTQuotaHistoryReader | None, Depends(get_chatgpt_quota_history_store)],
    days: Annotated[ChatGPTQuotaHistoryDays, Query()] = ChatGPTQuotaHistoryDays.THIRTY,
) -> ChatGPTQuotaHistoryResponse:
    try:
        credential = _get_chatgpt_credential(credential_name)
        current_snapshot = get_chatgpt_daily_quota_snapshot(credential.credential_info or {})
        stored_snapshots = (
            await history_store.list(
                credential_name=credential_name,
                days=days,
                timezone_name=get_budget_reset_timezone(),
            )
            if history_store is not None
            else ()
        )
        timezone_name = get_budget_reset_timezone()
        return ChatGPTQuotaHistoryResponse(
            credential_name=credential_name,
            days=days,
            timezone=timezone_name,
            current_date=get_current_budget_date(),
            snapshots=list(merge_chatgpt_quota_snapshots(stored_snapshots, current_snapshot)),
        )
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


@router.post(
    "/credentials/{credential_name:path}/chatgpt/rate-limit-reset-credits/consume",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
    response_model=ChatGPTResetCreditConsumeResponse,
)
async def consume_chatgpt_credential_rate_limit_reset_credit(
    request: Request,
    fastapi_response: Response,
    credential_name: Annotated[
        str,
        Path(description="The ChatGPT credential name, percent-decoded; may contain slashes"),
    ],
    body: ChatGPTResetCreditConsumeRequest,
    user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
):
    try:
        auth = await _get_chatgpt_credential_auth(credential_name, user_api_key_dict)
        return await consume_chatgpt_rate_limit_reset_credit(
            credential_name=auth.credential_name,
            access_token=auth.access_token,
            account_id=auth.account_id,
            idempotency_key=body.idempotency_key,
            credit_id=body.credit_id,
        )
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


async def _get_chatgpt_credential_auth(
    credential_name: str,
    user_api_key_dict: UserAPIKeyAuth,
) -> ChatGPTCredentialAuth:
    credential = _get_chatgpt_credential(credential_name)

    credential_values = await async_refresh_chatgpt_credential_if_needed(
        credential=credential,
        user_id=user_api_key_dict.user_id,
    )
    access_token = get_chatgpt_access_token(credential_values)
    if access_token is None:
        raise HTTPException(status_code=400, detail="ChatGPT credential is missing an access token")

    daily_snapshot = get_chatgpt_daily_quota_snapshot(credential.credential_info or {})
    current_daily_snapshot = (
        daily_snapshot if daily_snapshot is not None and daily_snapshot.date == get_current_budget_date() else None
    )
    return ChatGPTCredentialAuth(
        credential_name=credential.credential_name,
        access_token=access_token,
        account_id=get_chatgpt_account_id(credential_values),
        plan_label=get_chatgpt_plan_label(credential_values),
        daily_snapshot=current_daily_snapshot,
    )


def _get_chatgpt_credential(credential_name: str) -> CredentialItem:
    credential = next(
        (credential for credential in litellm.credential_list if credential.credential_name == credential_name),
        None,
    )
    if credential is None:
        raise HTTPException(
            status_code=404,
            detail="Credential not found. Got credential name: " + credential_name,
        )
    if not is_chatgpt_credential(credential):
        raise HTTPException(status_code=400, detail="Credential is not a ChatGPT credential")
    return credential


@router.delete(
    "/credentials/{credential_name:path}",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def delete_credential(
    request: Request,
    fastapi_response: Response,
    credential_name: str = Path(..., description="The credential name, percent-decoded; may contain slashes"),
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    [BETA] endpoint. This might change unexpectedly.
    """
    from litellm.proxy.proxy_server import prisma_client

    try:
        if prisma_client is None:
            raise HTTPException(
                status_code=500,
                detail={"error": CommonProxyErrors.db_not_connected_error.value},
            )
        await CredentialWriter(CredentialsRepository(prisma_client)).delete(credential_name)
        return {"success": True, "message": "Credential deleted successfully"}
    except Exception as e:
        return handle_exception_on_proxy(e)


@router.patch(
    "/credentials/{credential_name:path}",
    dependencies=[Depends(user_api_key_auth)],
    tags=["credential management"],
)
async def update_credential(
    request: Request,
    fastapi_response: Response,
    credential: CredentialItem,
    credential_name: str = Path(..., description="The credential name, percent-decoded; may contain slashes"),
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    [BETA] endpoint. This might change unexpectedly.
    """
    from litellm.proxy.proxy_server import prisma_client

    try:
        _reject_managed_oauth_metadata(credential.credential_info)
        if prisma_client is None:
            raise HTTPException(
                status_code=500,
                detail={"error": CommonProxyErrors.db_not_connected_error.value},
            )
        result = await CredentialWriter(CredentialsRepository(prisma_client)).patch(
            credential_name=credential_name,
            patch=credential,
            actor_id=user_api_key_dict.user_id,
        )
        if isinstance(result, CredentialNotFound):
            raise HTTPException(status_code=404, detail="Credential not found in DB.")
        if isinstance(result, CredentialConflict):
            raise HTTPException(status_code=409, detail="Credential already exists")

        return {"success": True, "message": "Credential updated successfully"}
    except Exception as e:
        return handle_exception_on_proxy(e)
