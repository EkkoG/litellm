from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from litellm.llms.chatgpt.authenticator import Authenticator
from litellm.types.utils import CredentialItem

CHATGPT_SUBSCRIPTION_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CHATGPT_RATE_LIMIT_RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
CHATGPT_CONSUME_RATE_LIMIT_RESET_CREDIT_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume"
CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY = "chatgpt_daily_quota_snapshot"

CredentialStatus = Literal["valid", "expired"]
ChatGPTResetCreditConsumeOutcome = Literal["reset", "nothing_to_reset", "no_credit", "already_redeemed"]


class ChatGPTSubscriptionTier(BaseModel):
    name: str
    remaining_percent: float
    resets_at: str | None = None


class ChatGPTDailyQuotaSnapshot(BaseModel):
    date: str
    timezone: str
    captured_at: str
    tiers: list[ChatGPTSubscriptionTier]


class ChatGPTSubscriptionStatus(BaseModel):
    credential_name: str
    success: bool
    credential_status: CredentialStatus
    tiers: list[ChatGPTSubscriptionTier]
    rate_limit_reset_credits: ChatGPTRateLimitResetCredits | None = None
    plan_label: str | None = None
    daily_snapshot: ChatGPTDailyQuotaSnapshot | None = None
    error: str | None = None
    queried_at: int


class ChatGPTResetCreditConsumeResponse(BaseModel):
    credential_name: str
    success: bool
    credential_status: CredentialStatus
    outcome: ChatGPTResetCreditConsumeOutcome | None = None
    windows_reset: int = 0
    error: str | None = None
    queried_at: int


class ChatGPTResetCreditConsumeRequest(BaseModel):
    idempotency_key: str
    credit_id: str | None = None


class ChatGPTRateLimitResetCredit(BaseModel):
    id: str
    reset_type: str
    status: str
    granted_at: str
    expires_at: str | None = None
    title: str | None = None
    description: str | None = None


class ChatGPTRateLimitResetCredits(BaseModel):
    available_count: int
    credits: list[ChatGPTRateLimitResetCredit] | None = None


class ChatGPTUsageWindow(BaseModel):
    used_percent: float | None = None
    limit_window_seconds: int | None = None
    reset_at: int | None = None


class ChatGPTRateLimit(BaseModel):
    primary_window: ChatGPTUsageWindow | None = None
    secondary_window: ChatGPTUsageWindow | None = None


class ChatGPTUsageResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    rate_limit: ChatGPTRateLimit | None = None
    rate_limit_reset_credits: ChatGPTRateLimitResetCredits | None = None


class ChatGPTResetCreditConsumeBody(BaseModel):
    code: ChatGPTResetCreditConsumeOutcome
    windows_reset: int = 0


ChatGPTUsageFetcher = Callable[[str, str | None], Awaitable[httpx.Response]]
ChatGPTResetCreditsFetcher = Callable[[str, str | None], Awaitable[httpx.Response]]
ChatGPTResetCreditConsumeFetcher = Callable[[str, str | None, str, str | None], Awaitable[httpx.Response]]


def is_chatgpt_credential(credential: CredentialItem) -> bool:
    return str((credential.credential_info or {}).get("custom_llm_provider") or "").lower() == "chatgpt"


def get_chatgpt_access_token(credential_values: Mapping[str, object]) -> str | None:
    token = credential_values.get("api_key") or credential_values.get("chatgpt_access_token")
    return token if isinstance(token, str) and token else None


