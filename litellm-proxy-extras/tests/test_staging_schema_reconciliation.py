import hashlib
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "litellm_proxy_extras" / "migrations"

HISTORICAL_MIGRATION_CHECKSUMS = {
    "20260712021735_add_device_login_state": "c59fa3743eea4d329abc667e87df3e1f1909784d63f16aeb3b9886ea8363eca7",
    "20260716000000_add_chatgpt_quota_snapshot_history": "925a197c025f1c96287f5fff4615a6de788052afba251e3f2400e664623d8d58",
    "20260727185619_add_managed_proxy_table": "863fba4f09808219e84b4063d471a2ab8893fdbf3068a0bd26e9a882767c977f",
    "20260731000000_add_chatgpt_reset_credit_usage": "c5241602ada1d43c4c55f1c8f8a8aedbea883a232aa18be22547906831eca988",
    "20260803000455_add_encrypted_content_recovery_table": "2e4b1789ebefb5b12942bb3c2795dc8806d7df22ee629528f7dbf158ee9b581e",
    "20260803010000_add_proxy_model_soft_delete": "5dc3bcfc5a3efa3ac445bf6daea1ef4a0f17dcdad81b18f8155448fe17d13776",
    "20260804010000_revert_proxy_model_soft_delete": "c4018fd6f95b940c5c4ece7e927804b017f4e92de8c3fd26f1e50143a6513524",
}

EXPECTED_CLEANUP_STATEMENTS = (
    'ALTER TABLE "LiteLLM_CredentialsTable" DROP CONSTRAINT IF EXISTS "LiteLLM_CredentialsTable_proxy_id_fkey"',
    'DROP INDEX IF EXISTS "LiteLLM_CredentialsTable_proxy_id_key"',
    'ALTER TABLE "LiteLLM_CredentialsTable" DROP COLUMN IF EXISTS "proxy_id"',
    'DROP TABLE IF EXISTS "LiteLLM_ChatGPTQuotaSnapshot"',
    'DROP TABLE IF EXISTS "LiteLLM_ChatGPTResetCreditUsage"',
    'DROP TABLE IF EXISTS "LiteLLM_DeviceLoginState"',
    'DROP TABLE IF EXISTS "LiteLLM_EncryptedContentRecoveryTable"',
    'DROP TABLE IF EXISTS "LiteLLM_ManagedProxyTable"',
)


def _normalized_statements(sql: str) -> tuple[str, ...]:
    return tuple(" ".join(statement.split()) for statement in sql.split(";") if statement.strip())


def test_historical_migrations_match_production_checksums() -> None:
    actual = {
        migration_name: hashlib.sha256((MIGRATIONS_DIR / migration_name / "migration.sql").read_bytes()).hexdigest()
        for migration_name in HISTORICAL_MIGRATION_CHECKSUMS
    }

    assert actual == HISTORICAL_MIGRATION_CHECKSUMS


def test_cleanup_migration_only_removes_staging_schema() -> None:
    cleanup_sql = (MIGRATIONS_DIR / "20260812000000_remove_staging_only_schema" / "migration.sql").read_text()

    assert _normalized_statements(cleanup_sql) == EXPECTED_CLEANUP_STATEMENTS
