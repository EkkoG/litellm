-- CreateTable
CREATE TABLE "LiteLLM_DeviceLoginState" (
    "login_id" TEXT NOT NULL,
    "encrypted_state" TEXT NOT NULL,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "LiteLLM_DeviceLoginState_pkey" PRIMARY KEY ("login_id")
);

-- CreateIndex
CREATE INDEX "LiteLLM_DeviceLoginState_expires_at_idx" ON "LiteLLM_DeviceLoginState"("expires_at");
