import { DateCell, IdCell, MoneyCell, StatusBadge } from "@/components/shared/table_cells";
import type { ColumnDef } from "@tanstack/react-table";
import { Tooltip } from "antd";
import { AgentIcon, SparkleIcon, WrenchIcon } from "./TypeBadges";
import { LogsSortProps, SortableHeader } from "./columns";

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
  end_users: string[] | null;
  team_names: string[] | null;
  key_aliases: string[] | null;
  primary_model: string | null;
  call_type: string;
  model_id: string | null;
  api_base: string | null;
}

const getValues = (values: string[] | null | undefined): string[] => values?.filter(Boolean) ?? [];

const MultiValueCell = ({ values, fallback = "-" }: { values: string[] | null | undefined; fallback?: string }) => {
  const normalizedValues = getValues(values);
  if (normalizedValues.length === 0) return <span>{fallback}</span>;

  const label =
    normalizedValues.length === 1 ? normalizedValues[0] : `${normalizedValues[0]} +${normalizedValues.length - 1}`;
  return (
    <Tooltip title={normalizedValues.join(", ")}>
      <span className="max-w-[18ch] truncate block">{label}</span>
    </Tooltip>
  );
};

export const createSessionColumns = (sortProps: LogsSortProps): ColumnDef<SessionLogEntry>[] => [
  {
    header: () => (
      <SortableHeader
        label="Last Active"
        field="startTime"
        sortBy={sortProps.sortBy}
        sortOrder={sortProps.sortOrder}
        onSortChange={sortProps.onSortChange}
      />
    ),
    accessorKey: "last_active",
    size: 200,
    cell: (info) => <DateCell value={String(info.getValue() ?? "")} />,
  },
  {
    header: "Composition",
    id: "composition",
    size: 150,
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
      return (
        <Tooltip title={details.join(" • ")}>
          <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-700 border border-blue-200 rounded-full text-[11px] font-medium whitespace-nowrap">
            <SparkleIcon />
            <span>{requestCount}</span>
            {agentCount > 0 ? <AgentIcon size={10} /> : null}
            {mcpCount > 0 ? <WrenchIcon /> : null}
          </span>
        </Tooltip>
      );
    },
  },
  {
    header: "Status Summary",
    id: "status_summary",
    size: 130,
    cell: ({ row }) => {
      const { failure_count: failureCount, request_count: requestCount } = row.original;
      if (failureCount === 0) return <StatusBadge tone="success" label="Success" />;
      if (failureCount === requestCount) return <StatusBadge tone="error" label="Failure" />;
      return <StatusBadge tone="warning" label={`${failureCount} failed`} />;
    },
  },
  {
    header: "Session ID",
    id: "session_id",
    size: 140,
    cell: ({ row }) => {
      const value = row.original.session_id ?? row.original.request_id;
      return <IdCell value={value} variant={row.original.session_id ? undefined : "plain"} />;
    },
  },
  {
    header: () => (
      <SortableHeader
        label="Session Cost"
        field="spend"
        sortBy={sortProps.sortBy}
        sortOrder={sortProps.sortOrder}
        onSortChange={sortProps.onSortChange}
      />
    ),
    accessorKey: "session_spend",
    size: 125,
    meta: { numeric: true },
    cell: (info) => <MoneyCell value={Number(info.getValue())} decimals={6} />,
  },
  {
    header: () => (
      <SortableHeader
        label="Duration (s)"
        field="request_duration_ms"
        sortBy={sortProps.sortBy}
        sortOrder={sortProps.sortOrder}
        onSortChange={sortProps.onSortChange}
      />
    ),
    accessorKey: "session_duration_ms",
    size: 120,
    meta: { numeric: true },
    cell: (info) => {
      const milliseconds = Number(info.getValue());
      return Number.isFinite(milliseconds) ? <span>{(milliseconds / 1000).toFixed(2)}</span> : <span>-</span>;
    },
  },
  {
    header: "Models",
    id: "models",
    size: 190,
    cell: ({ row }) => <MultiValueCell values={row.original.models} fallback={row.original.primary_model ?? "-"} />,
  },
  {
    header: () => (
      <SortableHeader
        label="Session Tokens"
        field="total_tokens"
        sortBy={sortProps.sortBy}
        sortOrder={sortProps.sortOrder}
        onSortChange={sortProps.onSortChange}
      />
    ),
    accessorKey: "session_total_tokens",
    size: 165,
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
    header: "Prompt Cache",
    id: "prompt_cache",
    size: 150,
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
    header: "Team",
    id: "team",
    size: 150,
    cell: ({ row }) => <MultiValueCell values={row.original.team_names ?? row.original.team_ids} />,
  },
  {
    header: "Key Alias",
    id: "key_alias",
    size: 150,
    cell: ({ row }) => <MultiValueCell values={row.original.key_aliases} />,
  },
  {
    header: "Internal Users",
    id: "users",
    size: 150,
    cell: ({ row }) => <MultiValueCell values={row.original.users} />,
  },
];
