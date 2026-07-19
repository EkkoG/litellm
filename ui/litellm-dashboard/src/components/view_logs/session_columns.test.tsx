import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DataTable } from "./table";
import { createSessionColumns, type SessionLogEntry } from "./session_columns";

const sessionRow: SessionLogEntry = {
  row_type: "session",
  group_id: "session:sess-1",
  session_id: "sess-1",
  request_id: null,
  session_start_time: "2026-07-14T01:00:00Z",
  session_end_time: "2026-07-14T01:01:00Z",
  last_active: "2026-07-14T01:00:50Z",
  request_count: 9,
  session_spend: 0.06,
  session_duration_ms: 60000,
  session_total_tokens: 5000,
  session_prompt_tokens: 4000,
  session_completion_tokens: 1000,
  session_cache_read_tokens: 3000,
  success_count: 8,
  failure_count: 1,
  llm_count: 5,
  agent_count: 1,
  mcp_count: 3,
  models: ["gpt-5.2", "gpt-5.2-mini"],
  providers: ["openai"],
  team_ids: ["team-1"],
  api_keys: ["key-1"],
  users: ["user-1"],
  user_aliases: [{ user_id: "user-1", user_alias: "Alice" }],
  end_users: ["end-user-1"],
  team_names: ["Platform"],
  key_aliases: ["codex-key"],
  primary_model: "gpt-5.2",
  call_type: "acompletion",
  model_id: "model-1",
  api_base: null,
};

describe("session log columns", () => {
  it("renders stable session summaries and a weighted prompt cache rate", () => {
    render(
      <DataTable
        data={[sessionRow]}
        columns={createSessionColumns({ sortBy: "startTime", sortOrder: "desc", onSortChange: vi.fn() })}
        getRowId={(row) => row.group_id}
      />,
    );

    expect(screen.getByRole("columnheader", { name: /Last Active/ })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Request ID" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /TTFT/ })).not.toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    expect(screen.getByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText("3,000 / 4,000")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.2 +1")).toBeInTheDocument();
  });

  it("renders the user alias with user ID when aliases are provided", () => {
    render(
      <DataTable
        data={[sessionRow]}
        columns={createSessionColumns({ sortBy: "startTime", sortOrder: "desc", onSortChange: vi.fn() })}
        getRowId={(row) => row.group_id}
      />,
    );

    expect(screen.getByText("Alice (user-1)")).toBeInTheDocument();
  });

  it("falls back to plain user IDs when no aliases are provided", () => {
    render(
      <DataTable
        data={[{ ...sessionRow, user_aliases: null }]}
        columns={createSessionColumns({ sortBy: "startTime", sortOrder: "desc", onSortChange: vi.fn() })}
        getRowId={(row) => row.group_id}
      />,
    );

    expect(screen.getByText("user-1")).toBeInTheDocument();
  });

  it("shows truncated alias display with count for multiple users", () => {
    render(
      <DataTable
        data={[
          {
            ...sessionRow,
            users: ["user-1", "user-2"],
            user_aliases: [
              { user_id: "user-1", user_alias: "Alice" },
              { user_id: "user-2", user_alias: null },
            ],
          },
        ]}
        columns={createSessionColumns({ sortBy: "startTime", sortOrder: "desc", onSortChange: vi.fn() })}
        getRowId={(row) => row.group_id}
      />,
    );

    expect(screen.getByText("Alice (user-1) +1")).toBeInTheDocument();
  });
});
