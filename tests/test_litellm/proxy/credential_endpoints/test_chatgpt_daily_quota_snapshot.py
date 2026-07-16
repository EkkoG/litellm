import json
from collections.abc import Mapping
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from litellm.proxy.credential_endpoints.chatgpt_daily_quota_snapshot import (
    CHATGPT_DAILY_QUOTA_SNAPSHOT_JOB_ID,
    record_chatgpt_daily_quota_snapshots,
    schedule_chatgpt_daily_quota_snapshot_job,
)
from litellm.proxy.credential_endpoints.chatgpt_subscription import (
    CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY,
    ChatGPTDailyQuotaSnapshot,
    ChatGPTSubscriptionStatus,
    ChatGPTSubscriptionTier,
)
from litellm.types.utils import CredentialItem


@pytest.mark.asyncio
async def test_record_chatgpt_daily_quota_snapshots_uses_global_day_timezone():
    credential = CredentialItem(
        credential_name="chatgpt-admin",
        credential_values={"api_key": "access-token", "chatgpt_account_id": "account-id"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    repository = Mock()
    repository.find_by_name = AsyncMock(return_value=credential)
    repository.update_by_name = AsyncMock()
    history_store = Mock()
    history_store.save = AsyncMock()
    history_store.prune = AsyncMock()

    async def status_fetcher(
        credential_name: str,
        access_token: str,
        account_id: str | None,
    ) -> ChatGPTSubscriptionStatus:
        assert credential_name == "chatgpt-admin"
        assert access_token == "access-token"
        assert account_id == "account-id"
        return ChatGPTSubscriptionStatus(
            credential_name=credential_name,
            success=True,
            credential_status="valid",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=72.4)],
            plan_label="Pro",
            queried_at=1,
        )

    def credential_refresher(credential: CredentialItem, user_id: str | None) -> Mapping[str, object]:
        assert credential.credential_name == "chatgpt-admin"
        assert user_id == "litellm_proxy"
        return {"api_key": "access-token", "chatgpt_account_id": "account-id"}

    snapshots = await record_chatgpt_daily_quota_snapshots(
        credentials=(credential,),
        repository=repository,
        history_store=history_store,
        status_fetcher=status_fetcher,
        credential_refresher=credential_refresher,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc),
    )

    assert snapshots == (
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-16",
            timezone="Asia/Shanghai",
            captured_at="2026-07-15T16:00:00Z",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=72.4)],
        ),
    )
    stored = repository.update_by_name.await_args.kwargs["data"]
    stored_info = json.loads(stored["credential_info"])
    assert stored_info[CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY]["date"] == "2026-07-16"
    assert stored_info[CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY]["tiers"] == [
        {"name": "seven_day", "remaining_percent": 72.4, "resets_at": None}
    ]
    history_store.save.assert_awaited_once_with("chatgpt-admin", snapshots[0])
    history_store.prune.assert_awaited_once_with("2026-07-16")


