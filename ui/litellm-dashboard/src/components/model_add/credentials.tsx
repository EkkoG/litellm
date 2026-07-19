import {
  chatgptCredentialResetCreditConsumeCall,
  credentialCreateCall,
  credentialDeleteCall,
  chatgptCredentialSubscriptionStatusCall,
  credentialUpdateCall,
  deriveErrorMessage,
} from "@/components/networking"; // Assume this is your networking function
import type { ChatGPTRateLimitResetCredit, ChatGPTSubscriptionStatus, CredentialItem } from "@/components/networking";
import { PencilAltIcon, TrashIcon } from "@heroicons/react/outline";
import {
  Badge,
  Button,
  Card,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
} from "@tremor/react";
import { Form } from "antd";
import { UploadProps } from "antd/es/upload";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import DeleteResourceModal from "../common_components/DeleteResourceModal";
import NotificationsManager from "../molecules/notifications_manager";
import { ChatGPTQuotaHistoryDrawer } from "./ChatGPTQuotaHistoryDrawer";
import { chatgptTierLabel, formatChatGPTQuotaPercent } from "./chatgptQuotaDisplay";
import CredentialModal from "./CredentialModal";
import { useCredentials } from "@/app/(dashboard)/hooks/credentials/useCredentials";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { isProxyAdminRole } from "@/utils/roles";
import { stripMaskedSecrets } from "@/utils/maskedSecretUtils";
interface CredentialsPanelProps {
  uploadProps: UploadProps;
}

const CHATGPT_PROVIDER = "chatgpt";
const CHATGPT_SUBSCRIPTION_STALE_TIME_MS = 5 * 60 * 1000;
const getCredentialProvider = (credential: CredentialItem): string =>
  String(credential.credential_info?.custom_llm_provider || "").toLowerCase();

const isChatGPTCredential = (credential: CredentialItem): boolean =>
  getCredentialProvider(credential) === CHATGPT_PROVIDER;

const tierColor = (remainingPercent: number): "green" | "yellow" | "red" => {
  if (remainingPercent <= 10) return "red";
  if (remainingPercent <= 30) return "yellow";
  return "green";
};

