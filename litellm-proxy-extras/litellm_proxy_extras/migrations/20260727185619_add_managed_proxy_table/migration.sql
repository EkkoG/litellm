-- AlterTable
ALTER TABLE "LiteLLM_CredentialsTable" ADD COLUMN     "proxy_id" TEXT;

-- CreateTable
CREATE TABLE "LiteLLM_ManagedProxyTable" (
    "proxy_id" TEXT NOT NULL,
    "proxy_name" TEXT NOT NULL,
    "protocol" TEXT NOT NULL,
    "host" TEXT NOT NULL,
    "port" INTEGER NOT NULL,
    "encrypted_username" TEXT,
    "encrypted_password" TEXT,
    "dedupe_fingerprint" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'active',
    "last_tested_at" TIMESTAMP(3),
    "last_test_success" BOOLEAN,
    "last_test_latency_ms" INTEGER,
    "last_test_error" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "created_by" TEXT NOT NULL,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_by" TEXT NOT NULL,

    CONSTRAINT "LiteLLM_ManagedProxyTable_pkey" PRIMARY KEY ("proxy_id")
);

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_ManagedProxyTable_proxy_name_key" ON "LiteLLM_ManagedProxyTable"("proxy_name");

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_ManagedProxyTable_dedupe_fingerprint_key" ON "LiteLLM_ManagedProxyTable"("dedupe_fingerprint");

-- CreateIndex
CREATE INDEX "LiteLLM_ManagedProxyTable_status_idx" ON "LiteLLM_ManagedProxyTable"("status");

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_CredentialsTable_proxy_id_key" ON "LiteLLM_CredentialsTable"("proxy_id");

-- AddForeignKey
ALTER TABLE "LiteLLM_CredentialsTable" ADD CONSTRAINT "LiteLLM_CredentialsTable_proxy_id_fkey" FOREIGN KEY ("proxy_id") REFERENCES "LiteLLM_ManagedProxyTable"("proxy_id") ON DELETE RESTRICT ON UPDATE CASCADE;
