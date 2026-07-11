from unittest.mock import MagicMock

import pytest

from litellm.llms.github_copilot.authenticator import Authenticator
from litellm.llms.github_copilot.common_utils import GetAPIKeyError


@pytest.fixture(autouse=True)
def clear_authenticator_cache():
    Authenticator._cache.clear()
    Authenticator._refresh_locks.clear()


def test_requires_managed_github_credential():
    with pytest.raises(GetAPIKeyError, match="LLM Credentials"):
        Authenticator().get_api_key(None)


def test_exchanges_github_access_token_without_filesystem(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "token": "copilot-token",
        "expires_at": "4102444800",
        "endpoints": {"api": "https://api.githubcopilot.com"},
    }
    client = MagicMock()
    client.get.return_value = response
    monkeypatch.setattr("litellm.llms.github_copilot.authenticator._get_httpx_client", lambda: client)

    authenticator = Authenticator()

    assert authenticator.get_api_key("github-access-token") == "copilot-token"
    assert authenticator.get_api_base("github-access-token") == "https://api.githubcopilot.com"
    client.get.assert_called_once()
    assert client.get.call_args.kwargs["headers"]["authorization"] == "token github-access-token"


def test_reuses_unexpired_copilot_token(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"token": "copilot-token", "expires_at": 4102444800}
    client = MagicMock()
    client.get.return_value = response
    monkeypatch.setattr("litellm.llms.github_copilot.authenticator._get_httpx_client", lambda: client)

    authenticator = Authenticator()

    assert authenticator.get_api_key("github-access-token") == "copilot-token"
    assert authenticator.get_api_key("github-access-token") == "copilot-token"
    client.get.assert_called_once()


def test_device_flow_is_non_blocking(monkeypatch):
    pending_response = MagicMock()
    pending_response.raise_for_status.return_value = None
    pending_response.json.return_value = {"error": "authorization_pending"}
    client = MagicMock()
    client.post.return_value = pending_response
    monkeypatch.setattr("litellm.llms.github_copilot.authenticator._get_httpx_client", lambda: client)

    assert Authenticator()._poll_for_access_token_once("device-code") is None
    client.post.assert_called_once()
