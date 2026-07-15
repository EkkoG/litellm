import type { CredentialItem } from "@/components/networking";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UploadProps } from "antd/es/upload";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CredentialsPanel from "./credentials";

const DEFAULT_UPLOAD_PROPS = {} as UploadProps;

const { mockChatGPTResetCreditConsumeCall, mockChatGPTSubscriptionStatusCall } = vi.hoisted(() => ({
  mockChatGPTResetCreditConsumeCall: vi.fn(),
  mockChatGPTSubscriptionStatusCall: vi.fn(),
}));

const mockUseAuthorized = vi.fn();
const mockUseCredentials = vi.fn();

vi.mock("@/components/networking", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/networking")>();
  return {
    ...actual,
    chatgptCredentialResetCreditConsumeCall: mockChatGPTResetCreditConsumeCall,
    chatgptCredentialSubscriptionStatusCall: mockChatGPTSubscriptionStatusCall,
  };
});

vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => mockUseAuthorized(),
}));

vi.mock("@/app/(dashboard)/hooks/credentials/useCredentials", () => ({
  useCredentials: () => mockUseCredentials(),
}));

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

describe("CredentialsPanel", () => {
  beforeEach(() => {
    mockChatGPTResetCreditConsumeCall.mockReset();
    mockChatGPTSubscriptionStatusCall.mockReset();
  });

  it("should render", () => {
    mockUseAuthorized.mockReturnValue({ accessToken: "test-token", userRole: "Admin" });
    mockUseCredentials.mockReturnValue({
      data: { credentials: [] },
      refetch: vi.fn(),
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
      </QueryClientProvider>,
    );

    expect(screen.getByRole("button", { name: /add credential/i })).toBeInTheDocument();
  });

  it("should display provided credentials", () => {
    const credentials: CredentialItem[] = [
      {
        credential_name: "openai-key",
        credential_values: {},
        credential_info: { custom_llm_provider: "openai" },
      },
    ];

    mockUseAuthorized.mockReturnValue({ accessToken: "test-token", userRole: "Admin" });
    mockUseCredentials.mockReturnValue({
      data: { credentials },
      refetch: vi.fn(),
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
      </QueryClientProvider>,
    );

    expect(screen.getByText("openai-key")).toBeInTheDocument();
    expect(mockChatGPTSubscriptionStatusCall).not.toHaveBeenCalled();
  });

  it("should display ChatGPT subscription status for ChatGPT credentials", async () => {
    const credentials: CredentialItem[] = [
      {
        credential_name: "chatgpt-admin",
        credential_values: {},
        credential_info: { custom_llm_provider: "chatgpt" },
      },
    ];
    const subscriptionStatus = {
      credential_name: "chatgpt-admin",
      success: true,
      credential_status: "valid",
      plan_label: "Pro",
      tiers: [
        { name: "five_hour", remaining_percent: 74.94, resets_at: null },
        { name: "seven_day", remaining_percent: 90, resets_at: null },
      ],
      daily_snapshot: {
        date: "2026-07-15",
        timezone: "UTC",
        captured_at: "2026-07-15T00:00:00Z",
        tiers: [
          { name: "five_hour", remaining_percent: 80.5, resets_at: null },
          { name: "seven_day", remaining_percent: 92, resets_at: null },
        ],
      },
      rate_limit_reset_credits: null,
      error: null,
      queried_at: Date.now(),
    };
    mockChatGPTSubscriptionStatusCall.mockResolvedValue(subscriptionStatus);

    mockUseAuthorized.mockReturnValue({ accessToken: "test-token", userRole: "Admin" });
    mockUseCredentials.mockReturnValue({
      data: { credentials },
      refetch: vi.fn(),
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Pro")).toBeInTheDocument();
    });
    expect(screen.getByText("5h 74.9% remaining")).toBeInTheDocument();
    expect(screen.getByText("7d 90% remaining")).toBeInTheDocument();
    expect(screen.getByText("Daily start 2026-07-15 (UTC): 5h 80.5% · 7d 92%")).toBeInTheDocument();
    expect(mockChatGPTSubscriptionStatusCall).toHaveBeenCalledWith("test-token", "chatgpt-admin");
  });

  it("should display reset credits and consume a selected credit", async () => {
    const credentials: CredentialItem[] = [
      {
        credential_name: "chatgpt-admin",
        credential_values: {},
        credential_info: { custom_llm_provider: "chatgpt" },
      },
    ];
    const subscriptionStatus = {
      credential_name: "chatgpt-admin",
      success: true,
      credential_status: "valid",
      plan_label: "Plus",
      tiers: [],
      rate_limit_reset_credits: {
        available_count: 1,
        credits: [
          {
            id: "credit-1",
            reset_type: "codex_rate_limits",
            status: "available",
            granted_at: "2026-07-12T00:00:00Z",
            title: "Weekly reset",
            description: "Thanks for using Codex! You've been granted one free rate limit reset.",
            expires_at: "2099-07-12T00:00:00Z",
          },
        ],
      },
      error: null,
      queried_at: Date.now(),
    };
    const resetCreditConsumeResponse = {
      credential_name: "chatgpt-admin",
      success: true,
      credential_status: "valid",
      outcome: "reset",
      windows_reset: 2,
      error: null,
      queried_at: Date.now(),
    };
    mockChatGPTSubscriptionStatusCall.mockResolvedValue(subscriptionStatus);
    mockChatGPTResetCreditConsumeCall.mockResolvedValue(resetCreditConsumeResponse);

    mockUseAuthorized.mockReturnValue({ accessToken: "test-token", userRole: "Admin" });
    mockUseCredentials.mockReturnValue({
      data: { credentials },
      refetch: vi.fn(),
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Weekly reset")).toBeInTheDocument();
    });
    expect(
      screen.queryByText("Thanks for using Codex! You've been granted one free rate limit reset."),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/^Expires in /)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^use$/i }));

    await waitFor(() => {
      expect(mockChatGPTResetCreditConsumeCall).toHaveBeenCalledWith(
        "test-token",
        "chatgpt-admin",
        expect.any(String),
        "credit-1",
      );
    });
  });

  it("should display empty state when no credentials are provided", () => {
    mockUseAuthorized.mockReturnValue({ accessToken: "test-token", userRole: "Admin" });
    mockUseCredentials.mockReturnValue({
      data: { credentials: [] },
      refetch: vi.fn(),
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
      </QueryClientProvider>,
    );

    expect(screen.getByText("No credentials configured")).toBeInTheDocument();
  });

  it("should open add modal when add button is clicked", async () => {
    mockUseAuthorized.mockReturnValue({ accessToken: "test-token", userRole: "Admin" });
    mockUseCredentials.mockReturnValue({
      data: { credentials: [] },
      refetch: vi.fn(),
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
      </QueryClientProvider>,
    );

    const addButton = screen.getByRole("button", { name: /add credential/i });

    act(() => {
      fireEvent.click(addButton);
    });

    await waitFor(() => {
      expect(screen.getByText("Add New Credential")).toBeInTheDocument();
    });
  });

  describe("Admin Viewer write-action gating", () => {
    // Admin Viewer can VIEW credentials but must not be able to add / edit /
    // delete them. The page shows the credential list read-only.
    const credentials: CredentialItem[] = [
      {
        credential_name: "openai-key",
        credential_values: {},
        credential_info: { custom_llm_provider: "openai" },
      },
    ];

    it("hides the Add Credential button for Admin Viewer", () => {
      mockUseAuthorized.mockReturnValue({
        accessToken: "test-token",
        userRole: "Admin Viewer",
      });
      mockUseCredentials.mockReturnValue({
        data: { credentials },
        refetch: vi.fn(),
      });

      render(
        <QueryClientProvider client={createQueryClient()}>
          <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
        </QueryClientProvider>,
      );

      // Credential row still renders (read parity).
      expect(screen.getByText("openai-key")).toBeInTheDocument();
      // But no Add Credential button (write blocked).
      expect(screen.queryByRole("button", { name: /add credential/i })).not.toBeInTheDocument();
    });

    it("hides Edit / Delete buttons on existing credentials for Admin Viewer", () => {
      mockUseAuthorized.mockReturnValue({
        accessToken: "test-token",
        userRole: "Admin Viewer",
      });
      mockUseCredentials.mockReturnValue({
        data: { credentials },
        refetch: vi.fn(),
      });

      const { container } = render(
        <QueryClientProvider client={createQueryClient()}>
          <CredentialsPanel uploadProps={DEFAULT_UPLOAD_PROPS} />
        </QueryClientProvider>,
      );

      // The Actions cell should be empty (no edit/delete buttons rendered).
      // We rely on the row being visible but containing no `<button>`s in
      // the actions column — easier-to-read assertion: the entire panel
      // contains zero buttons in admin-viewer mode.
      expect(container.querySelectorAll("button").length).toBe(0);
    });
  });
});
