import { TextInput } from "@tremor/react";
import { Select as AntdSelect, Button, Form, Modal, Tooltip, Typography } from "antd";
import type { UploadProps } from "antd/es/upload";
import React, { useState } from "react";
import ProviderSpecificFields from "../add_model/provider_specific_fields";
import { Providers, providerLogoMap } from "../provider_info_helpers";
import { resolveLogoSrc } from "@/lib/assetPaths";
import { normalizeCredentialProvider, resetCredentialFormOnProviderChange } from "./credential_form_helpers";
import ChatGPTCredentialDeviceLogin from "./ChatGPTCredentialDeviceLogin";
import GitHubCopilotCredentialDeviceLogin from "./GitHubCopilotCredentialDeviceLogin";
const { Link } = Typography;

interface AddCredentialsModalProps {
  open: boolean;
  onCancel: () => void;
  onAddCredential: (values: any) => void;
  uploadProps: UploadProps;
  onChatGPTCredentialCreated?: () => void;
  initialProvider?: Providers;
}

const AddCredentialsModal: React.FC<AddCredentialsModalProps> = ({
  open,
  onCancel,
  onAddCredential,
  uploadProps,
  onChatGPTCredentialCreated,
  initialProvider = Providers.OpenAI,
}) => {
  const [form] = Form.useForm();
  const normalizedInitialProvider = normalizeCredentialProvider(initialProvider) ?? Providers.OpenAI;
  const [selectedProvider, setSelectedProvider] = useState<Providers>(normalizedInitialProvider);
  const credentialName = Form.useWatch("credential_name", form);
  const isGitHubCopilot = selectedProvider === Providers.GITHUB_COPILOT;

  const renderCredentialFields = () => {
    if (selectedProvider === Providers.ChatGPT) {
      return (
        <ChatGPTCredentialDeviceLogin
          credentialName={credentialName}
          overwriteExisting={false}
          onComplete={onChatGPTCredentialCreated}
        />
      );
    }
    if (isGitHubCopilot) {
      return (
        <GitHubCopilotCredentialDeviceLogin
          credentialName={credentialName}
          overwriteExisting={false}
          onComplete={onChatGPTCredentialCreated}
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
    onAddCredential(filteredValues);
    form.resetFields();
  };

  return (
    <Modal
      title="Add New Credential"
      open={open}
      onCancel={() => {
        onCancel();
        form.resetFields();
      }}
      footer={null}
      width={600}
      destroyOnHidden
    >
      <Form
        form={form}
        onFinish={handleSubmit}
        layout="vertical"
        initialValues={{ custom_llm_provider: normalizedInitialProvider }}
      >
        {/* Credential Name */}
        <Form.Item
          label="Credential Name:"
          name="credential_name"
          rules={[{ required: true, message: "Credential name is required" }]}
        >
          <TextInput placeholder="Enter a friendly name for these credentials" />
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
            {selectedProvider !== Providers.ChatGPT && !isGitHubCopilot && (
              <Button htmlType="submit">{"Add Credential"}</Button>
            )}
          </div>
        </div>
      </Form>
    </Modal>
  );
};

export default AddCredentialsModal;
