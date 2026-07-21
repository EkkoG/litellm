import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers } from "../provider_info_helpers";
import { CredentialItem } from "../networking";
import CredentialModal from "./CredentialModal";

const { mockXAIOAuthCredentialImportCall } = vi.hoisted(() => ({
  mockXAIOAuthCredentialImportCall: vi.fn(),
}));

vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => ({ accessToken: "test-token" }),
}));

vi.mock("../networking", async () => {
  const actual = await vi.importActual("../networking");
  return {
    ...actual,
    xaiOAuthCredentialImportCall: mockXAIOAuthCredentialImportCall,
    getProviderCreateMetadata: vi.fn().mockResolvedValue([
      {
        provider: "OpenAI",
        provider_display_name: Providers.OpenAI,
        litellm_provider: "openai",
        default_model_placeholder: "gpt-3.5-turbo",
        credential_fields: [
          {
            key: "api_key",
            label: "OpenAI API Key",
            field_type: "password",
            required: true,
          },
          {
            key: "api_base",
            label: "API Base",
            field_type: "text",
            placeholder: "https://api.openai.com/v1",
          },
        ],
      },
      {
        provider: "Anthropic",
        provider_display_name: Providers.Anthropic,
        litellm_provider: "anthropic",
        default_model_placeholder: "claude-3-opus-20240229",
        credential_fields: [
          {
            key: "api_key",
            label: "Anthropic API Key",
            field_type: "password",
            required: true,
          },
        ],
      },
      {
        provider: "xAI",
        provider_display_name: Providers.xAI,
        litellm_provider: "xai",
        credential_fields: [
          {
            key: "api_key",
            label: "xAI API Key",
            field_type: "password",
            required: true,
          },
        ],
      },
    ]),
  };
});

afterEach(cleanup);

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

const mockUploadProps = {
  beforeUpload: vi.fn(),
  onChange: vi.fn(),
};

const mockCredential: CredentialItem = {
  credential_name: "test-credential",
  credential_values: {
    api_key: "test-api-key",
    api_base: "https://api.test.com",
  },
  credential_info: {
    custom_llm_provider: Providers.OpenAI,
  },
};

const mockChatGPTCredential: CredentialItem = {
  credential_name: "chatgpt-admin",
  credential_values: {
    api_key: "****abcd",
    chatgpt_refresh_token: "****wxyz",
  },
  credential_info: {
    custom_llm_provider: "chatgpt",
  },
};

const mockGitHubCopilotCredential: CredentialItem = {
  credential_name: "copilot-admin",
  credential_values: { api_key: "****abcd" },
  credential_info: { custom_llm_provider: "github_copilot" },
};

const mockGoogleCredential: CredentialItem = {
  credential_name: "gemini-admin",
  credential_values: { api_key: "test-api-key" },
  credential_info: { custom_llm_provider: "gemini" },
};

const mockXAIOAuthCredential: CredentialItem = {
  credential_name: "xai-oauth",
  credential_values: { access_token: "****abcd" },
  credential_info: { custom_llm_provider: "xai", auth_type: "oauth_json_import" },
};

const renderModal = (props: Partial<React.ComponentProps<typeof CredentialModal>> = {}) =>
  render(
    <QueryClientProvider client={createQueryClient()}>
      <CredentialModal
        open={true}
        mode="add"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
        uploadProps={mockUploadProps}
        {...props}
      />
    </QueryClientProvider>,
  );

