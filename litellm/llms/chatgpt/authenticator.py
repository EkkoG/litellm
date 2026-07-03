import base64
import binascii
import json
import os
import time
from typing import Any, Optional

import httpx

from litellm.llms.custom_httpx.http_handler import _get_httpx_client

from .common_utils import (
    CHATGPT_API_BASE,
    CHATGPT_AUTH_BASE,
    CHATGPT_CLIENT_ID,
    CHATGPT_DEVICE_CODE_URL,
    CHATGPT_DEVICE_TOKEN_URL,
    CHATGPT_OAUTH_TOKEN_URL,
    GetAccessTokenError,
    GetDeviceCodeError,
    RefreshAccessTokenError,
)

TOKEN_EXPIRY_SKEW_SECONDS = 60
DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60
DEVICE_CODE_POLL_SLEEP_SECONDS = 5


class Authenticator:
    def __init__(self) -> None:
        pass

    def get_api_base(self) -> str:
        return os.getenv("CHATGPT_API_BASE") or os.getenv("OPENAI_CHATGPT_API_BASE") or CHATGPT_API_BASE

    def get_access_token(self, api_key: Optional[str] = None, litellm_params: Optional[Any] = None) -> str:
        auth_data = self._build_auth_data_from_params(api_key=api_key, litellm_params=litellm_params)
        access_token = auth_data.get("access_token")
        if access_token and not self._is_token_expired(auth_data, access_token):
            return access_token

        refresh_token = auth_data.get("refresh_token")
        if refresh_token:
            try:
                refreshed = self._refresh_tokens(refresh_token)
                return refreshed["access_token"]
            except RefreshAccessTokenError as exc:
                raise GetAccessTokenError(
                    message=f"ChatGPT refresh token failed, re-login required: {exc}",
                    status_code=exc.status_code,
                )

        raise GetAccessTokenError(
            message="ChatGPT credential is missing an access token. Sign in with ChatGPT in the Admin UI.",
            status_code=401,
        )

    def get_account_id(self, litellm_params: Optional[Any] = None, access_token: Optional[str] = None) -> Optional[str]:
        auth_data = self._build_auth_data_from_params(api_key=access_token, litellm_params=litellm_params)
        account_id = auth_data.get("account_id")
        if account_id:
            return account_id
        id_token = auth_data.get("id_token")
        return self._extract_account_id(id_token or auth_data.get("access_token"))

    def _build_auth_data_from_params(self, api_key: Optional[str], litellm_params: Optional[Any]) -> dict[str, Any]:
        params = self._coerce_params(litellm_params)
        return {
            "access_token": api_key or params.get("api_key") or params.get("chatgpt_access_token"),
            "refresh_token": params.get("chatgpt_refresh_token") or params.get("refresh_token"),
            "id_token": params.get("chatgpt_id_token") or params.get("id_token"),
            "expires_at": params.get("chatgpt_expires_at") or params.get("expires_at"),
            "account_id": params.get("chatgpt_account_id") or params.get("account_id"),
        }

    def _coerce_params(self, litellm_params: Optional[Any]) -> dict[str, Any]:
        if litellm_params is None:
            return {}
        if isinstance(litellm_params, dict):
            return litellm_params
        if hasattr(litellm_params, "model_dump"):
            return litellm_params.model_dump(exclude_none=True)
        return {}

    def _is_token_expired(self, auth_data: dict[str, Any], access_token: str) -> bool:
        expires_at = auth_data.get("expires_at")
        if expires_at is None:
            expires_at = self._get_expires_at(access_token)
        if expires_at is None:
            return False
        return time.time() >= float(expires_at) - TOKEN_EXPIRY_SKEW_SECONDS

    def _get_expires_at(self, token: str) -> Optional[int]:
        claims = self._decode_jwt_claims(token)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)
        return None

    def _decode_jwt_claims(self, token: str) -> dict[str, Any]:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            payload_b64 = parts[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            return json.loads(payload_bytes.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _extract_account_id(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        claims = self._decode_jwt_claims(token)
        auth_claims = claims.get("https://api.openai.com/auth")
        if isinstance(auth_claims, dict):
            account_id = auth_claims.get("chatgpt_account_id")
            if isinstance(account_id, str) and account_id:
                return account_id
        return None

    def _request_device_code(self) -> dict[str, str]:
        try:
            client = _get_httpx_client()
            resp = client.post(
                CHATGPT_DEVICE_CODE_URL,
                json={"client_id": CHATGPT_CLIENT_ID},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise GetDeviceCodeError(
                message=f"Failed to request device code: {exc}",
                status_code=exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise GetDeviceCodeError(
                message=f"Failed to request device code: {exc}",
                status_code=400,
            )

        device_auth_id = data.get("device_auth_id")
        user_code = data.get("user_code") or data.get("usercode")
        interval = data.get("interval")
        if not device_auth_id or not user_code:
            raise GetDeviceCodeError(
                message=f"Device code response missing fields: {data}",
                status_code=400,
            )
        return {
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "interval": str(interval or "5"),
        }

    def _poll_for_authorization_code(self, device_code: dict[str, str]) -> dict[str, str]:
        interval = int(device_code.get("interval", "5"))
        start_time = time.time()
        while time.time() - start_time < DEVICE_CODE_TIMEOUT_SECONDS:
            authorization_code = self._poll_for_authorization_code_once(device_code)
            if authorization_code is not None:
                return authorization_code
            time.sleep(max(interval, DEVICE_CODE_POLL_SLEEP_SECONDS))

        raise GetAccessTokenError(
            message="Timed out waiting for device authorization",
            status_code=408,
        )

    def _poll_for_authorization_code_once(self, device_code: dict[str, str]) -> Optional[dict[str, str]]:
        client = _get_httpx_client()
        try:
            resp = client.post(
                CHATGPT_DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": device_code["device_auth_id"],
                    "user_code": device_code["user_code"],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if all(
                    key in data
                    for key in (
                        "authorization_code",
                        "code_challenge",
                        "code_verifier",
                    )
                ):
                    return data
            if resp.status_code in (403, 404):
                return None
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            if status_code in (403, 404):
                return None
            raise GetAccessTokenError(
                message=f"Polling failed: {exc}",
                status_code=exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise GetAccessTokenError(
                message=f"Polling failed: {exc}",
                status_code=400,
            )
        return None

    def _exchange_code_for_tokens(self, code_data: dict[str, str]) -> dict[str, str]:
        try:
            client = _get_httpx_client()
            redirect_uri = f"{CHATGPT_AUTH_BASE}/deviceauth/callback"
            body = (
                "grant_type=authorization_code"
                f"&code={code_data['authorization_code']}"
                f"&redirect_uri={redirect_uri}"
                f"&client_id={CHATGPT_CLIENT_ID}"
                f"&code_verifier={code_data['code_verifier']}"
            )
            resp = client.post(
                CHATGPT_OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=body,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise GetAccessTokenError(
                message=f"Token exchange failed: {exc}",
                status_code=exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise GetAccessTokenError(
                message=f"Token exchange failed: {exc}",
                status_code=400,
            )

        if not all(key in data for key in ("access_token", "refresh_token", "id_token")):
            raise GetAccessTokenError(
                message=f"Token exchange response missing fields: {data}",
                status_code=400,
            )
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "id_token": data["id_token"],
        }

    def _refresh_tokens(self, refresh_token: str) -> dict[str, str]:
        try:
            client = _get_httpx_client()
            resp = client.post(
                CHATGPT_OAUTH_TOKEN_URL,
                json={
                    "client_id": CHATGPT_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": "openid profile email",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise RefreshAccessTokenError(
                message=f"Refresh token failed: {exc}",
                status_code=exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise RefreshAccessTokenError(
                message=f"Refresh token failed: {exc}",
                status_code=400,
            )

        access_token = data.get("access_token")
        id_token = data.get("id_token")
        if not access_token or not id_token:
            raise RefreshAccessTokenError(
                message=f"Refresh response missing fields: {data}",
                status_code=400,
            )

        refreshed = {
            "access_token": access_token,
            "refresh_token": data.get("refresh_token", refresh_token),
            "id_token": id_token,
        }
        return refreshed

    def _build_auth_record(self, tokens: dict[str, str]) -> dict[str, Any]:
        access_token = tokens.get("access_token")
        id_token = tokens.get("id_token")
        expires_at = self._get_expires_at(access_token) if access_token else None
        account_id = self._extract_account_id(id_token or access_token)
        return {
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token"),
            "id_token": id_token,
            "expires_at": expires_at,
            "account_id": account_id,
        }
