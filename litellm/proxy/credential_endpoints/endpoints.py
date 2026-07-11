"""
CRUD endpoints for storing reusable credentials.
"""

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel

import litellm
from litellm._logging import verbose_proxy_logger
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.litellm_core_utils.litellm_logging import _get_masked_values
from litellm.llms.chatgpt.authenticator import DEVICE_CODE_TIMEOUT_SECONDS, Authenticator
from litellm.llms.chatgpt.common_utils import CHATGPT_DEVICE_VERIFY_URL, GetAccessTokenError, GetDeviceCodeError
from litellm.llms.github_copilot.authenticator import Authenticator as GitHubCopilotAuthenticator
from litellm.llms.github_copilot.common_utils import GetAccessTokenError as GitHubGetAccessTokenError
from litellm.llms.github_copilot.common_utils import GetDeviceCodeError as GitHubGetDeviceCodeError
from litellm.proxy._types import CommonProxyErrors, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.common_utils.encrypt_decrypt_utils import encrypt_value_helper
from litellm.proxy.credential_endpoints.chatgpt_credential_utils import build_chatgpt_credential_values
from litellm.proxy.utils import handle_exception_on_proxy, jsonify_object
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CreateCredentialItem, CredentialItem

router = APIRouter()


class ChatGPTDeviceLoginStartRequest(BaseModel):
    credential_name: str
    api_base: Optional[str] = None
    overwrite_existing: bool = False


class ChatGPTDeviceLoginPollRequest(BaseModel):
    login_id: str


class ChatGPTDeviceLoginState(BaseModel):
    credential_name: str
    api_base: Optional[str] = None
    overwrite_existing: bool = False
    user_id: Optional[str] = None
    device_auth_id: str
    user_code: str
    interval: int
    expires_at: float


_chatgpt_device_login_flows: dict[str, ChatGPTDeviceLoginState] = {}


class GitHubCopilotDeviceLoginState(BaseModel):
    credential_name: str
    overwrite_existing: bool = False
    user_id: Optional[str] = None
    device_code: str
    interval: int
    expires_at: float


_github_copilot_device_login_flows: dict[str, GitHubCopilotDeviceLoginState] = {}


