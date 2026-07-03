import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Button, Alert, Space, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { chatgptCredentialDevicePollCall, chatgptCredentialDeviceStartCall, deriveErrorMessage } from "../networking";

const { Text, Link } = Typography;

interface ChatGPTDeviceLoginResponse {
  login_id: string;
  verification_url: string;
  user_code: string;
  interval: number;
  expires_at: number;
}

interface ChatGPTCredentialDeviceLoginProps {
  credentialName?: string;
  overwriteExisting?: boolean;
  onComplete?: () => void;
}

export default function ChatGPTCredentialDeviceLogin({
  credentialName,
  overwriteExisting = false,
  onComplete,
}: ChatGPTCredentialDeviceLoginProps) {
  const { accessToken } = useAuthorized();
  const [deviceLogin, setDeviceLogin] = useState<ChatGPTDeviceLoginResponse | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isPolling, setIsPolling] = useState(false);
  const [status, setStatus] = useState<"idle" | "pending" | "complete">("idle");
  const [error, setError] = useState<string | null>(null);

  const startLogin = useCallback(async () => {
    if (!accessToken || !credentialName) {
      return;
    }
    setIsStarting(true);
    setError(null);
    try {
      const response = await chatgptCredentialDeviceStartCall(accessToken, {
        credential_name: credentialName,
        overwrite_existing: overwriteExisting,
      });
      setDeviceLogin(response as ChatGPTDeviceLoginResponse);
      setStatus("pending");
    } catch (error) {
      setError(deriveErrorMessage(error));
    } finally {
      setIsStarting(false);
    }
  }, [accessToken, credentialName, overwriteExisting]);

  const pollLogin = useCallback(
    async (loginId?: string) => {
      if (!accessToken || !loginId) {
        return;
      }
      setIsPolling(true);
      setError(null);
      try {
        const response = (await chatgptCredentialDevicePollCall(accessToken, {
          login_id: loginId,
        })) as { status: "pending" | "complete" };
        if (response.status === "complete") {
          setStatus("complete");
          onComplete?.();
        }
      } catch (error) {
        setError(deriveErrorMessage(error));
      } finally {
        setIsPolling(false);
      }
    },
    [accessToken, onComplete],
  );

  useEffect(() => {
    if (!deviceLogin || status !== "pending") {
      return;
    }
    const intervalMs = Math.max(deviceLogin.interval || 5, 5) * 1000;
    const intervalId = window.setInterval(() => {
      void pollLogin(deviceLogin.login_id);
    }, intervalMs);
    return () => window.clearInterval(intervalId);
  }, [deviceLogin, pollLogin, status]);

  return (
    <Space direction="vertical" className="w-full" size="middle">
      <Button
        type="primary"
        onClick={startLogin}
        loading={isStarting}
        disabled={!credentialName || !accessToken || status === "pending"}
      >
        Sign in with ChatGPT
      </Button>
      {deviceLogin && status === "pending" && (
        <Alert
          type="info"
          showIcon
          message={
            <Space direction="vertical" size={4}>
              <Link href={deviceLogin.verification_url} target="_blank" rel="noreferrer">
                {deviceLogin.verification_url}
              </Link>
              <Text code copyable>
                {deviceLogin.user_code}
              </Text>
              <Button onClick={() => pollLogin(deviceLogin.login_id)} loading={isPolling}>
                Check status
              </Button>
            </Space>
          }
        />
      )}
      {status === "complete" && <Alert type="success" showIcon message="ChatGPT credential connected" />}
      {error && <Alert type="error" showIcon message={error} />}
    </Space>
  );
}