@pytest.mark.asyncio
async def test_record_chatgpt_daily_quota_snapshots_keeps_latest_snapshot_when_history_save_fails():
    credential = CredentialItem(
        credential_name="chatgpt-admin",
        credential_values={"api_key": "access-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    repository = Mock()
    repository.find_by_name = AsyncMock(return_value=credential)
    repository.update_by_name = AsyncMock()
    history_store = Mock()
    history_store.save = AsyncMock(side_effect=RuntimeError("history unavailable"))
    history_store.prune = AsyncMock()

    async def status_fetcher(
        credential_name: str,
        access_token: str,
        account_id: str | None,
    ) -> ChatGPTSubscriptionStatus:
        return ChatGPTSubscriptionStatus(
            credential_name=credential_name,
            success=True,
            credential_status="valid",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=72.4)],
            queried_at=1,
        )

    def credential_refresher(credential: CredentialItem, user_id: str | None) -> Mapping[str, object]:
        return {"api_key": "access-token"}

    snapshots = await record_chatgpt_daily_quota_snapshots(
        credentials=(credential,),
        repository=repository,
        history_store=history_store,
        status_fetcher=status_fetcher,
        credential_refresher=credential_refresher,
        timezone_name="UTC",
        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert len(snapshots) == 1
    history_store.save.assert_awaited_once_with("chatgpt-admin", snapshots[0])
    history_store.prune.assert_awaited_once_with("2026-07-15")
    repository.update_by_name.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_chatgpt_daily_quota_snapshots_updates_config_credential_runtime_state():
    credential = CredentialItem(
        credential_name="chatgpt-config",
        credential_values={"api_key": "access-token"},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    runtime_credential_upsert = Mock()

    async def status_fetcher(
        credential_name: str,
        access_token: str,
        account_id: str | None,
    ) -> ChatGPTSubscriptionStatus:
        assert credential_name == "chatgpt-config"
        assert access_token == "access-token"
        assert account_id is None
        return ChatGPTSubscriptionStatus(
            credential_name=credential_name,
            success=True,
            credential_status="valid",
            tiers=[ChatGPTSubscriptionTier(name="five_hour", remaining_percent=64)],
            queried_at=1,
        )

    def credential_refresher(credential: CredentialItem, user_id: str | None) -> Mapping[str, object]:
        assert user_id == "litellm_proxy"
        return {"api_key": "access-token"}

    snapshots = await record_chatgpt_daily_quota_snapshots(
        credentials=(credential,),
        repository=None,
        status_fetcher=status_fetcher,
        credential_refresher=credential_refresher,
        runtime_credential_upsert=runtime_credential_upsert,
        timezone_name="UTC",
        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert snapshots[0].date == "2026-07-15"
    runtime_credential = runtime_credential_upsert.call_args.args[0][0]
    assert runtime_credential.credential_info[CHATGPT_DAILY_QUOTA_SNAPSHOT_INFO_KEY]["tiers"] == [
        {"name": "five_hour", "remaining_percent": 64.0, "resets_at": None}
    ]


@pytest.mark.asyncio
async def test_record_chatgpt_daily_quota_snapshots_prunes_history_without_active_credentials():
    history_store = Mock()
    history_store.prune = AsyncMock()

    snapshots = await record_chatgpt_daily_quota_snapshots(
        credentials=(),
        repository=None,
        history_store=history_store,
        timezone_name="UTC",
        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert snapshots == ()
    history_store.prune.assert_awaited_once_with("2026-07-15")


@pytest.mark.asyncio
async def test_schedule_chatgpt_daily_quota_snapshot_job_checks_current_global_midnight():
    scheduler = AsyncIOScheduler()
    repository = Mock()
    credential = CredentialItem(
        credential_name="chatgpt-admin",
        credential_values={},
        credential_info={"custom_llm_provider": "chatgpt"},
    )
    credential_source = Mock(return_value=(credential,))
    recorder = AsyncMock(return_value=())
    history_store = Mock()
    timezone_provider = Mock(return_value="Asia/Shanghai")
    now_provider = Mock(return_value=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc))

    schedule_chatgpt_daily_quota_snapshot_job(
        scheduler=scheduler,
        repository=repository,
        history_store=history_store,
        credential_source=credential_source,
        recorder=recorder,
        timezone_provider=timezone_provider,
        now_provider=now_provider,
    )

    job = scheduler.get_job(CHATGPT_DAILY_QUOTA_SNAPSHOT_JOB_ID)
    assert job is not None
    assert str(job.trigger) == "cron[minute='*']"
    assert job.trigger.timezone == ZoneInfo("UTC")

    await job.func(*job.args)

    recorder.assert_awaited_once_with(
        credentials=(credential,),
        repository=repository,
        history_store=history_store,
        timezone_name="Asia/Shanghai",
    )


@pytest.mark.asyncio
async def test_scheduled_chatgpt_daily_quota_snapshot_skips_outside_global_midnight():
    scheduler = AsyncIOScheduler()
    recorder = AsyncMock(return_value=())

    schedule_chatgpt_daily_quota_snapshot_job(
        scheduler=scheduler,
        repository=Mock(),
        credential_source=Mock(return_value=()),
        recorder=recorder,
        timezone_provider=Mock(return_value="Asia/Shanghai"),
        now_provider=Mock(return_value=datetime(2026, 7, 15, 16, 1, tzinfo=timezone.utc)),
    )

    job = scheduler.get_job(CHATGPT_DAILY_QUOTA_SNAPSHOT_JOB_ID)
    assert job is not None

    await job.func(*job.args)

    recorder.assert_not_awaited()
