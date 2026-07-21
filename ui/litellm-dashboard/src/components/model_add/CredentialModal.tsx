import { TextInput } from "@tremor/react";
import { Select as AntdSelect, Button, Form, Modal, Radio, Tooltip, Typography } from "antd";
import type { UploadProps } from "antd/es/upload";
import { useState } from "react";
import ProviderSpecificFields from "../add_model/provider_specific_fields";
import { CredentialItem } from "../networking";
import { provider_map, Providers, providerLogoMap } from "../provider_info_helpers";
import { resolveLogoSrc } from "@/lib/assetPaths";
import { resetCredentialFormOnProviderChange } from "./credential_form_helpers";
import ChatGPTCredentialDeviceLogin from "./ChatGPTCredentialDeviceLogin";
import GitHubCopilotCredentialDeviceLogin from "./GitHubCopilotCredentialDeviceLogin";
import XAIOAuthCredentialImport from "./XAIOAuthCredentialImport";

const { Link } = Typography;

type CredentialFormValues = Record<string, unknown> & {
  credential_name?: string;
  custom_llm_provider?: string;
};

type XAIAuthenticationMethod = "api_key" | "oauth_json";

interface CredentialModalProps {
  open: boolean;
  onCancel: () => void;
  onSubmit: (values: CredentialFormValues) => void | Promise<void>;
  uploadProps: UploadProps;
  mode: "add" | "edit";
  existingCredential?: CredentialItem | null;
  onCredentialComplete?: () => void | Promise<void>;
}

const getCredentialAuthType = (credential?: CredentialItem | null): string =>
  typeof credential?.credential_info.auth_type === "string" ? credential.credential_info.auth_type.toLowerCase() : "";

