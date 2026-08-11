CREATE TABLE "LiteLLM_ChatGPTResetCreditUsage" (
    "usage_id" TEXT NOT NULL,
    "credential_name" TEXT NOT NULL,
    "credit_id" TEXT,
    "credit_label" TEXT,
    "outcome" TEXT NOT NULL,
    "windows_reset" INTEGER NOT NULL DEFAULT 0,
    "used_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "used_by" TEXT,
    "idempotency_key" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "LiteLLM_ChatGPTResetCreditUsage_pkey" PRIMARY KEY ("usage_id")
);

CREATE UNIQUE INDEX "LiteLLM_ChatGPTResetCreditUsage_idempotency_key_key"
ON "LiteLLM_ChatGPTResetCreditUsage"("idempotency_key");

CREATE INDEX "LiteLLM_ChatGPTResetCreditUsage_credential_name_used_at_idx"
ON "LiteLLM_ChatGPTResetCreditUsage"("credential_name", "used_at");
