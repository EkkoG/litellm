import time
from typing import Optional

from starlette.concurrency import run_in_threadpool

from litellm.llms.github_copilot.authenticator import Authenticator
from litellm.llms.github_copilot.common_utils import GetAccessTokenError, GetDeviceCodeError
from litellm.proxy.credential_endpoints.device_login_flow import (
    ProviderAuthorized,
    ProviderChallenge,
    ProviderFailed,
    ProviderPending,
    ProviderPollResult,
    ProviderStartResult,
)
from litellm.types.utils import CredentialItem


class GitHubCopilotDeviceAuthorizationProvider:
    def __init__(self, authenticator: Optional[Authenticator] = None) -> None:
        self._authenticator = authenticator or Authenticator()

    async def begin(self, api_base: Optional[str]) -> ProviderStartResult:
        try:
            device_code = await run_in_threadpool(self._authenticator.request_device_code)
        except GetDeviceCodeError as exc:
            return ProviderFailed(status_code=exc.status_code, detail=str(exc))
        return ProviderChallenge(
            provider="github_copilot",
            verification_url=device_code["verification_uri"],
            user_code=device_code["user_code"],
            interval_seconds=int(device_code.get("interval", "5")),
            expires_at=time.time() + int(device_code.get("expires_in", "900")),
            payload={"device_code": device_code["device_code"]},
        )

    async def poll(self, challenge: ProviderChallenge, credential_name: str) -> ProviderPollResult:
        try:
            access_token = await run_in_threadpool(
                self._authenticator.poll_for_access_token_once,
                challenge.payload["device_code"],
            )
        except GetAccessTokenError as exc:
            return ProviderFailed(status_code=exc.status_code, detail=str(exc))
        if access_token is None:
            return ProviderPending(retry_after_seconds=challenge.interval_seconds)
        return ProviderAuthorized(
            credential=CredentialItem(
                credential_name=credential_name,
                credential_values={"github_copilot_access_token": access_token},
                credential_info={"custom_llm_provider": "github_copilot", "auth_type": "device_code"},
            )
        )