def get_chatgpt_account_id(credential_values: Mapping[str, object]) -> str | None:
    account_id = credential_values.get("chatgpt_account_id") or credential_values.get("account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def get_chatgpt_plan_label(credential_values: Mapping[str, object]) -> str | None:
    return _normalize_plan_label(Authenticator().get_plan_type(credential_values))


def get_chatgpt_daily_quota_snapshot(
    credential_info: object,
) -> ChatGPTDailyQuotaSnapshot | None:
    if not isinstance(credential_info, Mapping):
        return None
    raw_snapshot = credential_info.get(CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY)
    if raw_snapshot is None:
        return None
    try:
        return ChatGPTDailyQuotaSnapshot.model_validate(raw_snapshot)
    except ValidationError:
        return None


def parse_chatgpt_subscription_usage(
    credential_name: str,
    body: Mapping[str, object],
    reset_credits: ChatGPTRateLimitResetCredits | None = None,
    plan_label: str | None = None,
    daily_snapshot: ChatGPTDailyQuotaSnapshot | None = None,
) -> ChatGPTSubscriptionStatus:
    usage = ChatGPTUsageResponse.model_validate(body)
    tiers = _parse_rate_limit_tiers(usage.rate_limit)
    subscription_plan = _first_present_string(body, ("subscription_plan",))
    fallback_plan_type = _first_present_string(
        body,
        ("plan_label", "planLabel", "plan", "plan_type", "account_plan"),
    )
    return ChatGPTSubscriptionStatus(
        credential_name=credential_name,
        success=True,
        credential_status="valid",
        tiers=tiers,
        rate_limit_reset_credits=reset_credits or usage.rate_limit_reset_credits,
        plan_label=(
            _normalize_plan_label(subscription_plan, include_quota_variant=True)
            if subscription_plan is not None
            else _normalize_plan_label(fallback_plan_type or plan_label)
        ),
        daily_snapshot=daily_snapshot,
        error=None,
        queried_at=_now_millis(),
    )


async def query_chatgpt_subscription_status(
    credential_name: str,
    access_token: str,
    account_id: str | None,
    fetcher: ChatGPTUsageFetcher | None = None,
    reset_credits_fetcher: ChatGPTResetCreditsFetcher | None = None,
    plan_label: str | None = None,
    daily_snapshot: ChatGPTDailyQuotaSnapshot | None = None,
    include_reset_credits: bool = True,
) -> ChatGPTSubscriptionStatus:
    response = await (fetcher or _fetch_chatgpt_usage)(access_token, account_id)
    if response.status_code in (401, 403):
        return _error_status(
            credential_name=credential_name,
            credential_status="expired",
            error=f"ChatGPT credential expired or rejected (HTTP {response.status_code}). Sign in again.",
        )
    if response.status_code < 200 or response.status_code >= 300:
        return _error_status(
            credential_name=credential_name,
            credential_status="valid",
            error=f"ChatGPT usage query failed (HTTP {response.status_code}): {response.text}",
        )
    try:
        data = response.json()
    except ValueError as exc:
        return _error_status(
            credential_name=credential_name,
            credential_status="valid",
            error=f"Failed to parse ChatGPT usage response: {exc}",
        )
    if not isinstance(data, dict):
        return _error_status(
            credential_name=credential_name,
            credential_status="valid",
            error="ChatGPT usage response was not a JSON object.",
        )
    return parse_chatgpt_subscription_usage(
        credential_name,
        data,
        reset_credits=(
            await _query_chatgpt_rate_limit_reset_credits(
                access_token=access_token,
                account_id=account_id,
                fetcher=reset_credits_fetcher,
            )
            if include_reset_credits
            else None
        ),
        plan_label=plan_label,
        daily_snapshot=daily_snapshot,
    )


async def consume_chatgpt_rate_limit_reset_credit(
    credential_name: str,
    access_token: str,
    account_id: str | None,
    idempotency_key: str,
    credit_id: str | None,
    fetcher: ChatGPTResetCreditConsumeFetcher | None = None,
) -> ChatGPTResetCreditConsumeResponse:
    if not idempotency_key:
        return _reset_credit_consume_error(
            credential_name=credential_name,
            credential_status="valid",
            error="idempotency_key must not be empty.",
        )
    if credit_id == "":
        return _reset_credit_consume_error(
            credential_name=credential_name,
            credential_status="valid",
            error="credit_id must not be empty.",
        )
    response = await (fetcher or _fetch_chatgpt_rate_limit_reset_credit_consume)(
        access_token,
        account_id,
        idempotency_key,
        credit_id,
    )
    if response.status_code in (401, 403):
        return _reset_credit_consume_error(
            credential_name=credential_name,
            credential_status="expired",
            error=f"ChatGPT credential expired or rejected (HTTP {response.status_code}). Sign in again.",
        )
    if response.status_code < 200 or response.status_code >= 300:
        return _reset_credit_consume_error(
            credential_name=credential_name,
            credential_status="valid",
            error=f"ChatGPT reset credit consume failed (HTTP {response.status_code}): {response.text}",
        )
    try:
        data = response.json()
    except ValueError as exc:
        return _reset_credit_consume_error(
            credential_name=credential_name,
            credential_status="valid",
            error=f"Failed to parse ChatGPT reset credit consume response: {exc}",
        )
    try:
        reset_body = ChatGPTResetCreditConsumeBody.model_validate(data)
    except ValidationError as exc:
        return _reset_credit_consume_error(
            credential_name=credential_name,
            credential_status="valid",
            error=f"Failed to parse ChatGPT reset credit consume response: {exc}",
        )
    return ChatGPTResetCreditConsumeResponse(
        credential_name=credential_name,
        success=True,
        credential_status="valid",
        outcome=reset_body.code,
        windows_reset=reset_body.windows_reset,
        error=None,
        queried_at=_now_millis(),
    )


async def _query_chatgpt_rate_limit_reset_credits(
    access_token: str,
    account_id: str | None,
    fetcher: ChatGPTResetCreditsFetcher | None,
) -> ChatGPTRateLimitResetCredits | None:
    response = await (fetcher or _fetch_chatgpt_rate_limit_reset_credits)(access_token, account_id)
    if response.status_code < 200 or response.status_code >= 300:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    try:
        return ChatGPTRateLimitResetCredits.model_validate(data)
    except ValidationError:
        return None


async def _fetch_chatgpt_usage(access_token: str, account_id: str | None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.get(CHATGPT_SUBSCRIPTION_USAGE_URL, headers=_chatgpt_headers(access_token, account_id))


async def _fetch_chatgpt_rate_limit_reset_credits(access_token: str, account_id: str | None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=5.0) as client:
        return await client.get(
            CHATGPT_RATE_LIMIT_RESET_CREDITS_URL, headers=_chatgpt_headers(access_token, account_id)
        )


async def _fetch_chatgpt_rate_limit_reset_credit_consume(
    access_token: str,
    account_id: str | None,
    idempotency_key: str,
    credit_id: str | None,
) -> httpx.Response:
    body = {"redeem_request_id": idempotency_key}
    if credit_id:
        body["credit_id"] = credit_id
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.post(
            CHATGPT_CONSUME_RATE_LIMIT_RESET_CREDIT_URL,
            headers=_chatgpt_headers(access_token, account_id),
            json=body,
        )


def _chatgpt_headers(access_token: str, account_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "codex-cli",
        "Accept": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def _parse_rate_limit_tiers(rate_limit: ChatGPTRateLimit | None) -> list[ChatGPTSubscriptionTier]:
    if rate_limit is None:
        return []
    windows = (rate_limit.primary_window, rate_limit.secondary_window)
    tiers = tuple(_tier_from_window(window) for window in windows)
    return [tier for tier in tiers if tier is not None]


def _tier_from_window(window: ChatGPTUsageWindow | None) -> ChatGPTSubscriptionTier | None:
    if window is None or window.used_percent is None:
        return None
    return ChatGPTSubscriptionTier(
        name=_window_seconds_to_tier_name(window.limit_window_seconds),
        remaining_percent=max(0.0, min(100.0, 100.0 - window.used_percent)),
        resets_at=_unix_seconds_to_iso(window.reset_at),
    )


def _window_seconds_to_tier_name(seconds: int | None) -> str:
    if seconds == 18_000:
        return "five_hour"
    if seconds == 604_800:
        return "seven_day"
    if seconds == 2_592_000:
        return "30_day"
    if seconds is None or seconds <= 0:
        return "unknown"
    hours = seconds // 3600
    if hours >= 24:
        return f"{hours // 24}_day"
    return f"{hours}_hour"


def _unix_seconds_to_iso(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _first_present_string(body: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _normalize_plan_label(plan_type: str | None, include_quota_variant: bool = False) -> str | None:
    if plan_type is None:
        return None
    normalized = plan_type.strip().lower()
    match normalized:
        case "chatgptfreeplan":
            return "Free"
        case "chatgptgoplan":
            return "Go"
        case "chatgptplusplan":
            return "Plus"
        case "chatgptprolite" if include_quota_variant:
            return "Pro (5x)"
        case "chatgptpro" if include_quota_variant:
            return "Pro (20x)"
        case "chatgptprolite" | "chatgptpro":
            return "Pro"
        case _:
            pass
    without_prefix = normalized.removeprefix("chatgpt_").removeprefix("chatgpt-")
    match without_prefix:
        case "free":
            return "Free"
        case "go":
            return "Go"
        case "plus":
            return "Plus"
        case "pro" | "prolite" | "pro_lite":
            return "Pro"
        case _:
            pass
    words = tuple(word for word in without_prefix.replace("-", "_").split("_") if word)
    return " ".join(word.capitalize() for word in words) or None


def _error_status(
    credential_name: str,
    credential_status: CredentialStatus,
    error: str,
) -> ChatGPTSubscriptionStatus:
    return ChatGPTSubscriptionStatus(
        credential_name=credential_name,
        success=False,
        credential_status=credential_status,
        tiers=[],
        rate_limit_reset_credits=None,
        plan_label=None,
        daily_snapshot=None,
        error=error,
        queried_at=_now_millis(),
    )


def _reset_credit_consume_error(
    credential_name: str,
    credential_status: CredentialStatus,
    error: str,
) -> ChatGPTResetCreditConsumeResponse:
    return ChatGPTResetCreditConsumeResponse(
        credential_name=credential_name,
        success=False,
        credential_status=credential_status,
        outcome=None,
        windows_reset=0,
        error=error,
        queried_at=_now_millis(),
    )


def _now_millis() -> int:
    return int(time.time() * 1000)
