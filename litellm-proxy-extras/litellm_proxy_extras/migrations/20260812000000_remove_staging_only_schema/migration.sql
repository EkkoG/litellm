ALTER TABLE "LiteLLM_CredentialsTable"
DROP CONSTRAINT IF EXISTS "LiteLLM_CredentialsTable_proxy_id_fkey";

DROP INDEX IF EXISTS "LiteLLM_CredentialsTable_proxy_id_key";

ALTER TABLE "LiteLLM_CredentialsTable"
DROP COLUMN IF EXISTS "proxy_id";

DROP TABLE IF EXISTS "LiteLLM_ChatGPTQuotaSnapshot";
DROP TABLE IF EXISTS "LiteLLM_ChatGPTResetCreditUsage";
DROP TABLE IF EXISTS "LiteLLM_DeviceLoginState";
DROP TABLE IF EXISTS "LiteLLM_EncryptedContentRecoveryTable";
DROP TABLE IF EXISTS "LiteLLM_ManagedProxyTable";