const formatResetCountdown = (resetsAt?: string | null): string | null => {
  if (!resetsAt) {
    return null;
  }
  const diffMs = new Date(resetsAt).getTime() - Date.now();
  if (diffMs <= 0) {
    return null;
  }
  const hours = Math.floor(diffMs / (1000 * 60 * 60));
  const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
  if (hours >= 24) {
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
};

const statusLabel = (status: ChatGPTSubscriptionStatus): string => {
  if (status.success) {
    return status.plan_label || "Unknown plan";
  }
  if (status.credential_status === "expired") {
    return "Sign in required";
  }
  return "Query failed";
};

const resetCreditOutcomeMessage = (outcome?: string | null): string => {
  if (outcome === "reset") return "ChatGPT reset credit used";
  if (outcome === "nothing_to_reset") return "No active rate limit window needed reset";
  if (outcome === "no_credit") return "No ChatGPT reset credits available";
  if (outcome === "already_redeemed") return "ChatGPT reset credit already redeemed";
  return "ChatGPT reset credit request completed";
};

const createIdempotencyKey = (): string => {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `chatgpt-reset-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const resetCreditLabel = (credit: ChatGPTRateLimitResetCredit): string => credit.title || "Reset credit";

const resetCreditDetail = (credit: ChatGPTRateLimitResetCredit): string | null => {
  const expiresIn = formatResetCountdown(credit.expires_at);
  return expiresIn ? `Expires in ${expiresIn}` : null;
};

const ChatGPTResetCreditsList: React.FC<{
  credential: CredentialItem;
  accessToken: string;
  status: ChatGPTSubscriptionStatus;
  isFetching: boolean;
  onRefresh: () => Promise<unknown>;
}> = ({ credential, accessToken, status, isFetching, onRefresh }) => {
  const resetCredits = status.rate_limit_reset_credits;
  const availableResetCredits = resetCredits?.available_count ?? 0;
  const resetMutation = useMutation({
    mutationFn: async (creditId?: string) => {
      return chatgptCredentialResetCreditConsumeCall(
        accessToken,
        credential.credential_name,
        createIdempotencyKey(),
        creditId,
      );
    },
    onSuccess: async (response) => {
      if (response.success && response.outcome === "reset") {
        NotificationsManager.success(resetCreditOutcomeMessage(response.outcome));
      } else if (response.success) {
        NotificationsManager.info(resetCreditOutcomeMessage(response.outcome));
      } else {
        NotificationsManager.error(response.error || "ChatGPT reset credit request failed");
      }
      await onRefresh();
    },
    onError: (error) => {
      NotificationsManager.error(deriveErrorMessage(error));
    },
  });

  if (availableResetCredits <= 0) {
    return null;
  }

  const credits = resetCredits?.credits?.filter((credit) => credit.status === "available") || [];
  if (credits.length > 0) {
    return (
      <div className="flex basis-full flex-col gap-1">
        {credits.map((credit) => {
          const detail = resetCreditDetail(credit);
          return (
            <div key={credit.id} className="flex flex-wrap items-center gap-2 rounded border border-gray-200 px-2 py-1">
              <Badge color="emerald" size="xs">
                {resetCreditLabel(credit)}
              </Badge>
              {detail && <Text className="text-xs text-gray-500">{detail}</Text>}
              <Button
                size="xs"
                variant="secondary"
                disabled={resetMutation.isPending || isFetching}
                onClick={() => resetMutation.mutate(credit.id)}
              >
                {resetMutation.isPending ? "Using" : "Use"}
              </Button>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <>
      <Badge color="emerald" size="xs">
        {availableResetCredits} reset {availableResetCredits === 1 ? "credit" : "credits"}
      </Badge>
      <Button
        size="xs"
        variant="secondary"
        disabled={resetMutation.isPending || isFetching}
        onClick={() => resetMutation.mutate(undefined)}
      >
        {resetMutation.isPending ? "Using" : "Use credit"}
      </Button>
    </>
  );
};

const ChatGPTSubscriptionStatusCell: React.FC<{
  credential: CredentialItem;
  accessToken?: string;
}> = ({ credential, accessToken }) => {
  const [historyOpen, setHistoryOpen] = useState(false);
  const enabled = Boolean(accessToken) && isChatGPTCredential(credential);
  const subscriptionQuery = {
    queryKey: ["chatgpt-credential-subscription", credential.credential_name],
    queryFn: async () => chatgptCredentialSubscriptionStatusCall(accessToken!, credential.credential_name),
    enabled,
    staleTime: CHATGPT_SUBSCRIPTION_STALE_TIME_MS,
    retry: 1,
  };
  const {
    data: status,
    error,
    isFetching,
    isLoading,
    refetch,
  } = useQuery<ChatGPTSubscriptionStatus>(subscriptionQuery);

  if (!isChatGPTCredential(credential)) {
    return <Text>-</Text>;
  }

  if (isLoading) {
    return (
      <Badge color="gray" size="xs">
        Loading
      </Badge>
    );
  }

  const errorMessage = error ? deriveErrorMessage(error) : status?.error;
  if (errorMessage || (status && !status.success)) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Badge color={status?.credential_status === "expired" ? "yellow" : "red"} size="xs">
          {status ? statusLabel(status) : "Unavailable"}
        </Badge>
        <Button size="xs" variant="light" disabled={isFetching} onClick={() => void refetch()}>
          {isFetching ? "Refreshing" : "Refresh"}
        </Button>
      </div>
    );
  }

  if (!status) {
    return (
      <Button size="xs" variant="light" disabled={isFetching} onClick={() => void refetch()}>
        {isFetching ? "Checking" : "Check status"}
      </Button>
    );
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge color="blue" size="xs">
          {statusLabel(status)}
        </Badge>
        {status.tiers.map((tier) => {
          const countdown = formatResetCountdown(tier.resets_at);
          return (
            <Badge key={tier.name} color={tierColor(tier.remaining_percent)} size="xs">
              {chatgptTierLabel(tier.name)} {formatChatGPTQuotaPercent(tier.remaining_percent)}% remaining
              {countdown ? ` · ${countdown}` : ""}
            </Badge>
          );
        })}
        {accessToken && (
          <div className="flex basis-full flex-wrap items-center gap-2">
            {status.daily_snapshot && (
              <Text className="text-xs text-gray-500">
                Today start:{" "}
                {status.daily_snapshot.tiers
                  .map((tier) => `${chatgptTierLabel(tier.name)} ${formatChatGPTQuotaPercent(tier.remaining_percent)}%`)
                  .join(" · ")}
              </Text>
            )}
            <Button size="xs" variant="light" onClick={() => setHistoryOpen(true)}>
              History
            </Button>
          </div>
        )}
        {accessToken && (
          <ChatGPTResetCreditsList
            credential={credential}
            accessToken={accessToken}
            status={status}
            isFetching={isFetching}
            onRefresh={refetch}
          />
        )}
        <Button size="xs" variant="light" disabled={isFetching} onClick={() => void refetch()}>
          {isFetching ? "Refreshing" : "Refresh"}
        </Button>
      </div>
      {accessToken && (
        <ChatGPTQuotaHistoryDrawer
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          accessToken={accessToken}
          credentialName={credential.credential_name}
          planLabel={statusLabel(status)}
          currentTiers={status.tiers}
          timezone={status.daily_snapshot?.timezone}
        />
      )}
    </>
  );
};

const CredentialsPanel: React.FC<CredentialsPanelProps> = ({ uploadProps }) => {
  const { accessToken, userRole } = useAuthorized();
  // Admin Viewer follows the read-parity rule: see credentials, do not modify.
  const canModifyCredentials = isProxyAdminRole(userRole ?? "");
  const { data: credentialsResponse, refetch: refetchCredentials } = useCredentials();
  const credentialList = credentialsResponse?.credentials || [];

  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [isUpdateModalOpen, setIsUpdateModalOpen] = useState(false);
  const [selectedCredential, setSelectedCredential] = useState<CredentialItem | null>(null);
  const [credentialToDelete, setCredentialToDelete] = useState<CredentialItem | null>(null);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isCredentialDeleting, setIsCredentialDeleting] = useState(false);
  const [form] = Form.useForm();

  const restrictedFields = ["credential_name", "custom_llm_provider"];
  const handleUpdateCredential = async (values: any) => {
    if (!accessToken) {
      return;
    }

    const filter_credential_values = stripMaskedSecrets(
      Object.entries(values)
        .filter(([key]) => !restrictedFields.includes(key))
        .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {}),
    );
    // Transform form values into credential structure
    const newCredential = {
      credential_name: values.credential_name,
      credential_values: filter_credential_values,
      credential_info: {
        custom_llm_provider: values.custom_llm_provider,
      },
    };

    await credentialUpdateCall(accessToken, values.credential_name, newCredential);
    NotificationsManager.success("Credential updated successfully");
    setIsUpdateModalOpen(false);
    await refetchCredentials();
  };

  const handleAddCredential = async (values: any) => {
    if (!accessToken) {
      return;
    }

    const filter_credential_values = Object.entries(values)
      .filter(([key]) => !restrictedFields.includes(key))
      .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {});
    // Transform form values into credential structure
    const newCredential = {
      credential_name: values.credential_name,
      credential_values: filter_credential_values,
      credential_info: {
        custom_llm_provider: values.custom_llm_provider,
      },
    };

    // Add to list and close modal
    await credentialCreateCall(accessToken, newCredential);
    NotificationsManager.success("Credential added successfully");
    setIsAddModalOpen(false);
    await refetchCredentials();
  };

  const renderProviderBadge = (provider: string) => {
    const providerColors: Record<string, string> = {
      openai: "blue",
      azure: "indigo",
      anthropic: "purple",
      default: "gray",
    };

    const color = providerColors[provider.toLowerCase()] || providerColors["default"];
    return (
      <Badge color={color as any} size="xs">
        {provider}
      </Badge>
    );
  };

  const handleDeleteCredential = async () => {
    if (!accessToken || !credentialToDelete) {
      return;
    }
    setIsCredentialDeleting(true);
    try {
      await credentialDeleteCall(accessToken, credentialToDelete.credential_name);
      NotificationsManager.success("Credential deleted successfully");
      await refetchCredentials();
    } catch (error) {
      NotificationsManager.error("Failed to delete credential");
    } finally {
      setCredentialToDelete(null);
      setIsDeleteModalOpen(false);
      setIsCredentialDeleting(false);
    }
  };

  const openDeleteModal = (credential: CredentialItem) => {
    setCredentialToDelete(credential);
    setIsDeleteModalOpen(true);
  };

  const closeDeleteModal = () => {
    setCredentialToDelete(null);
    setIsDeleteModalOpen(false);
  };

  return (
    <div className="w-full mx-auto flex-auto overflow-y-auto p-2">
      {canModifyCredentials && <Button onClick={() => setIsAddModalOpen(true)}>Add Credential</Button>}
      <div className="flex justify-between items-center mt-4 mb-4">
        <Text>Configured credentials for different AI providers. Add and manage your API credentials.</Text>
      </div>

      <Card>
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Credential Name</TableHeaderCell>
              <TableHeaderCell>Provider</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Actions</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {!credentialList || credentialList.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="text-center py-4 text-gray-500">
                  No credentials configured
                </TableCell>
              </TableRow>
            ) : (
              credentialList.map((credential: CredentialItem, index: number) => (
                <TableRow key={index}>
                  <TableCell>{credential.credential_name}</TableCell>
                  <TableCell>
                    {renderProviderBadge((credential.credential_info?.custom_llm_provider as string) || "-")}
                  </TableCell>
                  <TableCell>
                    <ChatGPTSubscriptionStatusCell credential={credential} accessToken={accessToken || undefined} />
                  </TableCell>
                  <TableCell>
                    {canModifyCredentials ? (
                      <>
                        <Button
                          icon={PencilAltIcon}
                          variant="light"
                          size="sm"
                          onClick={() => {
                            setSelectedCredential(credential);
                            setIsUpdateModalOpen(true);
                          }}
                        />
                        <Button
                          icon={TrashIcon}
                          variant="light"
                          size="sm"
                          onClick={() => openDeleteModal(credential)}
                          className="ml-2"
                        />
                      </>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      {isAddModalOpen && (
        <CredentialModal
          mode="add"
          onSubmit={handleAddCredential}
          onCredentialComplete={async () => {
            NotificationsManager.success("Credential added successfully");
            setIsAddModalOpen(false);
            await refetchCredentials();
          }}
          open={isAddModalOpen}
          onCancel={() => setIsAddModalOpen(false)}
          uploadProps={uploadProps}
        />
      )}
      {isUpdateModalOpen && (
        <CredentialModal
          mode="edit"
          open={isUpdateModalOpen}
          existingCredential={selectedCredential}
          onSubmit={handleUpdateCredential}
          onCredentialComplete={async () => {
            NotificationsManager.success("Credential updated successfully");
            setIsUpdateModalOpen(false);
            await refetchCredentials();
          }}
          uploadProps={uploadProps}
          onCancel={() => setIsUpdateModalOpen(false)}
        />
      )}

      <DeleteResourceModal
        isOpen={isDeleteModalOpen}
        onCancel={closeDeleteModal}
        onOk={handleDeleteCredential}
        title="Delete Credential?"
        message="Are you sure you want to delete this credential? This action cannot be undone and may break existing integrations."
        resourceInformationTitle="Credential Information"
        resourceInformation={[
          { label: "Credential Name", value: credentialToDelete?.credential_name },
          { label: "Provider", value: credentialToDelete?.credential_info?.custom_llm_provider || "-" },
        ]}
        confirmLoading={isCredentialDeleting}
        requiredConfirmation={credentialToDelete?.credential_name}
      />
    </div>
  );
};

export default CredentialsPanel;
