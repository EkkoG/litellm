from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from itertools import groupby
from typing import Literal, Protocol, TypedDict, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from litellm.proxy.credential_endpoints.chatgpt_subscription import (
    ChatGPTDailyQuotaSnapshot,
    ChatGPTSubscriptionTier,
)

CHATGPT_QUOTA_HISTORY_RETENTION_DAYS = 90


class ChatGPTQuotaHistoryDays(IntEnum):
    SEVEN = 7
    THIRTY = 30
    NINETY = 90


class ChatGPTQuotaHistoryResponse(BaseModel):
    credential_name: str
    days: ChatGPTQuotaHistoryDays
    timezone: str
    current_date: str
    snapshots: list[ChatGPTDailyQuotaSnapshot]


class ChatGPTQuotaHistoryRecord(Protocol):
    @property
    def credential_name(self) -> str: ...

    @property
    def snapshot_date(self) -> str: ...

    @property
    def timezone(self) -> str: ...

    @property
    def captured_at(self) -> datetime: ...

    @property
    def tier_name(self) -> str: ...

    @property
    def remaining_percent(self) -> float: ...

    @property
    def resets_at(self) -> datetime | None: ...


class ChatGPTQuotaHistoryKey(TypedDict):
    credential_name: str
    snapshot_date: str
    tier_name: str


class ChatGPTQuotaHistoryWhereUnique(TypedDict):
    credential_name_snapshot_date_tier_name: ChatGPTQuotaHistoryKey


class ChatGPTQuotaHistoryCreate(TypedDict):
    credential_name: str
    snapshot_date: str
    timezone: str
    captured_at: datetime
    tier_name: str
    remaining_percent: float
    resets_at: datetime | None


class ChatGPTQuotaHistoryUpdate(TypedDict):
    timezone: str
    captured_at: datetime
    remaining_percent: float
    resets_at: datetime | None


class ChatGPTQuotaHistoryUpsert(TypedDict):
    create: ChatGPTQuotaHistoryCreate
    update: ChatGPTQuotaHistoryUpdate


class ChatGPTQuotaHistoryDateFilter(TypedDict, total=False):
    lt: str
    gte: str


class ChatGPTQuotaHistoryWhere(TypedDict, total=False):
    credential_name: str
    snapshot_date: ChatGPTQuotaHistoryDateFilter


class ChatGPTQuotaHistoryOrder(TypedDict):
    snapshot_date: Literal["asc"]


@runtime_checkable
class ChatGPTQuotaHistoryTable(Protocol):
    async def upsert(
        self,
        *,
        where: ChatGPTQuotaHistoryWhereUnique,
        data: ChatGPTQuotaHistoryUpsert,
    ) -> object: ...

    async def delete_many(self, *, where: ChatGPTQuotaHistoryWhere) -> int: ...

    async def find_many(
        self,
        *,
        where: ChatGPTQuotaHistoryWhere,
        order: ChatGPTQuotaHistoryOrder,
    ) -> Sequence[ChatGPTQuotaHistoryRecord]: ...


class ChatGPTQuotaHistoryReader(Protocol):
    async def list(
        self,
        *,
        credential_name: str,
        days: ChatGPTQuotaHistoryDays,
        timezone_name: str,
        now: datetime | None = None,
    ) -> tuple[ChatGPTDailyQuotaSnapshot, ...]: ...


