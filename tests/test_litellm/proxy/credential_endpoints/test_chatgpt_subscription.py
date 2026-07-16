from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request, Response

import litellm
from litellm.proxy._types import ProxyException, UserAPIKeyAuth
from litellm.proxy.credential_endpoints import endpoints
from litellm.proxy.credential_endpoints.chatgpt_quota_history import ChatGPTQuotaHistoryDays
from litellm.proxy.credential_endpoints.chatgpt_subscription import (
    CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY,
    ChatGPTDailyQuotaSnapshot,
    ChatGPTResetCreditConsumeRequest,
    ChatGPTSubscriptionStatus,
    ChatGPTSubscriptionTier,
    consume_chatgpt_rate_limit_reset_credit,
    get_chatgpt_access_token,
    get_chatgpt_account_id,
    get_chatgpt_plan_label,
    parse_chatgpt_subscription_usage,
    query_chatgpt_subscription_status,
)
from litellm.types.utils import CredentialItem


class StaticQuotaHistoryStore:
    def __init__(self, snapshots: tuple[ChatGPTDailyQuotaSnapshot, ...]) -> None:
        self._snapshots = snapshots

    async def list(
        self,
        *,
        credential_name: str,
        days: ChatGPTQuotaHistoryDays,
        timezone_name: str,
        now: datetime | None = None,
    ) -> tuple[ChatGPTDailyQuotaSnapshot, ...]:
        assert credential_name == "chatgpt-config"
        assert days == 30
        assert timezone_name == "Asia/Shanghai"
        assert now is None
        return self._snapshots


def test_parse_chatgpt_subscription_usage_maps_known_windows():
    status = parse_chatgpt_subscription_usage(
        "chatgpt-admin",
        {
            "plan": "Plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 42.5,
                    "limit_window_seconds": 18_000,
                    "reset_at": 1_700_000_000,
                },
                "secondary_window": {
                    "used_percent": 9,
                    "limit_window_seconds": 604_800,
                    "reset_at": 1_700_604_800,
                },
            },
            "rate_limit_reset_credits": {"available_count": 2},
        },
    )

    assert status.success is True
    assert status.plan_label == "Plus"
    assert [(tier.name, tier.remaining_percent) for tier in status.tiers] == [
        ("five_hour", 57.5),
        ("seven_day", 91),
    ]
    assert status.tiers[0].resets_at == "2023-11-14T22:13:20Z"
    assert status.rate_limit_reset_credits is not None
    assert status.rate_limit_reset_credits.available_count == 2


@pytest.mark.parametrize(
    ("subscription_plan", "expected_label"),
    (("chatgptprolite", "Pro (5x)"), ("chatgptpro", "Pro (20x)")),
)
def test_parse_chatgpt_subscription_usage_prefers_live_subscription_plan(
    subscription_plan: str, expected_label: str
):
    status = parse_chatgpt_subscription_usage(
        "chatgpt-admin",
        {"plan": "prolite", "subscription_plan": subscription_plan, "rate_limit": {}},
        plan_label="Pro",
    )

    assert status.plan_label == expected_label


def test_parse_chatgpt_subscription_usage_does_not_infer_variant_from_legacy_plan():
    status = parse_chatgpt_subscription_usage(
        "chatgpt-admin",
        {"plan": "chatgptprolite", "rate_limit": {}},
        plan_label="Pro",
    )

    assert status.plan_label == "Pro"


@pytest.mark.asyncio
async def test_query_chatgpt_subscription_status_uses_reset_credit_details():
    async def usage_fetcher(access_token: str, account_id: str | None) -> httpx.Response:
        assert access_token == "access-token"
        assert account_id == "account-id"
        return httpx.Response(
            200,
            json={
                "plan": "Plus",
                "rate_limit": {"primary_window": {"used_percent": 1, "limit_window_seconds": 18_000}},
                "rate_limit_reset_credits": {"available_count": 1},
            },
        )

    async def reset_credits_fetcher(access_token: str, account_id: str | None) -> httpx.Response:
        assert access_token == "access-token"
        assert account_id == "account-id"
        return httpx.Response(
            200,
            json={
                "available_count": 2,
                "credits": [
                    {
                        "id": "credit-1",
                        "reset_type": "codex_rate_limits",
                        "status": "available",
                        "granted_at": "2026-07-12T00:00:00Z",
                        "expires_at": None,
                    }
                ],
            },
        )

    status = await query_chatgpt_subscription_status(
        credential_name="chatgpt-admin",
        access_token="access-token",
        account_id="account-id",
        fetcher=usage_fetcher,
        reset_credits_fetcher=reset_credits_fetcher,
    )

    assert status.rate_limit_reset_credits is not None
    assert status.rate_limit_reset_credits.available_count == 2
    assert status.rate_limit_reset_credits.credits is not None
    assert status.rate_limit_reset_credits.credits[0].id == "credit-1"


