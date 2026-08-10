"use client";

import { DataTableSortHeader } from "@/components/shared/DataTable";
import { CellTooltip, DateCell, IdCell, MoneyCell, StatusBadge } from "@/components/shared/table_cells";
import type { ColumnDef } from "@tanstack/react-table";
import { formatUserDisplay } from "./columns";
import { AgentIcon, SparkleIcon, WrenchIcon } from "./TypeBadges";

export interface UserAliasEntry {
  user_id: string;
  user_alias: string | null;
}

export interface SessionLogEntry {
  row_type: "session" | "request";
  group_id: string;
  session_id: string | null;
  request_id: string | null;
  session_start_time: string;
  session_end_time: string;
  last_active: string;
  request_count: number;
  session_spend: number;
  session_duration_ms: number;
  session_total_tokens: number;
  session_prompt_tokens: number;
  session_completion_tokens: number;
  session_cache_read_tokens: number;
  success_count: number;
  failure_count: number;
  llm_count: number;
  agent_count: number;
  mcp_count: number;
  models: string[] | null;
  providers: string[] | null;
  team_ids: string[] | null;
  api_keys: string[] | null;
  users: string[] | null;
  user_aliases: UserAliasEntry[] | null;
  end_users: string[] | null;
  team_names: string[] | null;
  key_aliases: string[] | null;
  primary_model: string | null;
  call_type: string;
  model_id: string | null;
  api_base: string | null;
}

const getValues = (values: string[] | null | undefined): string[] => values?.filter(Boolean) ?? [];

function MultiValueCell({ values, fallback = "-" }: { values: string[] | null | undefined; fallback?: string }) {
  const normalizedValues = getValues(values);
  if (normalizedValues.length === 0) return <span>{fallback}</span>;

  const label =
    normalizedValues.length === 1 ? normalizedValues[0] : `${normalizedValues[0]} +${normalizedValues.length - 1}`;
  return (
    <CellTooltip
      content={normalizedValues.join(", ")}
      trigger={<span className="max-w-[18ch] truncate block">{label}</span>}
    />
  );
}

export const getSessionLogsTableColumns = (): ColumnDef<SessionLogEntry>[] => [
  {
    id: "startTime",
    accessorFn: (row) => row.last_active,
    header: ({ column }) => <DataTableSortHeader column={column} title="Last Active" variant="dropdown-tristate" />,
    size: 200,
    enableSorting: true,
    cell: ({ row }) => <DateCell value={row.original.last_active} />,
  },
  {
    id: "composition",
    header: "Composition",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => {
      const {
        request_count: requestCount,
        llm_count: llmCount,
        agent_count: agentCount,
        mcp_count: mcpCount,
      } = row.original;
      const details = [
        llmCount > 0 ? `${llmCount} LLM` : null,
        agentCount > 0 ? `${agentCount} Agent` : null,
        mcpCount > 0 ? `${mcpCount} MCP` : null,
      ].filter(Boolean);
      const badge = (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-700 border border-blue-200 rounded-full text-[11px] font-medium whitespace-nowrap">
          <SparkleIcon />
          <span>{requestCount}</span>
          {agentCount > 0 ? <AgentIcon size={10} /> : null}
          {mcpCount > 0 ? <WrenchIcon /> : null}
        </span>
      );
      return <CellTooltip content={details.join(" • ")} trigger={badge} />;
    },
  },
  {
    id: "status_summary",
    header: "Status Summary",
    size: 130,
    enableSorting: false,
    cell: ({ row }) => {
      const { failure_count: failureCount, request_count: requestCount } = row.original;
      if (failureCount === 0) return <StatusBadge tone="success" label="Success" />;
      if (failureCount === requestCount) return <StatusBadge tone="error" label="Failure" />;
      return <StatusBadge tone="warning" label={`${failureCount} failed`} />;
    },
  },
  {
    id: "session_id",
    header: "Session ID",
    size: 140,
    enableSorting: false,
    cell: ({ row }) => {
      const value = row.original.session_id ?? row.original.request_id ?? undefined;
      return <IdCell value={value} variant={row.original.session_id ? undefined : "plain"} />;
    },
  },
  {
    id: "spend",
    accessorFn: (row) => row.session_spend,
    header: ({ column }) => <DataTableSortHeader column={column} title="Session Cost" variant="dropdown-tristate" />,
    size: 125,
    enableSorting: true,
    meta: { numeric: true },
    cell: ({ row }) => <MoneyCell value={row.original.session_spend} decimals={6} />,
  },
  {
    id: "request_duration_ms",
    accessorFn: (row) => row.session_duration_ms,
    header: ({ column }) => <DataTableSortHeader column={column} title="Duration (s)" variant="dropdown-tristate" />,
    size: 120,
    enableSorting: true,
    meta: { numeric: true },
    cell: ({ row }) => {
      const milliseconds = row.original.session_duration_ms;
      return Number.isFinite(milliseconds) ? <span>{(milliseconds / 1000).toFixed(2)}</span> : <span>-</span>;
    },
  },
  {
    id: "model",
    accessorFn: (row) => row.primary_model,
    header: ({ column }) => <DataTableSortHeader column={column} title="Models" variant="dropdown-tristate" />,
    size: 190,
    enableSorting: true,
    cell: ({ row }) => <MultiValueCell values={row.original.models} fallback={row.original.primary_model ?? "-"} />,
  },
  {
    id: "total_tokens",
    accessorFn: (row) => row.session_total_tokens,
    header: ({ column }) => <DataTableSortHeader column={column} title="Session Tokens" variant="dropdown-tristate" />,
    size: 165,
    enableSorting: true,
    meta: { numeric: true },
    cell: ({ row }) => (
      <span>
        {row.original.session_total_tokens.toLocaleString()}
        <span className="text-gray-400 text-xs ml-1">
          ({row.original.session_prompt_tokens.toLocaleString()}+
          {row.original.session_completion_tokens.toLocaleString()})
        </span>
      </span>
    ),
  },
  {
    id: "prompt_cache",
    header: "Prompt Cache",
    size: 150,
    enableSorting: false,
    meta: { numeric: true },
    cell: ({ row }) => {
      const promptTokens = row.original.session_prompt_tokens;
      const cacheReadTokens = row.original.session_cache_read_tokens;
      if (promptTokens <= 0) return <span>-</span>;

      return (
        <div className="flex flex-col items-end">
          <span>{((cacheReadTokens / promptTokens) * 100).toFixed(1)}%</span>
          <span className="text-[10px] text-gray-400">
            {cacheReadTokens.toLocaleString()} / {promptTokens.toLocaleString()}
          </span>
        </div>
      );
    },
  },
  {
    id: "team",
    header: "Team",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => <MultiValueCell values={row.original.team_names ?? row.original.team_ids} />,
  },
  {
    id: "key_alias",
    header: "Key Alias",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => <MultiValueCell values={row.original.key_aliases} />,
  },
  {
    id: "users",
    header: "Internal Users",
    size: 150,
    enableSorting: false,
    cell: ({ row }) => {
      const aliases = row.original.user_aliases;
      const values =
        aliases && aliases.length > 0
          ? aliases.map((entry) => formatUserDisplay(entry.user_id, entry.user_alias))
          : row.original.users;
      return <MultiValueCell values={values} />;
    },
  },
];
