"""Behavior pins for ``proxy_server.py`` model routes.

Pins (PR2):
    - GET /v1/models
    - GET /models
    - GET /v1/models/{model_id}
    - GET /models/{model_id}
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Protocol
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import TypeAdapter

import litellm
from litellm.proxy import proxy_server
from litellm.types.proxy.model_listing import (
    ClaudeDesktopModelListResponse,
    ModelInfoResponse,
    ModelListResponse,
)

from .conftest import normalize  # type: ignore[import-not-found]


CLAUDE_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Claude/1.22209.3 Chrome/148.0.7778.271 Electron/42.5.1 Safari/537.36"
)
CLAUDE_DESKTOP_MODEL_LIST_ADAPTER = TypeAdapter(ClaudeDesktopModelListResponse)
MODEL_LIST_ADAPTER = TypeAdapter(ModelListResponse)


class HTTPTestClient(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> httpx.Response: ...


def _stub_model_info_response(model_id: str = "gpt-4", provider: str = "openai") -> ModelInfoResponse:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": provider,
    }


@pytest.fixture
def patched_models(monkeypatch):
    """Stub router + utility helpers used by the /models routes."""
    from litellm.proxy import utils as proxy_utils

    router = MagicMock()
    router.get_fully_blocked_model_names = MagicMock(return_value=set())
    router.get_model_names = MagicMock(return_value=["gpt-4", "claude-sonnet"])
    router.get_model_access_groups = MagicMock(return_value={})

    deployment = MagicMock()
    deployment.litellm_params.model = "gpt-4"
    router.get_deployment_by_model_group_name = MagicMock(return_value=deployment)

    monkeypatch.setattr(proxy_server, "llm_router", router)
    monkeypatch.setattr(proxy_server, "prisma_client", MagicMock())

    async def _fake_get_available_models_for_user(**kwargs):
        return ["gpt-4", "claude-sonnet"]

    monkeypatch.setattr(
        proxy_utils,
        "get_available_models_for_user",
        _fake_get_available_models_for_user,
    )

    def _fake_create_model_info_response(model_id, provider="openai", **kwargs):
        return _stub_model_info_response(model_id=model_id, provider=provider)

    monkeypatch.setattr(proxy_utils, "create_model_info_response", _fake_create_model_info_response)

    monkeypatch.setattr(proxy_utils, "validate_model_access", lambda **kwargs: None)

    monkeypatch.setattr(
        litellm,
        "get_llm_provider",
        lambda model: (model, "openai", None, None),
    )

    return router


@pytest.mark.parametrize("path", ["/v1/models", "/models"])
def test_get_models_happy_path(client, auth_as, patched_models, path):
    """Pins: ``GET /v1/models``, ``GET /models``."""
    with auth_as():
        response = client.get(path)
    assert response.status_code == 200
    assert normalize(response.json()) == {
        "data": [
            {
                "id": "<VOLATILE>",
                "object": "model",
                "created": "<VOLATILE>",
                "owned_by": "openai",
            },
            {
                "id": "<VOLATILE>",
                "object": "model",
                "created": "<VOLATILE>",
                "owned_by": "openai",
            },
        ],
        "object": "list",
    }


def test_v1_models_returns_claude_desktop_shape_after_filtering(
    client: HTTPTestClient,
    auth_as: Callable[..., AbstractContextManager[object]],
    patched_models: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    from litellm.proxy import utils as proxy_utils

    monkeypatch.setattr(patched_models, "get_fully_blocked_model_names", MagicMock(return_value={"gpt-4"}))

    def _fake_create_model_info_response(
        model_id: str, provider: str = "openai", **kwargs: object
    ) -> ModelInfoResponse:
        return {
            **_stub_model_info_response(model_id=model_id, provider=provider),
            "max_input_tokens": 1_000_000 if model_id == "claude-sonnet" else 999_999,
        }

    monkeypatch.setattr(proxy_utils, "create_model_info_response", _fake_create_model_info_response)

    with auth_as():
        response = client.get("/v1/models", headers={"User-Agent": CLAUDE_DESKTOP_USER_AGENT})

    assert response.status_code == 200
    assert CLAUDE_DESKTOP_MODEL_LIST_ADAPTER.validate_json(response.content) == {
        "data": [
            {
                "id": "claude-sonnet",
                "type": "model",
                "created_at": "1970-01-01T00:00:00Z",
                "supports1m": True,
            }
        ],
        "has_more": False,
        "first_id": "claude-sonnet",
        "last_id": "claude-sonnet",
    }
    assert "user-agent" in {value.strip().lower() for value in response.headers["vary"].split(",")}


def test_models_route_ignores_claude_desktop_user_agent(
    client: HTTPTestClient,
    auth_as: Callable[..., AbstractContextManager[object]],
    patched_models: MagicMock,
):
    with auth_as():
        response = client.get("/models", headers={"User-Agent": CLAUDE_DESKTOP_USER_AGENT})

    assert response.status_code == 200
    response_body = MODEL_LIST_ADAPTER.validate_json(response.content)
    assert response_body["object"] == "list"
    assert all(model["object"] == "model" for model in response_body["data"])
    assert "has_more" not in response_body


def test_v1_models_normal_user_agent_keeps_openai_shape_and_varies_by_user_agent(
    client: HTTPTestClient,
    auth_as: Callable[..., AbstractContextManager[object]],
    patched_models: MagicMock,
):
    with auth_as():
        response = client.get("/v1/models", headers={"User-Agent": "OpenAI/Python 2.17.0"})

    assert response.status_code == 200
    response_body = MODEL_LIST_ADAPTER.validate_json(response.content)
    assert response_body["object"] == "list"
    assert "has_more" not in response_body
    assert "user-agent" in {value.strip().lower() for value in response.headers["vary"].split(",")}


@pytest.mark.parametrize("path", ["/v1/models", "/models"])
def test_get_models_invalid_scope_returns_400(client, auth_as, patched_models, path):
    """Pins: ``GET /v1/models``, ``GET /models`` (error path: invalid scope)."""
    with auth_as():
        response = client.get(path, params={"scope": "not-a-real-scope"})
    assert response.status_code == 400
    assert "Invalid scope parameter" in str(response.json())


@pytest.mark.parametrize("path", ["/v1/models/gpt-4", "/models/gpt-4"])
def test_get_model_by_id_happy_path(client, auth_as, patched_models, path):
    """Pins: ``GET /v1/models/{model_id}``, ``GET /models/{model_id}``."""
    with auth_as():
        response = client.get(path)
    assert response.status_code == 200
    assert normalize(response.json()) == {
        "id": "<VOLATILE>",
        "object": "model",
        "created": "<VOLATILE>",
        "owned_by": "openai",
    }


@pytest.mark.parametrize("path", ["/v1/models/missing", "/models/missing"])
def test_get_model_by_id_not_found(client, auth_as, patched_models, path):
    """Pins: ``GET /v1/models/{model_id}``, ``GET /models/{model_id}`` (error: 404)."""
    patched_models.get_deployment_by_model_group_name = MagicMock(return_value=None)
    with auth_as():
        response = client.get(path)
    assert response.status_code == 404
    assert "not found" in response.text.lower()
