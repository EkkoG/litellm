from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

import litellm

from litellm._logging import verbose_proxy_logger
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.proxy.common_utils.timezone_utils import get_budget_reset_timezone
from litellm.proxy.credential_endpoints.chatgpt_credential_utils import refresh_chatgpt_credential_if_needed
from litellm.proxy.credential_endpoints.chatgpt_subscription import (
    CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY,
    ChatGPTDailyQuotaSnapshot,
    ChatGPTSubscriptionStatus,
    get_chatgpt_access_token,
    get_chatgpt_account_id,
    is_chatgpt_credential,
    query_chatgpt_subscription_status,
)
from litellm.proxy.credential_endpoints.credential_writer import CredentialNotFound, CredentialSaved, CredentialWriter
from litellm.repositories.credentials_repository import CredentialsRepository
from litellm.types.utils import CredentialItem

CHATGPT_DAILY_QUOTA_SNAPSHOT_JOB_ID = "chatgpt_daily_quota_snapshot_job"

ChatGPTSnapshotStatusFetcher = Callable[[str, str, str | None], Awaitable[ChatGPTSubscriptionStatus]]
ChatGPTCredentialRefresher = Callable[[CredentialItem, str | None], Mapping[str, object]]
ChatGPTCredentialSource = Callable[[], Sequence[CredentialItem]]
ChatGPTTimezoneProvider = Callable[[], str]
ChatGPTNowProvider = Callable[[], datetime]
ChatGPTRuntimeCredentialUpsert = Callable[[Sequence[CredentialItem]], None]


class ChatGPTSnapshotRecorder(Protocol):
    async def __call__(
        self,
        *,
        credentials: Sequence[CredentialItem],
        repository: CredentialsRepository | None,
        history_store: ChatGPTQuotaHistoryStore | None,
        timezone_name: str,
    ) -> tuple[ChatGPTDailyQuotaSnapshot, ...]: ...


class ChatGPTQuotaHistoryStore(Protocol):
    async def save(self, credential_name: str, snapshot: ChatGPTDailyQuotaSnapshot) -> None: ...

    async def prune(self, reference_date: str) -> None: ...


class ChatGPTSnapshotScheduler(Protocol):
    def add_job(
        self,
        func: Callable[
            [
                CredentialsRepository | None,
                ChatGPTQuotaHistoryStore | None,
                ChatGPTCredentialSource,
                ChatGPTSnapshotRecorder,
                ChatGPTTimezoneProvider,
                ChatGPTNowProvider,
            ],
            Awaitable[None],
        ],
        trigger: str,
        *,
        minute: str,
        timezone: ZoneInfo,
        args: list[object],
        id: str,
        replace_existing: bool,
    ) -> object: ...


