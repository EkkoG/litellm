export interface LogDetailsPayload {
  messages?: unknown;
  response?: unknown;
  proxy_server_request?: unknown;
}

export interface LogExportSource {
  request_id: string;
  api_key: string;
  team_id: string;
  model: string;
  model_id: string;
  api_base?: string;
  call_type: string;
  spend: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  startTime: string;
  endTime: string;
  user?: string;
  end_user?: string;
  custom_llm_provider?: string;
  metadata?: unknown;
  cache_hit: string;
  cache_key?: string;
  request_tags?: unknown;
  requester_ip_address?: string;
  messages?: unknown;
  response?: unknown;
  proxy_server_request?: unknown;
  session_id?: string;
  status?: string;
  completionStartTime?: string;
  request_duration_ms?: number;
}

export interface LogExportFile {
  name: string;
  contents: string;
}

export type LogExportResult = { status: "success"; file: LogExportFile } | { status: "error"; message: string };

type LoadLogDetails = (requestId: string) => Promise<LogDetailsPayload | null>;

const DETAIL_BATCH_SIZE = 5;
const EMPTY_JSON_STRINGS = new Set(["", "{}", "[]", "null"]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const hasJsonContent = (value: unknown): boolean => {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return !EMPTY_JSON_STRINGS.has(value.trim());
  if (Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return true;
};

const parseStoredJson = (value: unknown): unknown => {
  if (typeof value !== "string") return value;

  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
};

const preferPayload = (detailValue: unknown, rowValue: unknown): unknown =>
  hasJsonContent(detailValue) ? detailValue : rowValue;

export const parseLogDetailsPayload = (value: unknown): LogDetailsPayload | null => {
  if (!isRecord(value)) return null;

  return {
    messages: value.messages,
    response: value.response,
    proxy_server_request: value.proxy_server_request,
  };
};

const loadDetailsInBatches = async (
  logs: readonly LogExportSource[],
  loadDetails: LoadLogDetails,
): Promise<(LogDetailsPayload | null)[]> => {
  if (logs.length === 0) return [];

  const batch = logs.slice(0, DETAIL_BATCH_SIZE);
  const currentDetails = await Promise.all(batch.map((log) => loadDetails(log.request_id)));
  const remainingDetails = await loadDetailsInBatches(logs.slice(DETAIL_BATCH_SIZE), loadDetails);

  return [...currentDetails, ...remainingDetails];
};

export const createLogExport = async (
  logs: readonly LogExportSource[],
  loadDetails: LoadLogDetails,
  exportedAt: Date = new Date(),
): Promise<LogExportResult> => {
  try {
    const details = await loadDetailsInBatches(logs, loadDetails);
    const exportedLogs = logs.map((log, index) => {
      const messages = preferPayload(details[index]?.messages, log.messages);
      const proxyServerRequest = preferPayload(details[index]?.proxy_server_request, log.proxy_server_request);
      const response = preferPayload(details[index]?.response, log.response);

      if ((!hasJsonContent(messages) && !hasJsonContent(proxyServerRequest)) || !hasJsonContent(response)) {
        return { status: "error" as const, requestId: log.request_id };
      }

      return {
        status: "success" as const,
        log: {
          request_id: log.request_id,
          session_id: log.session_id,
          call_type: log.call_type,
          status: log.status,
          model: log.model,
          model_id: log.model_id,
          custom_llm_provider: log.custom_llm_provider,
          api_base: log.api_base,
          api_key: log.api_key,
          team_id: log.team_id,
          user: log.user,
          end_user: log.end_user,
          startTime: log.startTime,
          endTime: log.endTime,
          completionStartTime: log.completionStartTime,
          request_duration_ms: log.request_duration_ms,
          spend: log.spend,
          total_tokens: log.total_tokens,
          prompt_tokens: log.prompt_tokens,
          completion_tokens: log.completion_tokens,
          cache_hit: log.cache_hit,
          cache_key: log.cache_key,
          requester_ip_address: log.requester_ip_address,
          request_tags: log.request_tags,
          metadata: log.metadata,
          messages: parseStoredJson(messages),
          proxy_server_request: parseStoredJson(proxyServerRequest),
          response: parseStoredJson(response),
        },
      };
    });
    const incompleteLog = exportedLogs.find((result) => result.status === "error");

    if (incompleteLog?.status === "error") {
      return {
        status: "error",
        message: `Request or response JSON is unavailable for log ${incompleteLog.requestId}`,
      };
    }

    const timestamp = exportedAt.toISOString();
    const name = `request_logs_${timestamp.replace(/[:.]/g, "-")}.json`;
    const contents = JSON.stringify(
      {
        exported_at: timestamp,
        log_count: exportedLogs.length,
        logs: exportedLogs.flatMap((result) => (result.status === "success" ? [result.log] : [])),
      },
      null,
      2,
    );

    return { status: "success", file: { name, contents } };
  } catch (error) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "Failed to load request and response details",
    };
  }
};

export const downloadLogExport = (file: LogExportFile): void => {
  const url = URL.createObjectURL(new Blob([file.contents], { type: "application/json" }));
  const anchor = document.createElement("a");

  anchor.href = url;
  anchor.download = file.name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
};