@pytest.mark.asyncio
async def test_query_chatgpt_subscription_status_marks_unauthorized_as_expired():
    async def fetcher(access_token: str, account_id: str | None) -> httpx.Response:
        assert access_token == "access-token"
        assert account_id == "account-id"
        return httpx.Response(401, text="nope")

    status = await query_chatgpt_subscription_status(
        credential_name="chatgpt-admin",
        access_token="access-token",
        account_id="account-id",
        fetcher=fetcher,
    )

    assert status.success is False
    assert status.credential_status == "expired"
    assert "HTTP 401" in status.error


@pytest.mark.asyncio
async def test_consume_chatgpt_rate_limit_reset_credit_maps_reset_response():
    async def fetcher(
        access_token: str,
        account_id: str | None,
        idempotency_key: str,
        credit_id: str | None,
    ) -> httpx.Response:
        assert access_token == "access-token"
        assert account_id == "account-id"
        assert idempotency_key == "redeem-123"
        assert credit_id == "credit-1"
        return httpx.Response(200, json={"code": "reset", "windows_reset": 2})

    response = await consume_chatgpt_rate_limit_reset_credit(
        credential_name="chatgpt-admin",
        access_token="access-token",
        account_id="account-id",
        idempotency_key="redeem-123",
        credit_id="credit-1",
        fetcher=fetcher,
    )

    assert response.success is True
    assert response.outcome == "reset"
    assert response.windows_reset == 2


def test_chatgpt_token_helpers_accept_api_key_and_account_id():
    credential_values = {
        "api_key": "access-token",
        "chatgpt_access_token": "legacy-token",
        "chatgpt_account_id": "account-id",
    }

    assert get_chatgpt_access_token(credential_values) == "access-token"
    assert get_chatgpt_account_id(credential_values) == "account-id"


@pytest.mark.parametrize(
    ("plan_type", "expected_label"),
    (
        ("free", "Free"),
        ("chatgptfreeplan", "Free"),
        ("go", "Go"),
        ("chatgptgoplan", "Go"),
        ("plus", "Plus"),
        ("chatgptplusplan", "Plus"),
        ("prolite", "Pro"),
        ("pro", "Pro"),
        ("chatgpt_pro", "Pro"),
        ("chatgptprolite", "Pro"),
        ("chatgptpro", "Pro"),
    ),
)
def test_get_chatgpt_plan_label_normalizes_token_plan_type(plan_type: str, expected_label: str):
    assert get_chatgpt_plan_label({"chatgpt_plan_type": plan_type}) == expected_label