class CredentialHelperUtils:
    @staticmethod
    def encrypt_credential_values(
        credential: CredentialItem, new_encryption_key: Optional[str] = None
    ) -> CredentialItem:
        """Encrypt values in credential.credential_values and add to DB"""
        encrypted_credential_values = {}
        for key, value in (credential.credential_values or {}).items():
            encrypted_credential_values[key] = encrypt_value_helper(value, new_encryption_key)

        # Return a new object to avoid mutating the caller's credential, which
        # is kept in memory and should remain unencrypted.
        return CredentialItem(
            credential_name=credential.credential_name,
            credential_values=encrypted_credential_values,
            credential_info=credential.credential_info or {},
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
        encrypted_credential = CredentialHelperUtils.encrypt_credential_values(processed_credential)
        credentials_dict = encrypted_credential.model_dump()
        credentials_dict_jsonified = jsonify_object(credentials_dict)
        await CredentialsRepository(prisma_client).create(
            data={
                **credentials_dict_jsonified,
                "created_by": user_api_key_dict.user_id,
                "updated_by": user_api_key_dict.user_id,
            }
        )

        ## ADD TO LITELLM ##
        CredentialAccessor.upsert_credentials([processed_credential])

        return {"success": True, "message": "Credential created successfully"}
    except Exception as e:
        verbose_proxy_logger.exception(e)
        raise handle_exception_on_proxy(e)


def _chatgpt_device_login_user_id(user_api_key_dict: UserAPIKeyAuth) -> Optional[str]:
    return user_api_key_dict.user_id or user_api_key_dict.key_alias or user_api_key_dict.token


def _cleanup_expired_chatgpt_device_login_flows() -> None:
    now = time.time()
    expired_login_ids = [login_id for login_id, state in _chatgpt_device_login_flows.items() if state.expires_at <= now]
    for login_id in expired_login_ids:
        _chatgpt_device_login_flows.pop(login_id, None)


def _cleanup_expired_github_copilot_device_login_flows() -> None:
    now = time.time()
    expired_login_ids = [
        login_id for login_id, state in _github_copilot_device_login_flows.items() if state.expires_at <= now
    ]
    for login_id in expired_login_ids:
        _github_copilot_device_login_flows.pop(login_id, None)


async def _store_chatgpt_credential(
    credential_name: str,
    credential_values: dict,
    user_id: Optional[str],
    overwrite_existing: bool,
) -> None:
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        raise HTTPException(
            status_code=500,
            detail={"error": CommonProxyErrors.db_not_connected_error.value},
        )

    credentials_repository = CredentialsRepository(prisma_client)
    existing_credential = await credentials_repository.find_by_name(credential_name)
    if existing_credential is not None and not overwrite_existing:
        raise HTTPException(status_code=409, detail="Credential already exists")

    processed_credential = CredentialItem(
        credential_name=credential_name,
        credential_values=credential_values,
        credential_info={
            "custom_llm_provider": "chatgpt",
            "auth_type": "device_code",
            "chatgpt_account_id": credential_values.get("chatgpt_account_id"),
        },
    )
    encrypted_credential = CredentialHelperUtils.encrypt_credential_values(processed_credential)
    credentials_dict_jsonified = jsonify_object(encrypted_credential.model_dump())
    if existing_credential is None:
        await credentials_repository.create(
            data={
                **credentials_dict_jsonified,
                "created_by": user_id,
                "updated_by": user_id,
            }
        )
    else:
        await credentials_repository.update_by_name(
            credential_name,
            data={
                **credentials_dict_jsonified,
                "updated_by": user_id,
            },
        )

    CredentialAccessor.upsert_credentials([processed_credential])


async def _store_github_copilot_credential(
    credential_name: str,
    access_token: str,
    user_id: Optional[str],
    overwrite_existing: bool,
) -> None:
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        raise HTTPException(status_code=500, detail={"error": CommonProxyErrors.db_not_connected_error.value})

    credentials_repository = CredentialsRepository(prisma_client)
    existing_credential = await credentials_repository.find_by_name(credential_name)
    if existing_credential is not None and not overwrite_existing:
        raise HTTPException(status_code=409, detail="Credential already exists")

    processed_credential = CredentialItem(
        credential_name=credential_name,
        credential_values={"api_key": access_token},
        credential_info={"custom_llm_provider": "github_copilot", "auth_type": "device_code"},
    )
    encrypted_credential = CredentialHelperUtils.encrypt_credential_values(processed_credential)
    credentials_dict_jsonified = jsonify_object(encrypted_credential.model_dump())
    if existing_credential is None:
        await credentials_repository.create(
            data={**credentials_dict_jsonified, "created_by": user_id, "updated_by": user_id}
        )
    else:
        await credentials_repository.update_by_name(
            credential_name,
            data={**credentials_dict_jsonified, "updated_by": user_id},
        )
    CredentialAccessor.upsert_credentials([processed_credential])


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
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        raise HTTPException(
            status_code=500,
            detail={"error": CommonProxyErrors.db_not_connected_error.value},
        )

    _cleanup_expired_chatgpt_device_login_flows()
    if not body.overwrite_existing:
        existing_credential = await CredentialsRepository(prisma_client).find_by_name(body.credential_name)
        if existing_credential is not None:
            raise HTTPException(status_code=409, detail="Credential already exists")

    try:
        device_code = Authenticator()._request_device_code()
    except GetDeviceCodeError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e

    login_id = str(uuid.uuid4())
    interval = int(device_code.get("interval", "5"))
    expires_at = time.time() + DEVICE_CODE_TIMEOUT_SECONDS
    _chatgpt_device_login_flows[login_id] = ChatGPTDeviceLoginState(
        credential_name=body.credential_name,
        api_base=body.api_base,
        overwrite_existing=body.overwrite_existing,
        user_id=_chatgpt_device_login_user_id(user_api_key_dict),
        device_auth_id=device_code["device_auth_id"],
        user_code=device_code["user_code"],
        interval=interval,
        expires_at=expires_at,
    )
    return {
        "success": True,
        "login_id": login_id,
        "verification_url": CHATGPT_DEVICE_VERIFY_URL,
        "user_code": device_code["user_code"],
        "interval": interval,
        "expires_at": expires_at,
    }


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
    _cleanup_expired_chatgpt_device_login_flows()
    login_state = _chatgpt_device_login_flows.get(body.login_id)
    if login_state is None:
        raise HTTPException(status_code=404, detail="ChatGPT device login not found or expired")

    if login_state.user_id != _chatgpt_device_login_user_id(user_api_key_dict):
        raise HTTPException(status_code=403, detail="ChatGPT device login belongs to another user")

    authenticator = Authenticator()
    try:
        authorization_code = authenticator._poll_for_authorization_code_once(
            {
                "device_auth_id": login_state.device_auth_id,
                "user_code": login_state.user_code,
                "interval": str(login_state.interval),
            }
        )
    except GetAccessTokenError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e

    if authorization_code is None:
        return {
            "success": True,
            "status": "pending",
            "interval": login_state.interval,
            "expires_at": login_state.expires_at,
        }

    try:
        tokens = authenticator._exchange_code_for_tokens(authorization_code)
    except GetAccessTokenError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e

    credential_values = build_chatgpt_credential_values(tokens=tokens, api_base=login_state.api_base)
    await _store_chatgpt_credential(
        credential_name=login_state.credential_name,
        credential_values=credential_values,
        user_id=login_state.user_id,
        overwrite_existing=login_state.overwrite_existing,
    )
    _chatgpt_device_login_flows.pop(body.login_id, None)
    return {
        "success": True,
        "status": "complete",
        "credential_name": login_state.credential_name,
        "chatgpt_account_id": credential_values.get("chatgpt_account_id"),
    }


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
    from litellm.proxy.proxy_server import prisma_client

    if prisma_client is None:
        raise HTTPException(status_code=500, detail={"error": CommonProxyErrors.db_not_connected_error.value})
    _cleanup_expired_github_copilot_device_login_flows()
    if not body.overwrite_existing:
        existing_credential = await CredentialsRepository(prisma_client).find_by_name(body.credential_name)
        if existing_credential is not None:
            raise HTTPException(status_code=409, detail="Credential already exists")

    try:
        device_code = GitHubCopilotAuthenticator()._get_device_code()
    except GitHubGetDeviceCodeError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e

    login_id = str(uuid.uuid4())
    interval = int(device_code.get("interval", "5"))
    expires_at = time.time() + int(device_code.get("expires_in", "900"))
    _github_copilot_device_login_flows[login_id] = GitHubCopilotDeviceLoginState(
        credential_name=body.credential_name,
        overwrite_existing=body.overwrite_existing,
        user_id=_chatgpt_device_login_user_id(user_api_key_dict),
        device_code=device_code["device_code"],
        interval=interval,
        expires_at=expires_at,
    )
    return {
        "success": True,
        "login_id": login_id,
        "verification_url": device_code["verification_uri"],
        "user_code": device_code["user_code"],
        "interval": interval,
        "expires_at": expires_at,
    }


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
    _cleanup_expired_github_copilot_device_login_flows()
    login_state = _github_copilot_device_login_flows.get(body.login_id)
    if login_state is None:
        raise HTTPException(status_code=404, detail="GitHub Copilot device login not found or expired")
    if login_state.user_id != _chatgpt_device_login_user_id(user_api_key_dict):
        raise HTTPException(status_code=403, detail="GitHub Copilot device login belongs to another user")

    try:
        access_token = GitHubCopilotAuthenticator()._poll_for_access_token_once(login_state.device_code)
    except GitHubGetAccessTokenError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    if access_token is None:
        return {
            "success": True,
            "status": "pending",
            "interval": login_state.interval,
            "expires_at": login_state.expires_at,
        }

    await _store_github_copilot_credential(
        credential_name=login_state.credential_name,
        access_token=access_token,
        user_id=login_state.user_id,
        overwrite_existing=login_state.overwrite_existing,
    )
    _github_copilot_device_login_flows.pop(body.login_id, None)
    return {"success": True, "status": "complete", "credential_name": login_state.credential_name}


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
        await CredentialsRepository(prisma_client).delete_by_name(credential_name)

        ## DELETE FROM LITELLM ##
        litellm.credential_list = [cred for cred in litellm.credential_list if cred.credential_name != credential_name]
        return {"success": True, "message": "Credential deleted successfully"}
    except Exception as e:
        return handle_exception_on_proxy(e)


def update_db_credential(
    db_credential: CredentialItem,
    updated_patch: CredentialItem,
    new_encryption_key: Optional[str] = None,
) -> CredentialItem:
    """
    Update a credential in the DB.
    """
    merged_credential = CredentialItem(
        credential_name=db_credential.credential_name,
        credential_info=db_credential.credential_info,
        credential_values=db_credential.credential_values,
    )

    encrypted_credential = CredentialHelperUtils.encrypt_credential_values(
        updated_patch,
        new_encryption_key,
    )
    # update model name
    if encrypted_credential.credential_name:
        merged_credential.credential_name = encrypted_credential.credential_name

    # update litellm params
    if encrypted_credential.credential_values:
        # Encrypt any sensitive values
        encrypted_params = {k: v for k, v in encrypted_credential.credential_values.items()}

        merged_credential.credential_values.update(encrypted_params)

    # update model info
    if encrypted_credential.credential_info:
        """Update credential info"""
        if "credential_info" not in merged_credential.credential_info:
            merged_credential.credential_info = {}
        merged_credential.credential_info.update(encrypted_credential.credential_info)

    return merged_credential


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
        if prisma_client is None:
            raise HTTPException(
                status_code=500,
                detail={"error": CommonProxyErrors.db_not_connected_error.value},
            )
        credentials_repository = CredentialsRepository(prisma_client)
        db_credential = await credentials_repository.find_by_name(credential_name)
        if db_credential is None:
            raise HTTPException(status_code=404, detail="Credential not found in DB.")
        merged_credential = update_db_credential(db_credential, credential)
        credential_object_jsonified = jsonify_object(merged_credential.model_dump())
        await credentials_repository.update_by_name(
            credential_name,
            data={
                **credential_object_jsonified,
                "updated_by": user_api_key_dict.user_id,
            },
        )

        # Sync in-memory credential_list (skip if not in memory - e.g., proxy restarted)
        new_name = merged_credential.credential_name
        existing_in_memory: Optional[CredentialItem] = None
        for cred in litellm.credential_list:
            if cred.credential_name == credential_name:
                existing_in_memory = cred
                break

        if existing_in_memory is not None:
            in_memory_values = dict(existing_in_memory.credential_values or {})
            if credential.credential_values:
                in_memory_values.update(credential.credential_values)
            in_memory_info = dict(existing_in_memory.credential_info or {})
            if credential.credential_info:
                in_memory_info.update(credential.credential_info)
            updated_in_memory = CredentialItem(
                credential_name=new_name,
                credential_values=in_memory_values,
                credential_info=in_memory_info,
            )
            # Remove old entry if renamed, then use upsert_credentials to handle duplicates
            if new_name != credential_name:
                litellm.credential_list = [c for c in litellm.credential_list if c.credential_name != credential_name]
            CredentialAccessor.upsert_credentials([updated_in_memory])

        return {"success": True, "message": "Credential updated successfully"}
    except Exception as e:
        return handle_exception_on_proxy(e)
