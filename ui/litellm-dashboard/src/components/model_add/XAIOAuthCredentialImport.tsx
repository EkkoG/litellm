import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Alert, Button, Input, Space, Upload, UploadProps, Typography } from "antd";
import { useState } from "react";
import { deriveErrorMessage, xaiOAuthCredentialImportCall } from "../networking";

const MAX_OAUTH_JSON_BYTES = 64 * 1024;
const MAX_OAUTH_JSON_MESSAGE = "OAuth JSON must be 64 KiB or smaller";

interface XAIOAuthCredentialImportProps {
  credentialName?: string;
  overwriteExisting: boolean;
  onComplete?: () => void | Promise<void>;
  onReset?: () => void;
}

const oauthJSONByteLength = (value: string): number => new TextEncoder().encode(value).byteLength;

const readFile = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(new Error("Unable to read the OAuth JSON file"));
    reader.readAsText(file);
  });

export default function XAIOAuthCredentialImport({
  credentialName,
  overwriteExisting,
  onComplete,
  onReset,
}: XAIOAuthCredentialImportProps) {
  const { accessToken } = useAuthorized();
  const [oauthJSON, setOAuthJSON] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const clearSensitiveState = () => {
    setOAuthJSON("");
    setFileName(null);
    setIsVisible(false);
  };

  const setValidatedOAuthJSON = (value: string, uploadedFileName: string | null) => {
    if (oauthJSONByteLength(value) > MAX_OAUTH_JSON_BYTES) {
      clearSensitiveState();
      setError(MAX_OAUTH_JSON_MESSAGE);
      return;
    }
    setOAuthJSON(value);
    setFileName(uploadedFileName);
    setError(null);
  };

  const handleFile = async (file: File) => {
    if (file.size > MAX_OAUTH_JSON_BYTES) {
      clearSensitiveState();
      setError(MAX_OAUTH_JSON_MESSAGE);
      return;
    }
    try {
      setValidatedOAuthJSON(await readFile(file), file.name);
    } catch (caughtError) {
      clearSensitiveState();
      setError(caughtError instanceof Error ? caughtError.message : "Unable to read the OAuth JSON file");
    }
  };

  const uploadProps: UploadProps = {
    accept: ".json,application/json",
    beforeUpload: (file) => {
      void handleFile(file);
      return false;
    },
    maxCount: 1,
    showUploadList: false,
  };

  const importCredential = async () => {
    if (!accessToken || !credentialName || !oauthJSON.trim()) {
      return;
    }
    setIsImporting(true);
    setError(null);
    try {
      await xaiOAuthCredentialImportCall(accessToken, {
        credential_name: credentialName,
        auth_json: oauthJSON,
        overwrite_existing: overwriteExisting,
      });
      clearSensitiveState();
      await onComplete?.();
    } catch (caughtError) {
      clearSensitiveState();
      setError(caughtError instanceof Error ? caughtError.message : deriveErrorMessage(caughtError));
      onReset?.();
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <Space direction="vertical" className="w-full" size="middle">
      <div>
        <Typography.Text strong>OAuth JSON</Typography.Text>
        <Typography.Paragraph id="xai-oauth-json-help" type="secondary" className="mb-2">
          Upload or paste the JSON created by xAI OAuth login. The value stays hidden unless you choose to show it.
        </Typography.Paragraph>
        {isVisible ? (
          <Input.TextArea
            aria-label="OAuth JSON"
            aria-describedby="xai-oauth-json-help"
            autoComplete="off"
            rows={6}
            spellCheck={false}
            value={oauthJSON}
            onChange={(event) => setValidatedOAuthJSON(event.target.value, null)}
            placeholder="Paste xAI OAuth JSON"
          />
        ) : (
          <Input.Password
            aria-label="OAuth JSON"
            aria-describedby="xai-oauth-json-help"
            autoComplete="off"
            spellCheck={false}
            value={oauthJSON}
            visibilityToggle={false}
            onChange={(event) => setValidatedOAuthJSON(event.target.value, null)}
            placeholder="Paste xAI OAuth JSON"
          />
        )}
      </div>
      <Space wrap>
        <Upload {...uploadProps}>
          <Button>Upload JSON file</Button>
        </Upload>
        <Button aria-pressed={isVisible} onClick={() => setIsVisible((visible) => !visible)}>
          {isVisible ? "Hide OAuth JSON" : "Show OAuth JSON"}
        </Button>
        {fileName && <Typography.Text type="secondary">Selected: {fileName}</Typography.Text>}
      </Space>
      <Typography.Text type="secondary">Maximum size: 64 KiB</Typography.Text>
      {error && <Alert type="error" showIcon message={error} role="alert" />}
      <Button
        type="primary"
        loading={isImporting}
        disabled={!accessToken || !credentialName || !oauthJSON.trim()}
        onClick={() => void importCredential()}
      >
        {overwriteExisting ? "Replace OAuth JSON" : "Import OAuth JSON"}
      </Button>
    </Space>
  );
}