@pytest.mark.asyncio
async def test_get_chatgpt_credential_subscription_uses_refreshed_values(monkeypatch):
    credential = CredentialItem(
        credential_name="chatgpt-admin",
        credential_values={"api_key": "old-token"},
        credential_info={
            "custom_llm_provider": "chatgpt",
            CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY: {
                "date": "2026-07-15",
                "timezone": "UTC",
                "captured_at": "2026-07-15T00:00:00Z",
                "tiers": [{"name": "seven_day", "remaining_percent": 88.5}],
            },
        },
    )
    monkeypatch.setattr(litellm, "credential_list", [credential])
    monkeypatch.setattr(
        endpoints,
        "refresh_chatgpt_credential_if_needed",
        lambda credential, user_id: {
            "api_key": "new-token",
            "chatgpt_account_id": "account-id",
            "chatgpt_plan_type": "pro",
        },
    )
    query = AsyncMock(
        return_value={
            "credential_name": "chatgpt-admin",
            "success": True,
            "credential_status": "valid",
            "tiers": [],
            "rate_limit_reset_credits": None,
            "plan_label": None,
            "error": None,
            "queried_at": 1,
        }
    )
    monkeypatch.setattr(endpoints, "query_chatgpt_subscription_status", query)
    monkeypatch.setattr(endpoints, "get_current_budget_date", lambda: "2026-07-15")

    response = await endpoints.get_chatgpt_credential_subscription(
        request=None,
        fastapi_response=None,
        credential_name="chatgpt-admin",
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["success"] is True
    query.assert_awaited_once_with(
        credential_name="chatgpt-admin",
        access_token="new-token",
        account_id="account-id",
        plan_label="Pro",
        daily_snapshot=ChatGPTDailyQuotaSnapshot(
            date="2026-07-15",
            timezone="UTC",
            captured_at="2026-07-15T00:00:00Z",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=88.5)],
        ),
    )


@pytest.mark.asyncio
async def test_get_chatgpt_credential_subscription_omits_stale_daily_snapshot(monkeypatch: pytest.MonkeyPatch):
    credential = CredentialItem(
        credential_name="chatgpt-admin",
        credential_values={"api_key": "access-token"},
        credential_info={
            "custom_llm_provider": "chatgpt",
            CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY: {
                "date": "2026-07-15",
                "timezone": "UTC",
                "captured_at": "2026-07-15T00:00:00Z",
                "tiers": [{"name": "seven_day", "remaining_percent": 88.5}],
            },
        },
    )
    monkeypatch.setattr(litellm, "credential_list", [credential])
    monkeypatch.setattr(endpoints, "get_current_budget_date", lambda: "2026-07-16")
    query = AsyncMock(
        return_value=ChatGPTSubscriptionStatus(
            credential_name="chatgpt-admin",
            success=True,
            credential_status="valid",
            tiers=[],
            queried_at=1,
        )
    )

    def credential_refresher(credential: CredentialItem, user_id: str | None) -> dict[str, str]:
        return {"api_key": "access-token"}

    monkeypatch.setattr(endpoints, "refresh_chatgpt_credential_if_needed", credential_refresher)
    monkeypatch.setattr(endpoints, "query_chatgpt_subscription_status", query)

    await endpoints.get_chatgpt_credential_subscription(
        request=cast(Request, None),
        fastapi_response=cast(Response, None),
        credential_name="chatgpt-admin",
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    query.assert_awaited_once_with(
        credential_name="chatgpt-admin",
        access_token="access-token",
        account_id=None,
        plan_label=None,
        daily_snapshot=None,
    )


@pytest.mark.asyncio
async def test_get_chatgpt_credential_quota_history_returns_runtime_snapshot_without_database(
    monkeypatch: pytest.MonkeyPatch,
):
    credential = CredentialItem(
        credential_name="chatgpt-config",
        credential_values={},
        credential_info={
            "custom_llm_provider": "chatgpt",
            CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY: {
                "date": "2026-07-16",
                "timezone": "UTC",
                "captured_at": "2026-07-16T00:00:00Z",
                "tiers": [{"name": "seven_day", "remaining_percent": 88.5}],
            },
        },
    )
    monkeypatch.setattr(litellm, "credential_list", [credential])
    monkeypatch.setattr(endpoints, "get_budget_reset_timezone", lambda: "UTC")
    monkeypatch.setattr(endpoints, "get_current_budget_date", lambda: "2026-07-16")

    response = await endpoints.get_chatgpt_credential_quota_history(
        request=cast(Request, None),
        fastapi_response=cast(Response, None),
        credential_name="chatgpt-config",
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
        history_store=None,
        days=30,
    )

    assert response.credential_name == "chatgpt-config"
    assert response.days == 30
    assert response.timezone == "UTC"
    assert response.current_date == "2026-07-16"
    assert response.snapshots == [
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-16",
            timezone="UTC",
            captured_at="2026-07-16T00:00:00Z",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=88.5)],
        )
    ]


