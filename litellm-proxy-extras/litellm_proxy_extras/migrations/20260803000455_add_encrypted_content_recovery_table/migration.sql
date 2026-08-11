-- CreateTable
CREATE TABLE "LiteLLM_EncryptedContentRecoveryTable" (
    "deployment_id" TEXT NOT NULL,
    "credential_id" TEXT NOT NULL,
    "credential_name" TEXT NOT NULL,
    "custom_llm_provider" TEXT NOT NULL,
    "source" TEXT NOT NULL DEFAULT 'spend_logs',
    "recovered_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_EncryptedContentRecoveryTable_pkey" PRIMARY KEY ("deployment_id")
);

-- CreateIndex
CREATE INDEX "LiteLLM_EncryptedContentRecoveryTable_credential_id_idx" ON "LiteLLM_EncryptedContentRecoveryTable"("credential_id");
