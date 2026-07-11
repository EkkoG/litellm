import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Alert, Button, Space, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deriveErrorMessage,
  githubCopilotCredentialDevicePollCall,
  githubCopilotCredentialDeviceStartCall,
} from "../networking";

const { Link, Text } = Typography;

interface DeviceLoginResponse {
  login_id: string;
  verification_url: string;
  user_code: string;
  interval: number;
  expires_at: number;
}

interface Props {
  credentialName?: string;
  overwriteExisting?: boolean;
  onComplete?: () => void;
}

export default function GitHubCopilotCredentialDeviceLogin({
  credentialName,
  overwriteExisting = false,
  onComplete,
}: Props) {
  const { accessToken } = useAuthorized();
  const [deviceLogin, setDeviceLogin] = useState<DeviceLoginResponse | null>(null);
  const [status, setStatus] = useState<"idle" | "pending" | "complete">("idle");
  const [isStarting, setIsStarting] = useState(false);
  const [isPolling, setIsPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollInFlight = useRef(false);

  const pollLogin = useCallback(
    async (loginId: string) => {
      if (!accessToken || pollInFlight.current) return;
      pollInFlight.current = true;
      setIsPolling(true);
      setError(null);
      try {
        const response = (await githubCopilotCredentialDevicePollCall(accessToken, {
          login_id: loginId,
        })) as { status: "pending" | "complete" };
        if (response.status === "complete") {
          setStatus("complete");
          onComplete?.();
        }
      } catch (caughtError) {
        setError(deriveErrorMessage(caughtError));
        setDeviceLogin(null);
        setStatus("idle");
      } finally {
        pollInFlight.current = false;
        setIsPolling(false);
      }
    },
    [accessToken, onComplete],
  );

  useEffect(() => {
    if (!deviceLogin || status !== "pending") return;
    const intervalId = window.setInterval(
      () => void pollLogin(deviceLogin.login_id),
      Math.max((deviceLogin.interval || 5) + 5, 10) * 1000,
    );
    return () => window.clearInterval(intervalId);
  }, [deviceLogin, pollLogin, status]);

  useEffect(() => {
    if (!deviceLogin || status !== "pending") return;
    const remainingMs = deviceLogin.expires_at * 1000 - Date.now();
    const timeoutId = window.setTimeout(
      () => {
        setDeviceLogin(null);
        setStatus("idle");
        setError("GitHub device login expired. Start a new sign-in.");
      },
      Math.max(remainingMs, 0),
    );
    return () => window.clearTimeout(timeoutId);
  }, [deviceLogin, status]);

  const startLogin = async () => {
    if (!accessToken || !credentialName) return;
    setIsStarting(true);
    setError(null);
    try {
      const response = (await githubCopilotCredentialDeviceStartCall(accessToken, {
        credential_name: credentialName,
        overwrite_existing: overwriteExisting,
      })) as DeviceLoginResponse;
      setDeviceLogin(response);
      setStatus("pending");
    } catch (caughtError) {
      setError(deriveErrorMessage(caughtError));
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <Space direction="vertical" className="w-full" size="middle">
      <Button
        type="primary"
        onClick={startLogin}
        loading={isStarting}
        disabled={!credentialName || status === "pending"}
      >
        Sign in with GitHub
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
      {status === "complete" && <Alert type="success" showIcon message="GitHub Copilot credential connected" />}
      {error && <Alert type="error" showIcon message={error} />}
    </Space>
  );
}
