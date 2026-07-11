import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GitHubCopilotCredentialDeviceLogin from "./GitHubCopilotCredentialDeviceLogin";
import { githubCopilotCredentialDevicePollCall, githubCopilotCredentialDeviceStartCall } from "../networking";

vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => ({ accessToken: "test-token" }),
}));

vi.mock("../networking", () => ({
  deriveErrorMessage: (error: Error) => error.message,
  githubCopilotCredentialDeviceStartCall: vi.fn(),
  githubCopilotCredentialDevicePollCall: vi.fn(),
}));

describe("GitHubCopilotCredentialDeviceLogin", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(githubCopilotCredentialDeviceStartCall).mockResolvedValue({
      login_id: "login-1",
      verification_url: "https://github.com/login/device",
      user_code: "ABCD-EFGH",
      interval: 5,
      expires_at: Date.now() / 1000 + 60,
    });
  });

  it("allows a new sign-in after polling fails", async () => {
    vi.mocked(githubCopilotCredentialDevicePollCall).mockRejectedValue(new Error("Device login expired"));
    render(<GitHubCopilotCredentialDeviceLogin credentialName="copilot-admin" />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in with GitHub" }));
    fireEvent.click(await screen.findByRole("button", { name: "Check status" }));

    await waitFor(() => {
      expect(screen.getByText("Device login expired")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sign in with GitHub" })).toBeEnabled();
    });
  });
});