describe("CredentialModal", () => {
  beforeEach(() => {
    mockXAIOAuthCredentialImportCall.mockReset();
    mockXAIOAuthCredentialImportCall.mockResolvedValue({ success: true });
  });

  describe("add mode", () => {
    it("renders the add title and an editable credential name", () => {
      renderModal({ mode: "add" });

      expect(screen.getByText("Add New Credential")).toBeInTheDocument();
      expect(screen.getByText("Add Credential")).toBeInTheDocument();
      const nameInput = screen.getByLabelText("Credential Name:") as HTMLInputElement;
      expect(nameInput.value).toBe("");
      expect(nameInput.disabled).toBe(false);
    });

    it("shows provider-specific fields for the selected provider", async () => {
      renderModal({ mode: "add" });

      await waitFor(() => {
        expect(screen.getByLabelText("OpenAI API Key")).toBeInTheDocument();
        expect(screen.getByPlaceholderText("https://api.openai.com/v1")).toBeInTheDocument();
      });
    });

    it("renders ChatGPT device login when selected", async () => {
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "add" });

      const providerSelect = screen.getByLabelText("Provider:");
      await user.click(providerSelect);
      await user.type(providerSelect, "chatgpt");
      await user.click(await screen.findByText("ChatGPT", { selector: "span" }));

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Sign in with ChatGPT" })).toBeInTheDocument();
        expect(screen.queryByLabelText("OpenAI API Key")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Add Credential" })).not.toBeInTheDocument();
      });
    });

    it("renders GitHub Copilot device login when selected", async () => {
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "add" });

      const providerSelect = screen.getByLabelText("Provider:");
      await user.click(providerSelect);
      await user.type(providerSelect, "copilot");
      await user.click(await screen.findByText("Github Copilot"));

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Sign in with GitHub" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Add Credential" })).not.toBeInTheDocument();
      });
    });

    it("defaults xAI to API key authentication", async () => {
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "add" });

      const providerSelect = screen.getByLabelText("Provider:");
      await user.click(providerSelect);
      await user.type(providerSelect, "xAI");
      await user.click(await screen.findByText("xAI", { selector: "span" }));

      expect(screen.getByRole("radio", { name: "API key" })).toBeChecked();
      expect(screen.getByRole("radio", { name: "OAuth JSON" })).not.toBeChecked();
      expect(await screen.findByLabelText("xAI API Key")).toBeInTheDocument();
      expect(screen.queryByPlaceholderText("Paste xAI OAuth JSON")).not.toBeInTheDocument();
    });

    it("imports pasted xAI OAuth JSON without previewing it by default", async () => {
      const onCredentialComplete = vi.fn();
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "add", onCredentialComplete });

      await user.type(screen.getByLabelText("Credential Name:"), "xai-oauth");
      const providerSelect = screen.getByLabelText("Provider:");
      await user.click(providerSelect);
      await user.type(providerSelect, "xAI");
      await user.click(await screen.findByText("xAI", { selector: "span" }));
      await user.click(screen.getByRole("radio", { name: "OAuth JSON" }));

      const oauthJSONInput = screen.getByPlaceholderText("Paste xAI OAuth JSON");
      expect(oauthJSONInput).toHaveAttribute("type", "password");
      fireEvent.change(oauthJSONInput, { target: { value: '{"access_token":"secret"}' } });
      await user.click(screen.getByRole("button", { name: "Import OAuth JSON" }));

      await waitFor(() => {
        expect(mockXAIOAuthCredentialImportCall).toHaveBeenCalledWith("test-token", {
          credential_name: "xai-oauth",
          auth_json: '{"access_token":"secret"}',
          overwrite_existing: false,
        });
      });
      expect(onCredentialComplete).toHaveBeenCalledOnce();
      expect(screen.queryByPlaceholderText("Paste xAI OAuth JSON")).not.toBeInTheDocument();
    });

    it("rejects pasted xAI OAuth JSON over 64 KiB", async () => {
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "add" });

      const providerSelect = screen.getByLabelText("Provider:");
      await user.click(providerSelect);
      await user.type(providerSelect, "xAI");
      await user.click(await screen.findByText("xAI", { selector: "span" }));
      await user.click(screen.getByRole("radio", { name: "OAuth JSON" }));

      await user.click(screen.getByRole("button", { name: "Show OAuth JSON" }));
      fireEvent.change(screen.getByPlaceholderText("Paste xAI OAuth JSON"), {
        target: { value: "x".repeat(64 * 1024 + 1) },
      });

      expect(await screen.findByRole("alert")).toHaveTextContent("OAuth JSON must be 64 KiB or smaller");
      expect(screen.getByPlaceholderText("Paste xAI OAuth JSON")).toHaveValue("");
      expect(screen.getByRole("button", { name: "Import OAuth JSON" })).toBeDisabled();
    });
  });

  describe("edit mode", () => {
    it("renders the edit title and update button", () => {
      renderModal({ mode: "edit", existingCredential: mockCredential });

      expect(screen.getByText("Edit Credential")).toBeInTheDocument();
      expect(screen.getByText("Update Credential")).toBeInTheDocument();
    });

    it("prefills the credential name and disables it", async () => {
      renderModal({ mode: "edit", existingCredential: mockCredential });

      await waitFor(() => {
        const nameInput = screen.getByLabelText("Credential Name:") as HTMLInputElement;
        expect(nameInput.value).toBe("test-credential");
        expect(nameInput.disabled).toBe(true);
      });
    });

    it("disables the name from the mode, not the credential's name value", () => {
      renderModal({
        mode: "edit",
        existingCredential: { ...mockCredential, credential_name: "" },
      });

      expect((screen.getByLabelText("Credential Name:") as HTMLInputElement).disabled).toBe(true);
    });

    it("renders ChatGPT reconnect instead of masked token fields", () => {
      renderModal({ mode: "edit", existingCredential: mockChatGPTCredential });

      expect(screen.getByRole("button", { name: "Sign in with ChatGPT" })).toBeInTheDocument();
      expect(screen.queryByDisplayValue("****abcd")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Update Credential" })).not.toBeInTheDocument();
    });

    it("renders GitHub Copilot reconnect instead of masked token fields", () => {
      renderModal({ mode: "edit", existingCredential: mockGitHubCopilotCredential });

      expect(screen.getByRole("button", { name: "Sign in with GitHub" })).toBeInTheDocument();
      expect(screen.queryByDisplayValue("****abcd")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Update Credential" })).not.toBeInTheDocument();
    });

    it("preserves the backend provider id when updating a standard credential", async () => {
      const onSubmit = vi.fn();
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "edit", existingCredential: mockGoogleCredential, onSubmit });

      await user.click(await screen.findByRole("button", { name: "Update Credential" }));

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ custom_llm_provider: "gemini" }));
      });
    });

    it("replaces an existing xAI OAuth credential without exposing stored tokens", async () => {
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "edit", existingCredential: mockXAIOAuthCredential });

      expect(screen.getByRole("radio", { name: "OAuth JSON" })).toBeChecked();
      expect(screen.queryByDisplayValue("****abcd")).not.toBeInTheDocument();
      fireEvent.change(screen.getByPlaceholderText("Paste xAI OAuth JSON"), {
        target: { value: '{"access_token":"replacement"}' },
      });
      await user.click(screen.getByRole("button", { name: "Replace OAuth JSON" }));

      await waitFor(() => {
        expect(mockXAIOAuthCredentialImportCall).toHaveBeenCalledWith("test-token", {
          credential_name: "xai-oauth",
          auth_json: '{"access_token":"replacement"}',
          overwrite_existing: true,
        });
      });
    });

    it("clears xAI OAuth JSON after an import error", async () => {
      mockXAIOAuthCredentialImportCall.mockRejectedValue(new Error("Invalid xAI OAuth credential JSON"));
      const user = userEvent.setup({ delay: null });
      renderModal({ mode: "edit", existingCredential: mockXAIOAuthCredential });

      fireEvent.change(screen.getByPlaceholderText("Paste xAI OAuth JSON"), {
        target: { value: '{"access_token":"invalid"}' },
      });
      await user.click(screen.getByRole("button", { name: "Replace OAuth JSON" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Invalid xAI OAuth credential JSON");
      expect(screen.getByPlaceholderText("Paste xAI OAuth JSON")).toHaveValue("");
    });
  });
});
