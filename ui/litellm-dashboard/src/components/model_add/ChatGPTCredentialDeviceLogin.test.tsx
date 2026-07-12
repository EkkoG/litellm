import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { chatgptCredentialDevicePollCall, chatgptCredentialDeviceStartCall } from "../networking";
import ChatGPTCredentialDeviceLogin from "./ChatGPTCredentialDeviceLogin";

vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => ({ accessToken: "test-token" }),
}));

vi.mock("../networking", () => ({
  chatgptCredentialDeviceStartCall: vi.fn(),
  chatgptCredentialDevicePollCall: vi.fn(),
  deriveErrorMessage: (error: Error) => error.message,
}));

const deviceLoginResponse = {
  login_id: "login-1",
  verification_url: "https://auth.openai.com/activate",
  user_code: "ABCD-EFGH",
  interval: 5,
  expires_at: Date.now() / 1000 + 60,
};

describe("ChatGPTCredentialDeviceLogin", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chatgptCredentialDeviceStartCall).mockResolvedValue(deviceLoginResponse);
  });

  it("allows a new sign-in after polling fails", async () => {
    vi.mocked(chatgptCredentialDevicePollCall).mockRejectedValue(new Error("Device login expired"));
    render(<ChatGPTCredentialDeviceLogin credentialName="chatgpt-admin" />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    fireEvent.click(await screen.findByRole("button", { name: "Check status" }));

    await waitFor(() => {
      expect(screen.getByText("Device login expired")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sign in with ChatGPT" })).toBeEnabled();
    });
  });
});
