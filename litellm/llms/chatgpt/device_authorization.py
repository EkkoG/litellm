import time
from typing import Optional

from starlette.concurrency import run_in_threadpool

from litellm.llms.chatgpt.authenticator import DEVICE_CODE_TIMEOUT_SECONDS, Authenticator
from litellm.llms.chatgpt.common_utils import CHATGPT_DEVICE_VERIFY_URL, GetAccessTokenError, GetDeviceCodeError
from litellm.proxy.credential_endpoints.chatgpt_credential_utils import build_chatgpt_credential_values
from litellm.proxy.credential_endpoints.device_login_flow import (
    ProviderAuthorized,
    ProviderChallenge,
    ProviderFailed,
    ProviderPending,
    ProviderPollResult,
    ProviderStartResult,
)
from litellm.types.utils import CredentialItem


class ChatGPTDeviceAuthorizationProvider:
    def __init__(self, authenticator: Optional[Authenticator] = None) -> None:
        self._authenticator = authenticator or Authenticator()

    async def begin(self, api_base: Optional[str]) -> ProviderStartResult:
        try:
            device_code = await run_in_threadpool(self._authenticator.request_device_code)
        except GetDeviceCodeError as exc:
            return ProviderFailed(status_code=exc.status_code, detail=str(exc))
        return ProviderChallenge(
            provider="chatgpt",
            verification_url=CHATGPT_DEVICE_VERIFY_URL,
            user_code=device_code["user_code"],
            interval_seconds=int(device_code.get("interval", "5")),
            expires_at=time.time() + DEVICE_CODE_TIMEOUT_SECONDS,
            payload={
                "device_auth_id": device_code["device_auth_id"],
                "user_code": device_code["user_code"],
                "api_base": api_base or "",
            },
        )

    async def poll(self, challenge: ProviderChallenge, credential_name: str) -> ProviderPollResult:
        try:
            authorization_code = await run_in_threadpool(
                self._authenticator.poll_for_authorization_code_once,
                {
                    "device_auth_id": challenge.payload["device_auth_id"],
                    "user_code": challenge.payload["user_code"],
                    "interval": str(challenge.interval_seconds),
                },
            )
            if authorization_code is None:
                return ProviderPending(retry_after_seconds=challenge.interval_seconds)
            tokens = await run_in_threadpool(self._authenticator.exchange_code_for_tokens, authorization_code)
        except GetAccessTokenError as exc:
            return ProviderFailed(status_code=exc.status_code, detail=str(exc))
        credential_values = build_chatgpt_credential_values(
            tokens=tokens,
            api_base=challenge.payload.get("api_base") or None,
        )
        return ProviderAuthorized(
            credential=CredentialItem(
                credential_name=credential_name,
                credential_values=credential_values,
                credential_info={
                    "custom_llm_provider": "chatgpt",
                    "auth_type": "device_code",
                    "chatgpt_account_id": credential_values.get("chatgpt_account_id"),
                },
            )
        )