class DatabaseChatGPTQuotaHistoryStore:
    def __init__(self, table: ChatGPTQuotaHistoryTable) -> None:
        self._table = table

    async def save(self, credential_name: str, snapshot: ChatGPTDailyQuotaSnapshot) -> None:
        captured_at = _parse_datetime(snapshot.captured_at)
        await asyncio.gather(
            *(
                self._table.upsert(
                    where={
                        "credential_name_snapshot_date_tier_name": {
                            "credential_name": credential_name,
                            "snapshot_date": snapshot.date,
                            "tier_name": tier.name,
                        }
                    },
                    data={
                        "create": {
                            "credential_name": credential_name,
                            "snapshot_date": snapshot.date,
                            "timezone": snapshot.timezone,
                            "captured_at": captured_at,
                            "tier_name": tier.name,
                            "remaining_percent": tier.remaining_percent,
                            "resets_at": _parse_optional_datetime(tier.resets_at),
                        },
                        "update": {
                            "timezone": snapshot.timezone,
                            "captured_at": captured_at,
                            "remaining_percent": tier.remaining_percent,
                            "resets_at": _parse_optional_datetime(tier.resets_at),
                        },
                    },
                )
                for tier in snapshot.tiers
            )
        )

    async def prune(self, reference_date: str) -> None:
        retention_cutoff = (
            datetime.fromisoformat(reference_date).date() - timedelta(days=CHATGPT_QUOTA_HISTORY_RETENTION_DAYS - 1)
        ).isoformat()
        await self._table.delete_many(where={"snapshot_date": {"lt": retention_cutoff}})

    async def list(
        self,
        *,
        credential_name: str,
        days: ChatGPTQuotaHistoryDays,
        timezone_name: str,
        now: datetime | None = None,
    ) -> tuple[ChatGPTDailyQuotaSnapshot, ...]:
        current_time = now or datetime.now(timezone.utc)
        first_date = (current_time.astimezone(ZoneInfo(timezone_name)).date() - timedelta(days=days - 1)).isoformat()
        rows = await self._table.find_many(
            where={"credential_name": credential_name, "snapshot_date": {"gte": first_date}},
            order={"snapshot_date": "asc"},
        )
        grouped_rows = tuple(
            (snapshot_date, tuple(group)) for snapshot_date, group in groupby(rows, key=lambda row: row.snapshot_date)
        )
        return tuple(_decode_snapshot(snapshot_date, records) for snapshot_date, records in grouped_rows)


def merge_chatgpt_quota_snapshots(
    stored_snapshots: Sequence[ChatGPTDailyQuotaSnapshot],
    current_snapshot: ChatGPTDailyQuotaSnapshot | None,
) -> tuple[ChatGPTDailyQuotaSnapshot, ...]:
    if current_snapshot is None:
        return tuple(stored_snapshots)
    stored_current_snapshot = next(
        (snapshot for snapshot in stored_snapshots if snapshot.date == current_snapshot.date),
        None,
    )
    stored_current_tiers = stored_current_snapshot.tiers if stored_current_snapshot is not None else []
    merged_tiers_by_name = {tier.name: tier for tier in stored_current_tiers} | {
        tier.name: tier for tier in current_snapshot.tiers
    }
    merged_current_snapshot = current_snapshot.model_copy(
        update={"tiers": sorted(merged_tiers_by_name.values(), key=lambda tier: tier.name)}
    )
    other_snapshots = tuple(snapshot for snapshot in stored_snapshots if snapshot.date != current_snapshot.date)
    return tuple(sorted((*other_snapshots, merged_current_snapshot), key=lambda snapshot: snapshot.date))


def _decode_snapshot(
    snapshot_date: str,
    records: Sequence[ChatGPTQuotaHistoryRecord],
) -> ChatGPTDailyQuotaSnapshot:
    first_record = records[0]
    return ChatGPTDailyQuotaSnapshot(
        date=snapshot_date,
        timezone=first_record.timezone,
        captured_at=_datetime_to_iso(first_record.captured_at),
        tiers=[
            ChatGPTSubscriptionTier(
                name=record.tier_name,
                remaining_percent=record.remaining_percent,
                resets_at=_datetime_to_iso(record.resets_at) if record.resets_at is not None else None,
            )
            for record in sorted(records, key=lambda record: record.tier_name)
        ],
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return _parse_datetime(value) if value is not None else None


def _datetime_to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
