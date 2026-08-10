import type { LogEntry } from "./columns";
import type { SessionLogEntry } from "./SessionLogsTableColumns";

export interface LogDetailsPayload {
  messages?: unknown;
  response?: unknown;
  proxy_server_request?: unknown;
}

export interface LogExportFile {
  name: string;
  contents: string;
}

export type LogExportResult = { status: "success"; file: LogExportFile } | { status: "error"; message: string };

type LoadLogDetails = (requestId: string) => Promise<LogDetailsPayload | null>;

const DETAIL_BATCH_SIZE = 5;
const EMPTY_JSON_STRINGS = new Set(["", "{}", "[]", "null"]);
const UNAVAILABLE_JSON_STRINGS = new Set(["", "null"]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const hasJsonContent = (value: unknown): boolean => {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return !EMPTY_JSON_STRINGS.has(value.trim());
  if (Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return true;
};

const hasRequestJson = (value: unknown): boolean => {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return !UNAVAILABLE_JSON_STRINGS.has(value.trim());
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

const preferRequestPayload = (detailValue: unknown, rowValue: unknown): unknown =>
  hasRequestJson(detailValue) ? detailValue : rowValue;

const preferResponsePayload = (detailValue: unknown, rowValue: unknown): unknown =>
  hasJsonContent(detailValue) ? detailValue : rowValue;

const isFailedLog = (log: LogEntry): boolean => {
  const metadataStatus = isRecord(log.metadata) ? log.metadata.status : undefined;
  const status = typeof log.status === "string" ? log.status : metadataStatus;
  return typeof status === "string" && status.toLowerCase() === "failure";
};

export const parseLogDetailsPayload = (value: unknown): LogDetailsPayload | null => {
  if (!isRecord(value)) return null;
  return {
    messages: value.messages,
    response: value.response,
    proxy_server_request: value.proxy_server_request,
  };
};

const loadDetailsInBatches = async (
  logs: readonly LogEntry[],
  loadDetails: LoadLogDetails,
): Promise<(LogDetailsPayload | null)[]> => {
  if (logs.length === 0) return [];

  const batch = logs.slice(0, DETAIL_BATCH_SIZE);
  const currentDetails = await Promise.all(batch.map((log) => loadDetails(log.request_id)));
  const remainingDetails = await loadDetailsInBatches(logs.slice(DETAIL_BATCH_SIZE), loadDetails);
  return [...currentDetails, ...remainingDetails];
};

export const createLogExport = async (
  logs: readonly LogEntry[],
  loadDetails: LoadLogDetails,
  exportedAt: Date = new Date(),
): Promise<LogExportResult> => {
  try {
    const details = await loadDetailsInBatches(logs, loadDetails);
    const exportedLogs = logs.map((log, index) => {
      const messages = preferRequestPayload(details[index]?.messages, log.messages);
      const proxyServerRequest = preferRequestPayload(details[index]?.proxy_server_request, log.proxy_server_request);
      const response = preferResponsePayload(details[index]?.response, log.response);
      const hasRequest = hasRequestJson(messages) || hasRequestJson(proxyServerRequest);
      const hasResponse = hasJsonContent(response);

      if (!hasRequest || (!hasResponse && !isFailedLog(log))) {
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
          user_alias: log.user_alias,
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
          response: hasResponse ? parseStoredJson(response) : null,
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
    return {
      status: "success",
      file: {
        name: `request_logs_${timestamp.replace(/[:.]/g, "-")}.json`,
        contents: JSON.stringify(
          {
            exported_at: timestamp,
            log_count: exportedLogs.length,
            logs: exportedLogs.flatMap((result) => (result.status === "success" ? [result.log] : [])),
          },
          null,
          2,
        ),
      },
    };
  } catch (error) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "Failed to load request and response details",
    };
  }
};

export const createSessionLogExport = (
  sessions: readonly SessionLogEntry[],
  exportedAt: Date = new Date(),
): LogExportResult => {
  const timestamp = exportedAt.toISOString();
  return {
    status: "success",
    file: {
      name: `session_logs_${timestamp.replace(/[:.]/g, "-")}.json`,
      contents: JSON.stringify(
        {
          exported_at: timestamp,
          session_count: sessions.length,
          sessions,
        },
        null,
        2,
      ),
    },
  };
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
