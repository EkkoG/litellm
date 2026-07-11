import { TextInput } from "@tremor/react";
import { Select as AntdSelect, Button, Form, Modal, Tooltip, Typography } from "antd";
import type { UploadProps } from "antd/es/upload";
import { useEffect, useState } from "react";
import ProviderSpecificFields from "../add_model/provider_specific_fields";
import { CredentialItem } from "../networking";
import { Providers, providerLogoMap } from "../provider_info_helpers";
import { resolveLogoSrc } from "@/lib/assetPaths";
import { resetCredentialFormOnProviderChange } from "./credential_form_helpers";
import ChatGPTCredentialDeviceLogin from "./ChatGPTCredentialDeviceLogin";
import GitHubCopilotCredentialDeviceLogin from "./GitHubCopilotCredentialDeviceLogin";
const { Link } = Typography;

interface EditCredentialsModalProps {
  open: boolean;
  onCancel: () => void;
  onUpdateCredential: (values: any) => void;
  uploadProps: UploadProps;
  existingCredential: CredentialItem | null;
  onChatGPTCredentialUpdated?: () => void;
}

export default function EditCredentialsModal({
  open,
  onCancel,
  onUpdateCredential,
  uploadProps,
  existingCredential,
  onChatGPTCredentialUpdated,
}: EditCredentialsModalProps) {
  const [form] = Form.useForm();
  const [selectedProvider, setSelectedProvider] = useState<Providers>(Providers.Anthropic);
  const credentialName = Form.useWatch("credential_name", form);
  const isChatGPTCredential =
    selectedProvider === Providers.ChatGPT || existingCredential?.credential_info?.custom_llm_provider === "chatgpt";
  const isGitHubCopilotCredential =
    selectedProvider === Providers.GITHUB_COPILOT ||
    existingCredential?.credential_info?.custom_llm_provider === "github_copilot";

  const renderCredentialFields = () => {
    if (isChatGPTCredential) {
      return (
        <ChatGPTCredentialDeviceLogin
          credentialName={credentialName || existingCredential?.credential_name}
          overwriteExisting={true}
          onComplete={onChatGPTCredentialUpdated}
        />
      );
    }
    if (isGitHubCopilotCredential) {
      return (
        <GitHubCopilotCredentialDeviceLogin
          credentialName={credentialName || existingCredential?.credential_name}
          overwriteExisting={true}
          onComplete={onChatGPTCredentialUpdated}
        />
      );
    }
    return <ProviderSpecificFields selectedProvider={selectedProvider} uploadProps={uploadProps} />;
  };

  const handleSubmit = (values: any) => {
    const filteredValues = Object.entries(values).reduce((acc, [key, value]) => {
      if (value !== "" && value !== undefined && value !== null) {
        acc[key] = value;
      }
      return acc;
    }, {} as any);
    onUpdateCredential(filteredValues);
    form.resetFields();
  };

  useEffect(() => {
    if (existingCredential) {
      const storedProvider = existingCredential.credential_info.custom_llm_provider;
      const managedProviderMap: Record<string, Providers> = {
        chatgpt: Providers.ChatGPT,
        github_copilot: Providers.GITHUB_COPILOT,
      };
      const provider = managedProviderMap[storedProvider] ?? (storedProvider as Providers);
      if (provider === Providers.ChatGPT || provider === Providers.GITHUB_COPILOT) {
        form.setFieldsValue({
          credential_name: existingCredential.credential_name,
          custom_llm_provider: provider,
        });
        setSelectedProvider(provider);
        return;
      }
      // Spread all credential_values dynamically, converting undefined/null to null for form compatibility
      const credentialValues = Object.entries(existingCredential.credential_values || {}).reduce(
        (acc, [key, value]) => {
          acc[key] = value ?? null;
          return acc;
        },
        {} as Record<string, any>,
      );

      form.setFieldsValue({
        credential_name: existingCredential.credential_name,
        custom_llm_provider: provider,
        ...credentialValues,
      });
      setSelectedProvider(provider);
    }
  }, [existingCredential]);

  return (
    <Modal
      title="Edit Credential"
      open={open}
      onCancel={() => {
        onCancel();
        form.resetFields();
      }}
      footer={null}
      width={600}
      destroyOnHidden={true}
    >
      <Form form={form} onFinish={handleSubmit} layout="vertical">
        {/* Credential Name */}
        <Form.Item
          label="Credential Name:"
          name="credential_name"
          rules={[{ required: true, message: "Credential name is required" }]}
          initialValue={existingCredential?.credential_name}
        >
          <TextInput
            placeholder="Enter a friendly name for these credentials"
            disabled={existingCredential?.credential_name ? true : false}
          />
        </Form.Item>

        {/* Provider Selection */}
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

        {renderCredentialFields()}

        {/* Modal Footer */}
        <div className="flex justify-between items-center">
          <Tooltip title="Get help on our github">
            <Link href="https://github.com/BerriAI/litellm/issues">Need Help?</Link>
          </Tooltip>

          <div>
            <Button
              onClick={() => {
                onCancel();
                form.resetFields();
              }}
              style={{ marginRight: 10 }}
            >
              Cancel
            </Button>
            {!isChatGPTCredential && !isGitHubCopilotCredential && (
              <Button htmlType="submit">{"Update Credential"}</Button>
            )}
          </div>
        </div>
      </Form>
    </Modal>
  );
}