async def record_chatgpt_daily_quota_snapshots(
    credentials: Sequence[CredentialItem],
    repository: CredentialsRepository | None,
    history_store: ChatGPTQuotaHistoryStore | None = None,
    status_fetcher: ChatGPTSnapshotStatusFetcher | None = None,
    credential_refresher: ChatGPTCredentialRefresher | None = None,
    runtime_credential_upsert: ChatGPTRuntimeCredentialUpsert | None = None,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> tuple[ChatGPTDailyQuotaSnapshot, ...]:
    snapshot_timezone = timezone_name or get_budget_reset_timezone()
    capture_time = now or datetime.now(timezone.utc)
    effective_status_fetcher = status_fetcher or _query_chatgpt_snapshot_status
    effective_credential_refresher = credential_refresher or _refresh_chatgpt_credential
    effective_runtime_credential_upsert = runtime_credential_upsert or _upsert_runtime_credentials
    writer = CredentialWriter(repository) if repository is not None else None
    results = await asyncio.gather(
        *(
            _record_chatgpt_daily_quota_snapshot(
                credential=credential,
                writer=writer,
                history_store=history_store,
                status_fetcher=effective_status_fetcher,
                credential_refresher=effective_credential_refresher,
                runtime_credential_upsert=effective_runtime_credential_upsert,
                timezone_name=snapshot_timezone,
                capture_time=capture_time,
            )
            for credential in credentials
            if is_chatgpt_credential(credential)
        )
    )
    reference_date = capture_time.astimezone(ZoneInfo(snapshot_timezone)).date().isoformat()
    await _prune_history_snapshots(history_store, reference_date)
    return tuple(snapshot for snapshot in results if snapshot is not None)


def schedule_chatgpt_daily_quota_snapshot_job(
    scheduler: ChatGPTSnapshotScheduler,
    repository: CredentialsRepository | None,
    history_store: ChatGPTQuotaHistoryStore | None = None,
    credential_source: ChatGPTCredentialSource | None = None,
    recorder: ChatGPTSnapshotRecorder | None = None,
    timezone_provider: ChatGPTTimezoneProvider | None = None,
    now_provider: ChatGPTNowProvider | None = None,
) -> None:
    scheduler.add_job(
        _run_scheduled_chatgpt_daily_quota_snapshot,
        "cron",
        minute="*",
        timezone=ZoneInfo("UTC"),
        args=[
            repository,
            history_store,
            credential_source or _get_runtime_credentials,
            recorder or record_chatgpt_daily_quota_snapshots,
            timezone_provider or get_budget_reset_timezone,
            now_provider or _utc_now,
        ],
        id=CHATGPT_DAILY_QUOTA_SNAPSHOT_JOB_ID,
        replace_existing=True,
    )


async def _run_scheduled_chatgpt_daily_quota_snapshot(
    repository: CredentialsRepository | None,
    history_store: ChatGPTQuotaHistoryStore | None,
    credential_source: ChatGPTCredentialSource,
    recorder: ChatGPTSnapshotRecorder,
    timezone_provider: ChatGPTTimezoneProvider,
    now_provider: ChatGPTNowProvider,
) -> None:
    capture_time = now_provider()
    timezone_name = timezone_provider()
    local_time = capture_time.astimezone(ZoneInfo(timezone_name))
    if local_time.hour != 0 or local_time.minute != 0:
        return
    await recorder(
        credentials=credential_source(),
        repository=repository,
        history_store=history_store,
        timezone_name=timezone_name,
    )


def _get_runtime_credentials() -> Sequence[CredentialItem]:
    return tuple(litellm.credential_list)


def _refresh_chatgpt_credential(credential: CredentialItem, user_id: str | None) -> Mapping[str, object]:
    return refresh_chatgpt_credential_if_needed(credential=credential, user_id=user_id)


def _upsert_runtime_credentials(credentials: Sequence[CredentialItem]) -> None:
    CredentialAccessor.upsert_credentials(list(credentials))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _query_chatgpt_snapshot_status(
    credential_name: str,
    access_token: str,
    account_id: str | None,
) -> ChatGPTSubscriptionStatus:
    return await query_chatgpt_subscription_status(
        credential_name=credential_name,
        access_token=access_token,
        account_id=account_id,
        include_reset_credits=False,
    )


async def _record_chatgpt_daily_quota_snapshot(
    credential: CredentialItem,
    writer: CredentialWriter | None,
    history_store: ChatGPTQuotaHistoryStore | None,
    status_fetcher: ChatGPTSnapshotStatusFetcher,
    credential_refresher: ChatGPTCredentialRefresher,
    runtime_credential_upsert: ChatGPTRuntimeCredentialUpsert,
    timezone_name: str,
    capture_time: datetime,
) -> ChatGPTDailyQuotaSnapshot | None:
    try:
        credential_values = credential_refresher(credential, "litellm_proxy")
        access_token = get_chatgpt_access_token(credential_values)
        if access_token is None:
            return None
        status = await status_fetcher(
            credential.credential_name,
            access_token,
            get_chatgpt_account_id(credential_values),
        )
        if not status.success:
            return None
        snapshot = ChatGPTDailyQuotaSnapshot(
            date=capture_time.astimezone(ZoneInfo(timezone_name)).date().isoformat(),
            timezone=timezone_name,
            captured_at=_datetime_to_iso(capture_time),
            tiers=status.tiers,
        )
        runtime_credential_upsert(
            (
                CredentialItem(
                    credential_name=credential.credential_name,
                    credential_values=credential.credential_values,
                    credential_info={
                        **(credential.credential_info or {}),
                        CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY: snapshot.model_dump(),
                    },
                ),
            )
        )
        await _save_history_snapshot(history_store, credential.credential_name, snapshot)
        if writer is None:
            return snapshot
        result = await writer.patch(
            credential_name=credential.credential_name,
            patch=CredentialItem(
                credential_name=credential.credential_name,
                credential_values={},
                credential_info={CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY: snapshot.model_dump()},
            ),
            actor_id="litellm_proxy",
        )
        return snapshot if isinstance(result, (CredentialSaved, CredentialNotFound)) else None
    except Exception as exc:
        verbose_proxy_logger.warning(
            "Failed to record daily ChatGPT quota snapshot for credential %s: %s",
            credential.credential_name,
            exc,
        )
        return None


async def _save_history_snapshot(
    history_store: ChatGPTQuotaHistoryStore | None,
    credential_name: str,
    snapshot: ChatGPTDailyQuotaSnapshot,
) -> None:
    if history_store is None:
        return
    try:
        await history_store.save(credential_name, snapshot)
    except Exception as exc:
        verbose_proxy_logger.warning(
            "Failed to persist ChatGPT quota history for credential %s: %s",
            credential_name,
            exc,
        )


async def _prune_history_snapshots(
    history_store: ChatGPTQuotaHistoryStore | None,
    reference_date: str,
) -> None:
    if history_store is None:
        return
    try:
        await history_store.prune(reference_date)
    except Exception as exc:
        verbose_proxy_logger.warning("Failed to prune ChatGPT quota history: %s", exc)


def _datetime_to_iso(value: datetime) -> str:
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat().replace("+00:00", "Z")
