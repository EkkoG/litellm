CREATE TABLE "LiteLLM_ChatGPTQuotaSnapshot" (
    "snapshot_id" TEXT NOT NULL,
    "credential_name" TEXT NOT NULL,
    "snapshot_date" TEXT NOT NULL,
    "timezone" TEXT NOT NULL,
    "captured_at" TIMESTAMP(3) NOT NULL,
    "tier_name" TEXT NOT NULL,
    "remaining_percent" DOUBLE PRECISION NOT NULL,
    "resets_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "LiteLLM_ChatGPTQuotaSnapshot_pkey" PRIMARY KEY ("snapshot_id")
);

CREATE UNIQUE INDEX "LiteLLM_ChatGPTQuotaSnapshot_credential_name_snapshot_date_tier_name_key"
ON "LiteLLM_ChatGPTQuotaSnapshot"("credential_name", "snapshot_date", "tier_name");

CREATE INDEX "LiteLLM_ChatGPTQuotaSnapshot_credential_name_snapshot_date_idx"
ON "LiteLLM_ChatGPTQuotaSnapshot"("credential_name", "snapshot_date");

CREATE INDEX "LiteLLM_ChatGPTQuotaSnapshot_snapshot_date_idx"
ON "LiteLLM_ChatGPTQuotaSnapshot"("snapshot_date");
