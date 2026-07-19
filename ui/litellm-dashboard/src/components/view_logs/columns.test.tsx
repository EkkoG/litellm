import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createColumns, formatUserDisplay, type LogEntry } from "./columns";
import { DataTable } from "./table";

const logEntry = (overrides: Partial<LogEntry>): LogEntry => ({
  request_id: "req-1",
  api_key: "key-1",
  team_id: "team-1",
  model: "gpt-4o",
  model_id: "model-1",
  call_type: "acompletion",
  spend: 0,
  total_tokens: 10,
  prompt_tokens: 5,
  completion_tokens: 5,
  startTime: "2026-07-07T09:50:13Z",
  endTime: "2026-07-07T09:50:14Z",
  cache_hit: "false",
  messages: [],
  response: {},
  ...overrides,
});

const renderLogEntry = (overrides: Partial<LogEntry>) =>
  render(<DataTable data={[logEntry(overrides)]} columns={createColumns()} getRowId={(row) => row.request_id} />);

describe("Cost column", () => {
  it("renders '-' for zero spend with no tooltip, so hovering never shows a contradictory $0", async () => {
    const user = userEvent.setup();
    render(
      <DataTable
        data={[logEntry({ request_id: "req-zero", spend: 0 })]}
        columns={createColumns()}
        getRowId={(r) => r.request_id}
      />,
    );
    for (const dash of screen.getAllByText("-")) {
      await user.hover(dash);
    }
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
  });

  it("shows the full-precision raw value in the tooltip for a real spend", async () => {
    const user = userEvent.setup();
    render(
      <DataTable
        data={[logEntry({ request_id: "req-spend", spend: 0.00012345678 })]}
        columns={createColumns()}
        getRowId={(r) => r.request_id}
      />,
    );
    const formatted = screen.getByText("$0.000123");
    await user.hover(formatted);
    expect(await screen.findByText("$0.00012345678")).toBeInTheDocument();
  });

  it("shows the summed session total, not the representative call's spend, for a multi-round session", () => {
    const overrides: Partial<LogEntry> = {
      request_id: "req-session",
      spend: 0.01,
      session_id: "sess-1",
      session_total_count: 3,
      session_total_spend: 0.06,
    };
    render(<DataTable data={[logEntry(overrides)]} columns={createColumns()} getRowId={(r) => r.request_id} />);
    expect(screen.getByText("$0.060000")).toBeInTheDocument();
    expect(screen.queryByText("$0.010000")).not.toBeInTheDocument();
    expect(screen.getByText("session total")).toBeInTheDocument();
  });
});

describe("Internal User column", () => {
  it("renders the alias and user ID when an alias is available", () => {
    renderLogEntry({
      request_id: "req-user-alias",
      user: "user-id-123",
      user_alias: "Alice",
    });

    expect(screen.getByText("Alice (user-id-123)")).toBeInTheDocument();
  });

  it("falls back to the user ID alone when no alias is set", () => {
    renderLogEntry({
      request_id: "req-user-no-alias",
      user: "user-id-456",
    });

    expect(screen.getByText("user-id-456")).toBeInTheDocument();
  });

  it("shows the user ID once when the alias equals the ID", () => {
    renderLogEntry({
      request_id: "req-user-alias-equals-id",
      user: "user-id-789",
      user_alias: "user-id-789",
    });

    expect(screen.getByText("user-id-789")).toBeInTheDocument();
  });

  it("shows '-' when no user is set", () => {
    renderLogEntry({
      request_id: "req-no-user",
    });

    const internalUserColumnIndex = screen.getByRole("columnheader", { name: "Internal User" }).cellIndex;
    const row = screen.getByText("req-no-user").closest("tr");

    expect(row).not.toBeNull();
    expect(within(row!).getAllByRole("cell")[internalUserColumnIndex]).toHaveTextContent("-");
  });
});

describe("formatUserDisplay", () => {
  it("formats alias with user ID in parentheses", () => {
    expect(formatUserDisplay("user-id-123", "Alice")).toBe("Alice (user-id-123)");
  });

  it("returns the user ID when alias is missing or matches the ID", () => {
    expect(formatUserDisplay("user-id-123", null)).toBe("user-id-123");
    expect(formatUserDisplay("user-id-123", "")).toBe("user-id-123");
    expect(formatUserDisplay("user-id-123", "user-id-123")).toBe("user-id-123");
  });

  it("returns '-' when user ID is missing", () => {
    expect(formatUserDisplay(undefined, "Alice")).toBe("-");
  });
});

describe("Prompt Cache column", () => {
  it("shows the provider prompt cache rate for OpenAI-compatible usage", () => {
    renderLogEntry({
      request_id: "req-openai-cache",
      prompt_tokens: 10000,
      metadata: {
        additional_usage_values: {
          prompt_tokens_details: { cached_tokens: 8000 },
        },
      },
    });

    expect(screen.getByText("Prompt Cache")).toBeInTheDocument();
    expect(screen.getByText("80.0%")).toBeInTheDocument();
    expect(screen.getByText("8,000 / 10,000")).toBeInTheDocument();
  });

  it("shows the provider prompt cache rate for Anthropic usage", () => {
    renderLogEntry({
      request_id: "req-anthropic-cache",
      prompt_tokens: 4000,
      metadata: {
        additional_usage_values: {
          cache_read_input_tokens: 3000,
          cache_creation_input_tokens: 500,
        },
      },
    });

    expect(screen.getByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText("3,000 / 4,000")).toBeInTheDocument();
  });

  it("shows '-' when the provider cache token count is null", () => {
    renderLogEntry({
      request_id: "req-null-cache",
      prompt_tokens: 4000,
      metadata: {
        additional_usage_values: {
          prompt_tokens_details: { cached_tokens: null },
        },
      },
    });

    const promptCacheColumnIndex = screen.getByRole("columnheader", { name: "Prompt Cache" }).cellIndex;
    const row = screen.getByText("req-null-cache").closest("tr");

    expect(row).not.toBeNull();
    expect(within(row!).getAllByRole("cell")[promptCacheColumnIndex]).toHaveTextContent("-");
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });
});
