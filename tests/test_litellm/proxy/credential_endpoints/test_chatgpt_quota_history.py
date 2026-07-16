from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock, call, create_autospec

import pytest

from litellm.proxy.credential_endpoints.chatgpt_quota_history import (
    ChatGPTQuotaHistoryRecord,
    ChatGPTQuotaHistoryTable,
    DatabaseChatGPTQuotaHistoryStore,
    merge_chatgpt_quota_snapshots,
)
from litellm.proxy.credential_endpoints.chatgpt_subscription import (
    ChatGPTDailyQuotaSnapshot,
    ChatGPTSubscriptionTier,
)


@dataclass(frozen=True)
class QuotaHistoryRecord:
    credential_name: str
    snapshot_date: str
    timezone: str
    captured_at: datetime
    tier_name: str
    remaining_percent: float
    resets_at: datetime | None


def create_quota_history_table_mock() -> tuple[ChatGPTQuotaHistoryTable, AsyncMock, AsyncMock, AsyncMock]:
    table = cast(ChatGPTQuotaHistoryTable, create_autospec(ChatGPTQuotaHistoryTable, instance=True))
    return (
        table,
        cast(AsyncMock, table.upsert),
        cast(AsyncMock, table.delete_many),
        cast(AsyncMock, table.find_many),
    )


@pytest.mark.asyncio
async def test_save_upserts_each_tier_and_prune_removes_all_records_older_than_90_days():
    table, upsert, delete_many, _ = create_quota_history_table_mock()
    store = DatabaseChatGPTQuotaHistoryStore(table)
    snapshot = ChatGPTDailyQuotaSnapshot(
        date="2026-07-16",
        timezone="Asia/Shanghai",
        captured_at="2026-07-15T16:00:00Z",
        tiers=[
            ChatGPTSubscriptionTier(name="five_hour", remaining_percent=75.5),
            ChatGPTSubscriptionTier(
                name="seven_day",
                remaining_percent=90,
                resets_at="2026-07-20T00:00:00Z",
            ),
        ],
    )

    await store.save("chatgpt-admin", snapshot)
    await store.prune("2026-07-16")

    upsert.assert_has_awaits(
        [
            call(
                where={
                    "credential_name_snapshot_date_tier_name": {
                        "credential_name": "chatgpt-admin",
                        "snapshot_date": "2026-07-16",
                        "tier_name": "five_hour",
                    }
                },
                data={
                    "create": {
                        "credential_name": "chatgpt-admin",
                        "snapshot_date": "2026-07-16",
                        "timezone": "Asia/Shanghai",
                        "captured_at": datetime(2026, 7, 15, 16, tzinfo=timezone.utc),
                        "tier_name": "five_hour",
                        "remaining_percent": 75.5,
                        "resets_at": None,
                    },
                    "update": {
                        "timezone": "Asia/Shanghai",
                        "captured_at": datetime(2026, 7, 15, 16, tzinfo=timezone.utc),
                        "remaining_percent": 75.5,
                        "resets_at": None,
                    },
                },
            ),
            call(
                where={
                    "credential_name_snapshot_date_tier_name": {
                        "credential_name": "chatgpt-admin",
                        "snapshot_date": "2026-07-16",
                        "tier_name": "seven_day",
                    }
                },
                data={
                    "create": {
                        "credential_name": "chatgpt-admin",
                        "snapshot_date": "2026-07-16",
                        "timezone": "Asia/Shanghai",
                        "captured_at": datetime(2026, 7, 15, 16, tzinfo=timezone.utc),
                        "tier_name": "seven_day",
                        "remaining_percent": 90,
                        "resets_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
                    },
                    "update": {
                        "timezone": "Asia/Shanghai",
                        "captured_at": datetime(2026, 7, 15, 16, tzinfo=timezone.utc),
                        "remaining_percent": 90,
                        "resets_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
                    },
                },
            ),
        ]
    )
    delete_many.assert_awaited_once_with(where={"snapshot_date": {"lt": "2026-04-18"}})


@pytest.mark.asyncio
async def test_list_groups_tiers_into_daily_snapshots():
    records: tuple[ChatGPTQuotaHistoryRecord, ...] = (
        QuotaHistoryRecord(
            credential_name="chatgpt-admin",
            snapshot_date="2026-07-15",
            timezone="UTC",
            captured_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            tier_name="seven_day",
            remaining_percent=88,
            resets_at=None,
        ),
        QuotaHistoryRecord(
            credential_name="chatgpt-admin",
            snapshot_date="2026-07-16",
            timezone="UTC",
            captured_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            tier_name="five_hour",
            remaining_percent=70,
            resets_at=None,
        ),
        QuotaHistoryRecord(
            credential_name="chatgpt-admin",
            snapshot_date="2026-07-16",
            timezone="UTC",
            captured_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            tier_name="seven_day",
            remaining_percent=82.5,
            resets_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        ),
    )
    table, _, _, find_many = create_quota_history_table_mock()
    find_many.return_value = records
    store = DatabaseChatGPTQuotaHistoryStore(table)

    snapshots = await store.list(
        credential_name="chatgpt-admin",
        days=7,
        timezone_name="UTC",
        now=datetime(2026, 7, 16, 12, tzinfo=timezone.utc),
    )

    find_many.assert_awaited_once_with(
        where={"credential_name": "chatgpt-admin", "snapshot_date": {"gte": "2026-07-10"}},
        order={"snapshot_date": "asc"},
    )
    assert snapshots == (
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-15",
            timezone="UTC",
            captured_at="2026-07-15T00:00:00Z",
            tiers=[ChatGPTSubscriptionTier(name="seven_day", remaining_percent=88)],
        ),
        ChatGPTDailyQuotaSnapshot(
            date="2026-07-16",
            timezone="UTC",
            captured_at="2026-07-16T00:00:00Z",
            tiers=[
                ChatGPTSubscriptionTier(name="five_hour", remaining_percent=70),
                ChatGPTSubscriptionTier(
                    name="seven_day",
                    remaining_percent=82.5,
                    resets_at="2026-07-20T00:00:00Z",
                ),
            ],
        ),
    )


def test_merge_chatgpt_quota_snapshots_fills_partial_database_day_from_runtime_snapshot():
    stored_snapshot = ChatGPTDailyQuotaSnapshot(
        date="2026-07-16",
        timezone="UTC",
        captured_at="2026-07-16T00:00:00Z",
        tiers=[ChatGPTSubscriptionTier(name="five_hour", remaining_percent=70)],
    )
    current_snapshot = ChatGPTDailyQuotaSnapshot(
        date="2026-07-16",
        timezone="UTC",
        captured_at="2026-07-16T00:00:00Z",
        tiers=[
            ChatGPTSubscriptionTier(name="five_hour", remaining_percent=72),
            ChatGPTSubscriptionTier(name="seven_day", remaining_percent=88),
        ],
    )

    snapshots = merge_chatgpt_quota_snapshots((stored_snapshot,), current_snapshot)

    assert snapshots == (current_snapshot,)
