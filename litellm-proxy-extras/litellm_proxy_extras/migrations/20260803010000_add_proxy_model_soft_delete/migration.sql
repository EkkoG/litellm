ALTER TABLE "LiteLLM_ProxyModelTable"
ADD COLUMN "deleted_at" TIMESTAMP(3),
ADD COLUMN "deleted_by" TEXT;

CREATE INDEX "LiteLLM_ProxyModelTable_deleted_at_idx"
ON "LiteLLM_ProxyModelTable"("deleted_at");
