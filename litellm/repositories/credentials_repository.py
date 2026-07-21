"""
Credentials repository for database operations on LiteLLM_CredentialsTable.

This is the only place that talks to ``litellm_credentialstable``. Encryption of
credential values is the caller's responsibility (see ``CredentialHelperUtils``),
so reads return the stored values verbatim.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict, Optional

from litellm.models.credentials import CredentialItem


class _TransactionPrismaClient:
    def __init__(self, transaction: Any) -> None:
        self.db = transaction


class CredentialsRepository:
    """Repository for credentials database operations, keyed by credential name."""

    def __init__(self, prisma_client: Any):
        self._prisma_client = prisma_client

    @property
    def prisma_client(self) -> Any:
        if self._prisma_client is None:
            raise RuntimeError("No DB Connected. See - https://docs.litellm.ai/docs/proxy/virtual_keys")
        return self._prisma_client

    @property
    def table(self) -> Any:
        return self.prisma_client.db.litellm_credentialstable

    @staticmethod
    def _to_model(record: Any) -> Optional[CredentialItem]:
        if record is None:
            return None
        data = record.dict() if hasattr(record, "dict") else dict(record)
        return CredentialItem(
            credential_name=data["credential_name"],
            credential_values=data.get("credential_values") or {},
            credential_info=data.get("credential_info") or {},
        )

    async def find_all(self) -> Any:
        return await self.table.find_many()

    async def create(self, data: Dict[str, Any]) -> Any:
        return await self.table.create(data=data)

    async def find_by_name(self, credential_name: str) -> Optional[CredentialItem]:
        record = await self.table.find_unique(where={"credential_name": credential_name})
        return self._to_model(record)

    @staticmethod
    def advisory_lock_key(credential_name: str, provider: str = "managed-oauth") -> int:
        return int.from_bytes(
            hashlib.blake2b(
                f"litellm:oauth-credential:{provider}:{credential_name}".encode(),
                digest_size=8,
            ).digest(),
            "big",
            signed=True,
        )

    @asynccontextmanager
    async def locked_by_name(self, credential_name: str) -> AsyncIterator["CredentialsRepository"]:
        async with self.prisma_client.db.tx(timeout=timedelta(seconds=30)) as transaction:
            await transaction.execute_raw(
                "SELECT pg_advisory_xact_lock($1::bigint)",
                self.advisory_lock_key(credential_name),
            )
            yield CredentialsRepository(_TransactionPrismaClient(transaction))

    async def update_by_name(self, credential_name: str, data: Dict[str, Any]) -> Any:
        return await self.table.update(where={"credential_name": credential_name}, data=data)

    @property
    def supports_json_merge_update(self) -> bool:
        try:
            return self.prisma_client.db is not None
        except RuntimeError:
            return False

    async def merge_update_by_name(
        self,
        credential_name: str,
        new_name: str,
        credential_values_patch: Dict[str, Any],
        credential_info_patch: Dict[str, Any],
        updated_by: str | None,
    ) -> bool:
        """Merge top-level keys into the stored JSON documents in one UPDATE.

        Unlike find_by_name + update_by_name, the merge happens inside the
        database, so concurrent patches to disjoint keys do not overwrite each
        other. Returns False when no credential matches ``credential_name``.
        """
        import json as _json

        result = await self.prisma_client.db.execute_raw(
            """
            UPDATE "LiteLLM_CredentialsTable"
            SET
                credential_name = $2,
                credential_values = COALESCE(credential_values, '{}'::jsonb) || $3::jsonb,
                credential_info = COALESCE(credential_info, '{}'::jsonb) || $4::jsonb,
                updated_by = $5
            WHERE credential_name = $1
            """,
            credential_name,
            new_name,
            _json.dumps(credential_values_patch),
            _json.dumps(credential_info_patch),
            updated_by,
        )
        return int(result) > 0

    async def delete_by_name(self, credential_name: str) -> Any:
        return await self.table.delete(where={"credential_name": credential_name})
