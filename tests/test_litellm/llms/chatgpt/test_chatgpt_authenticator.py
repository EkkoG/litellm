import base64
import json
import time
from unittest.mock import patch

import pytest

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.llms.chatgpt.common_utils import GetAccessTokenError


def _make_jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def _b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_b64(header)}.{_b64(payload)}."


class TestChatGPTAuthenticator:
    @pytest.fixture
    def authenticator(self):
        return Authenticator()

    def test_get_access_token_uses_supplied_api_key(self, authenticator):
        token = authenticator.get_access_token(
            api_key="token-123",
            litellm_params={"chatgpt_refresh_token": "refresh-123"},
        )

        assert token == "token-123"

    def test_get_access_token_refreshes_supplied_expired_token(self, authenticator):
        refreshed = {
            "access_token": "token-new",
            "refresh_token": "refresh-123",
            "id_token": "id-123",
        }

        with patch.object(authenticator, "_refresh_tokens", return_value=refreshed):
            token = authenticator.get_access_token(
                api_key="token-old",
                litellm_params={
                    "chatgpt_refresh_token": "refresh-123",
                    "chatgpt_expires_at": str(time.time() - 10),
                },
            )

        assert token == "token-new"

    def test_parse_refresh_response_preserves_unrotated_refresh_token(self, authenticator):
        refreshed = authenticator._parse_refresh_response(
            {"access_token": "token-new", "id_token": "id-new"},
            "refresh-old",
        )

        assert refreshed == {
            "access_token": "token-new",
            "refresh_token": "refresh-old",
            "id_token": "id-new",
        }

    def test_get_access_token_does_not_start_local_device_login(self, authenticator):
        with pytest.raises(GetAccessTokenError):
            authenticator.get_access_token(api_key=None, litellm_params={})

    def test_get_account_id_from_id_token(self, authenticator):
        id_token = _make_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"}})

        account_id = authenticator.get_account_id(
            {
                "chatgpt_id_token": id_token,
            }
        )

        assert account_id == "acct-123"

    def test_get_plan_type_from_id_token(self, authenticator: Authenticator):
        id_token = _make_jwt({"https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}})

        plan_type = authenticator.get_plan_type(
            {
                "chatgpt_id_token": id_token,
            }
        )

        assert plan_type == "pro"
