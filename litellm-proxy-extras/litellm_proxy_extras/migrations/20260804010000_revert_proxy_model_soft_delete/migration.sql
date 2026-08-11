DROP INDEX IF EXISTS "LiteLLM_ProxyModelTable_deleted_at_idx";

ALTER TABLE "LiteLLM_ProxyModelTable"
DROP COLUMN IF EXISTS "deleted_at",
DROP COLUMN IF EXISTS "deleted_by";