@pytest.mark.asyncio
async def test_get_chatgpt_credential_quota_history_reads_store_and_merges_partial_current_day(
    monkeypatch: pytest.MonkeyPatch,
):
    credential = CredentialItem(
        credential_name="chatgpt-config",
        credential_values={},
        credential_info={
            "custom_llm_provider": "chatgpt",
            CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY: {
                "date": "2026-07-16",
                "timezone": "Asia/Shanghai",
                "captured_at": "2026-07-15T16:00:00Z",
                "tiers": [
                    {"name": "five_hour", "remaining_percent": 72},
                    {"name": "seven_day", "remaining_percent": 88},
                ],
            },
        },
    )
    stored_snapshots = (
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-15",
            timezone="Asia/Shanghai",
            captured_at="2026-07-14T16:00:00Z",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=92)],
        ),
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-16",
            timezone="Asia/Shanghai",
            captured_at="2026-07-15T16:00:00Z",
            tiers=[ChatGPTSubscriptionTier(name="five_hour", remaining_percent=70)],
        ),
    )
    monkeypatch.setattr(litellm, "credential_list", [credential])
    monkeypatch.setattr(endpoints, "get_budget_reset_timezone", lambda: "Asia/Shanghai")
    monkeypatch.setattr(endpoints, "get_current_budget_date", lambda: "2026-07-16")

    response = await endpoints.get_chatgpt_credential_quota_history(
        request=cast(Request, None),
        fastapi_response=cast(Response, None),
        credential_name="chatgpt-config",
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
        history_store=StaticQuotaHistoryStore(stored_snapshots),
        days=30,
    )

    assert response.timezone == "Asia/Shanghai"
    assert response.current_date == "2026-07-16"
    assert response.snapshots == [
        stored_snapshots[0],
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-16",
            timezone="Asia/Shanghai",
            captured_at="2026-07-15T16:00:00Z",
            tiers=[
                ChatGPTSubscriptionTier(name="five_hour", remaining_percent=72),
                ChatGPTSubscriptionTier(name="seven_day", remaining_percent=88),
            ],
        ),
    ]


@pytest.mark.asyncio
async def test_consume_chatgpt_credential_rate_limit_reset_credit_uses_refreshed_values(monkeypatch):
    credential = CredentialItem(
        credential_name="chatgpt-admin",
        credential_values={"api_key": "old-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    monkeypatch.setattr(litellm, "credential_list", [credential])
    monkeypatch.setattr(
        endpoints,
        "refresh_chatgpt_credential_if_needed",
        lambda credential, user_id: {
            "api_key": "new-token",
            "chatgpt_account_id": "account-id",
        },
    )
    consume = AsyncMock(
        return_value={
            "credential_name": "chatgpt-admin",
            "success": True,
            "credential_status": "valid",
            "outcome": "reset",
            "windows_reset": 2,
            "error": None,
            "queried_at": 1,
        }
    )
    monkeypatch.setattr(endpoints, "consume_chatgpt_rate_limit_reset_credit", consume)

    response = await endpoints.consume_chatgpt_credential_rate_limit_reset_credit(
        request=None,
        fastapi_response=None,
        credential_name="chatgpt-admin",
        body=ChatGPTResetCreditConsumeRequest(idempotency_key="redeem-123", credit_id="credit-1"),
        user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
    )

    assert response["success"] is True
    consume.assert_awaited_once_with(
        credential_name="chatgpt-admin",
        access_token="new-token",
        account_id="account-id",
        idempotency_key="redeem-123",
        credit_id="credit-1",
    )


@pytest.mark.asyncio
async def test_get_chatgpt_credential_subscription_rejects_non_chatgpt_credential(monkeypatch):
    credential = CredentialItem(
        credential_name="openai-admin",
        credential_values={"api_key": "sk-test"},
        credential_info={"custom_llm_provider": "openai"},
    )
    monkeypatch.setattr(litellm, "credential_list", [credential])

    with pytest.raises(ProxyException) as exc_info:
        await endpoints.get_chatgpt_credential_subscription(
            request=None,
            fastapi_response=None,
            credential_name="openai-admin",
            user_api_key_dict=UserAPIKeyAuth(user_id="admin-user"),
        )

    assert exc_info.value.code == "400"
    assert "not a ChatGPT credential" in exc_info.value.message
