import json
import os
import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from litellm._logging import verbose_logger
from litellm.llms.custom_httpx.http_handler import _get_httpx_client

from .common_utils import (
    GetAccessTokenError,
    GetAPIKeyError,
    GetDeviceCodeError,
    RefreshAPIKeyError,
)

# Constants (default values — overridable via environment variables at call time)
DEFAULT_GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
DEFAULT_GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEFAULT_GITHUB_API_KEY_URL = "https://api.github.com/copilot_internal/v2/token"


@dataclass(frozen=True, slots=True)
class CopilotCredential:
    token: str
    expires_at: float
    api_base: Optional[str]


class Authenticator:
    _cache: dict[str, CopilotCredential] = {}
    _refresh_locks: dict[str, threading.Lock] = {}
    _cache_lock = threading.Lock()

    def get_api_key(self, github_access_token: Optional[str]) -> str:
        return self._get_copilot_credential(github_access_token).token

    def get_api_base(self, github_access_token: Optional[str]) -> Optional[str]:
        return self._get_copilot_credential(github_access_token).api_base

    def _get_copilot_credential(self, github_access_token: Optional[str]) -> CopilotCredential:
        if not github_access_token:
            raise GetAPIKeyError(
                message=(
                    "GitHub Copilot credential is required. Create one from "
                    "Models & Endpoints > LLM Credentials > Github Copilot."
                ),
                status_code=401,
            )
        cache_key = hashlib.sha256(github_access_token.encode()).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            refresh_lock = self._refresh_locks.setdefault(cache_key, threading.Lock())
        if cached is not None and cached.expires_at > datetime.now().timestamp() + 60:
            return cached
        with refresh_lock:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached is not None and cached.expires_at > datetime.now().timestamp() + 60:
                return cached
            try:
                response = self._refresh_api_key(github_access_token)
            except RefreshAPIKeyError as e:
                raise GetAPIKeyError(message=f"Failed to refresh API key: {str(e)}", status_code=401) from e
            token = response.get("token")
            if not isinstance(token, str) or not token:
                raise GetAPIKeyError(message="API key response missing token", status_code=401)
            expires_at = response.get("expires_at")
            try:
                resolved_expires_at = float(expires_at)
            except (TypeError, ValueError):
                resolved_expires_at = 0.0
            endpoints = response.get("endpoints")
            api_base = endpoints.get("api") if isinstance(endpoints, dict) else None
            credential = CopilotCredential(token=token, expires_at=resolved_expires_at, api_base=api_base)
            with self._cache_lock:
                self._cache[cache_key] = credential
        return credential

    def _refresh_api_key(self, github_access_token: str) -> Dict[str, Any]:
        """
        Refresh the API key using the access token.

        Returns:
            Dict[str, Any]: The API key information including token and expiration.

        Raises:
            RefreshAPIKeyError: If unable to refresh the API key.
        """
        headers = self._get_github_headers(github_access_token)
        api_key_url = os.getenv("GITHUB_COPILOT_API_KEY_URL", DEFAULT_GITHUB_API_KEY_URL)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                sync_client = _get_httpx_client()
                response = sync_client.get(api_key_url, headers=headers)
                response.raise_for_status()

                response_json = response.json()

                if "token" in response_json:
                    return response_json
                else:
                    verbose_logger.warning(f"API key response missing token: {response_json}")
            except httpx.HTTPStatusError as e:
                verbose_logger.error(f"HTTP error refreshing API key (attempt {attempt + 1}/{max_retries}): {str(e)}")
            except Exception as e:
                verbose_logger.error(f"Unexpected error refreshing API key: {str(e)}")

        raise RefreshAPIKeyError(
            message="Failed to refresh API key after maximum retries",
            status_code=401,
        )

    def _get_github_headers(self, access_token: Optional[str] = None) -> Dict[str, str]:
        """
        Generate standard GitHub headers for API requests.

        Args:
            access_token: Optional access token to include in the headers.

        Returns:
            Dict[str, str]: Headers for GitHub API requests.
        """
        headers = {
            "accept": "application/json",
            "editor-version": "vscode/1.85.1",
            "editor-plugin-version": "copilot/1.155.0",
            "user-agent": "GithubCopilot/1.155.0",
            "accept-encoding": "gzip,deflate,br",
        }

        if access_token:
            headers["authorization"] = f"token {access_token}"

        if "content-type" not in headers:
            headers["content-type"] = "application/json"

        return headers

    def _get_device_code(self) -> Dict[str, str]:
        """
        Get a device code for GitHub authentication.

        Returns:
            Dict[str, str]: Device code information.

        Raises:
            GetDeviceCodeError: If unable to get a device code.
        """
        try:
            sync_client = _get_httpx_client()
            device_code_url = os.getenv("GITHUB_COPILOT_DEVICE_CODE_URL", DEFAULT_GITHUB_DEVICE_CODE_URL)
            client_id = os.getenv("GITHUB_COPILOT_CLIENT_ID", DEFAULT_GITHUB_CLIENT_ID)
            resp = sync_client.post(
                device_code_url,
                headers=self._get_github_headers(),
                json={"client_id": client_id, "scope": "read:user"},
            )
            resp.raise_for_status()
            resp_json = resp.json()

            required_fields = ["device_code", "user_code", "verification_uri"]
            if not all(field in resp_json for field in required_fields):
                verbose_logger.error(f"Response missing required fields: {resp_json}")
                raise GetDeviceCodeError(
                    message="Response missing required fields",
                    status_code=400,
                )

            return resp_json
        except httpx.HTTPStatusError as e:
            verbose_logger.error(f"HTTP error getting device code: {str(e)}")
            raise GetDeviceCodeError(
                message=f"Failed to get device code: {str(e)}",
                status_code=400,
            )
        except json.JSONDecodeError as e:
            verbose_logger.error(f"Error decoding JSON response: {str(e)}")
            raise GetDeviceCodeError(
                message=f"Failed to decode device code response: {str(e)}",
                status_code=400,
            )
        except Exception as e:
            verbose_logger.error(f"Unexpected error getting device code: {str(e)}")
            raise GetDeviceCodeError(
                message=f"Failed to get device code: {str(e)}",
                status_code=400,
            )

    def _poll_for_access_token_once(self, device_code: str) -> Optional[str]:
        sync_client = _get_httpx_client()
        access_token_url = os.getenv("GITHUB_COPILOT_ACCESS_TOKEN_URL", DEFAULT_GITHUB_ACCESS_TOKEN_URL)
        client_id = os.getenv("GITHUB_COPILOT_CLIENT_ID", DEFAULT_GITHUB_CLIENT_ID)
        try:
            resp = sync_client.post(
                access_token_url,
                headers=self._get_github_headers(),
                json={
                    "client_id": client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            resp.raise_for_status()
            resp_json = resp.json()
        except httpx.HTTPStatusError as e:
            raise GetAccessTokenError(message=f"Failed to get access token: {str(e)}", status_code=400) from e
        except (json.JSONDecodeError, TypeError) as e:
            raise GetAccessTokenError(
                message=f"Failed to decode access token response: {str(e)}", status_code=400
            ) from e

        access_token = resp_json.get("access_token")
        if isinstance(access_token, str) and access_token:
            return access_token
        if resp_json.get("error") in {"authorization_pending", "slow_down"}:
            return None
        error = resp_json.get("error_description") or resp_json.get("error") or "Unexpected GitHub OAuth response"
        raise GetAccessTokenError(message=str(error), status_code=400)