export default function CredentialModal({
  open,
  onCancel,
  onSubmit,
  uploadProps,
  mode,
  existingCredential = null,
  onCredentialComplete,
}: CredentialModalProps) {
  const isEdit = mode === "edit";
  const [form] = Form.useForm<CredentialFormValues>();
  const initialProvider = (existingCredential?.credential_info.custom_llm_provider as Providers) ?? Providers.OpenAI;
  const [selectedProvider, setSelectedProvider] = useState<Providers>(initialProvider);
  const [xaiAuthenticationMethod, setXaiAuthenticationMethod] = useState<XAIAuthenticationMethod>(
    getCredentialAuthType(existingCredential) === "oauth_json_import" ? "oauth_json" : "api_key",
  );
  const credentialName = Form.useWatch("credential_name", form);
  const selectedProviderId = provider_map[selectedProvider as keyof typeof provider_map] ?? selectedProvider;
  const isChatGPT = selectedProviderId === provider_map.ChatGPT;
  const isGitHubCopilot = selectedProviderId === provider_map.GITHUB_COPILOT;
  const isXAI = selectedProviderId === provider_map.xAI;
  const isXAIOAuth = isXAI && xaiAuthenticationMethod === "oauth_json";
  const isManagedCredential = isChatGPT || isGitHubCopilot || isXAIOAuth;

  const initialValues = existingCredential
    ? {
        credential_name: existingCredential.credential_name,
        custom_llm_provider: existingCredential.credential_info.custom_llm_provider,
        ...Object.fromEntries(
          Object.entries(existingCredential.credential_values || {}).map(([key, value]) => [key, value ?? null]),
        ),
      }
    : undefined;

  const resetModal = () => {
    form.resetFields();
    setSelectedProvider(initialProvider);
    setXaiAuthenticationMethod(
      getCredentialAuthType(existingCredential) === "oauth_json_import" ? "oauth_json" : "api_key",
    );
  };

  const handleSubmit = async (values: CredentialFormValues) => {
    const filteredValues = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value !== "" && value !== undefined && value !== null),
    );
    try {
      await onSubmit(filteredValues);
      resetModal();
    } catch {
      resetModal();
    }
  };

  const closeAndReset = () => {
    resetModal();
    onCancel();
  };

  const handleCredentialComplete = async () => {
    resetModal();
    await onCredentialComplete?.();
  };

  const renderCredentialFields = () => {
    if (isChatGPT) {
      return (
        <ChatGPTCredentialDeviceLogin
          credentialName={credentialName || existingCredential?.credential_name}
          overwriteExisting={isEdit}
          onComplete={handleCredentialComplete}
        />
      );
    }
    if (isGitHubCopilot) {
      return (
        <GitHubCopilotCredentialDeviceLogin
          credentialName={credentialName || existingCredential?.credential_name}
          overwriteExisting={isEdit}
          onComplete={handleCredentialComplete}
        />
      );
    }
    if (isXAIOAuth) {
      return (
        <XAIOAuthCredentialImport
          key={`${open}-${mode}-${existingCredential?.credential_name ?? "new"}`}
          credentialName={credentialName || existingCredential?.credential_name}
          overwriteExisting={isEdit}
          onComplete={handleCredentialComplete}
          onReset={resetModal}
        />
      );
    }
    return <ProviderSpecificFields selectedProvider={selectedProvider} uploadProps={uploadProps} />;
  };

  return (
    <Modal
      title={isEdit ? "Edit Credential" : "Add New Credential"}
      open={open}
      onCancel={closeAndReset}
      footer={null}
      width={600}
      destroyOnHidden={isEdit}
    >
      <Form
        form={form}
        onFinish={handleSubmit}
        layout="vertical"
        initialValues={initialValues ?? { custom_llm_provider: initialProvider }}
      >
        <Form.Item
          label="Credential Name:"
          name="credential_name"
          rules={[{ required: true, message: "Credential name is required" }]}
        >
          <TextInput placeholder="Enter a friendly name for these credentials" disabled={isEdit} />
        </Form.Item>

        <Form.Item
          rules={[{ required: true, message: "Required" }]}
          label="Provider:"
          name="custom_llm_provider"
          tooltip="Helper to auto-populate provider specific fields"
        >
          <AntdSelect
            showSearch
            onChange={(value) => {
              resetCredentialFormOnProviderChange(form, value as Providers, setSelectedProvider);
              setXaiAuthenticationMethod("api_key");
            }}
          >
            {Object.entries(Providers).map(([providerEnum, providerDisplayName]) => (
              <AntdSelect.Option key={providerEnum} value={providerEnum}>
                <div className="flex items-center space-x-2">
                  <img
                    src={resolveLogoSrc(providerLogoMap[providerDisplayName])}
                    alt={`${providerEnum} logo`}
                    className="w-5 h-5"
                    onError={(e) => {
                      const target = e.target as HTMLImageElement;
                      const parent = target.parentElement;
                      if (parent) {
                        const fallbackDiv = document.createElement("div");
                        fallbackDiv.className =
                          "w-5 h-5 rounded-full bg-gray-200 flex items-center justify-center text-xs";
                        fallbackDiv.textContent = providerDisplayName.charAt(0);
                        parent.replaceChild(fallbackDiv, target);
                      }
                    }}
                  />
                  <span>{providerDisplayName}</span>
                </div>
              </AntdSelect.Option>
            ))}
          </AntdSelect>
        </Form.Item>

        {isXAI && (
          <Form.Item label="Authentication method:">
            <Radio.Group
              value={xaiAuthenticationMethod}
              onChange={(event) => setXaiAuthenticationMethod(event.target.value as XAIAuthenticationMethod)}
            >
              <Radio value="api_key">API key</Radio>
              <Radio value="oauth_json">OAuth JSON</Radio>
            </Radio.Group>
          </Form.Item>
        )}

        {renderCredentialFields()}

        <div className="flex justify-between items-center mt-4">
          <Tooltip title="Get help on our github">
            <Link href="https://github.com/BerriAI/litellm/issues">Need Help?</Link>
          </Tooltip>

          <div>
            <Button onClick={closeAndReset} style={{ marginRight: 10 }}>
              Cancel
            </Button>
            {!isManagedCredential && (
              <Button htmlType="submit">{isEdit ? "Update Credential" : "Add Credential"}</Button>
            )}
          </div>
        </div>
      </Form>
    </Modal>
  );
}
