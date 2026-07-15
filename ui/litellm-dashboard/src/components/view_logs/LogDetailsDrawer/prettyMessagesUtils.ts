/**
 * Utility functions for parsing and formatting messages for pretty view
 */

import { ParsedMessage, ParsedMessages, RoleStyle, ToolCall } from "./prettyMessagesTypes";

type UnknownRecord = Record<string, unknown>;

/**
 * Role color styles for message cards - minimal, professional design
 * Color only used for labels and left border accent
 */
export const ROLE_STYLES: Record<string, RoleStyle> = {
  system: {
    background: "transparent",
    borderColor: "#8c8c8c",
    label: "SYSTEM",
    labelColor: "#8c8c8c",
  },
  user: {
    background: "transparent",
    borderColor: "#1677ff",
    label: "USER",
    labelColor: "#1677ff",
  },
  assistant: {
    background: "transparent",
    borderColor: "#52c41a",
    label: "ASSISTANT",
    labelColor: "#52c41a",
  },
  tool: {
    background: "transparent",
    borderColor: "#fa8c16",
    label: "TOOL RESULT",
    labelColor: "#fa8c16",
  },
};

/**
 * Parse request messages and response message from log data
 */
export const parseMessages = (request: unknown, response: unknown): ParsedMessages => {
  // Parse request messages. `request` is either the raw request body
  // ({ messages: [...] }) or, when prompts come from cold storage, the bare
  // messages array itself.
  const requestMessages = getRequestMessageList(request).map(
    (msg): ParsedMessage => ({
      role: normalizeRole(msg.role),
      content: parseMessageContent(msg.content),
      toolCallId: typeof msg.tool_call_id === "string" ? msg.tool_call_id : undefined,
    }),
  );

  const responseRecord = isRecord(response) ? response : null;
  const responseMsg = getChatCompletionMessage(responseRecord);

  if (responseMsg) {
    return {
      requestMessages,
      responseMessage: {
        role: normalizeRole(responseMsg.role, "assistant"),
        content: parseMessageContent(responseMsg.content),
        toolCalls: parseToolCalls(responseMsg.tool_calls),
      },
    };
  }

  const responseOutput = getRecordArray(responseRecord?.output);
  const outputMessages = responseOutput.filter((item) => item.type === "message");
  const outputContent = outputMessages
    .map((item) => parseMessageContent(item.content))
    .filter((content) => content.length > 0)
    .join("\n");
  const outputToolCalls = parseResponseToolCalls(responseOutput);
  const responseOutputText = typeof responseRecord?.output_text === "string" ? responseRecord.output_text : "";
  const responseMessage =
    outputMessages.length > 0 || outputToolCalls.length > 0 || responseOutputText.length > 0
      ? {
          role: "assistant" as const,
          content: outputContent || responseOutputText,
          toolCalls: outputToolCalls.length > 0 ? outputToolCalls : undefined,
        }
      : null;

  return { requestMessages, responseMessage };
};

const isRecord = (value: unknown): value is UnknownRecord => {
  return typeof value === "object" && value !== null && !Array.isArray(value);
};

const getRecordArray = (value: unknown): UnknownRecord[] => {
  return Array.isArray(value) ? value.filter(isRecord) : [];
};

const getRequestMessageList = (request: unknown): UnknownRecord[] => {
  if (Array.isArray(request)) return request.filter(isRecord);
  if (!isRecord(request)) return [];

  const messages = getRecordArray(request.messages);
  if (messages.length > 0) return messages;

  if (typeof request.input === "string") return [{ role: "user", content: request.input }];

  return getRecordArray(request.input).filter((item) => item.type === "message" || typeof item.role === "string");
};

const getChatCompletionMessage = (response: UnknownRecord | null): UnknownRecord | null => {
  const firstChoice = getRecordArray(response?.choices)[0];
  return isRecord(firstChoice?.message) ? firstChoice.message : null;
};

const normalizeRole = (role: unknown, fallback: ParsedMessage["role"] = "user"): ParsedMessage["role"] => {
  if (role === "developer" || role === "system") return "system";
  if (role === "assistant" || role === "tool" || role === "user") return role;
  return fallback;
};

const parseResponseToolCalls = (output: UnknownRecord[]): ToolCall[] => {
  return output
    .filter((item) => item.type === "function_call" || item.type === "custom_tool_call")
    .map((item) => ({
      id: getString(item.call_id) || getString(item.id),
      name: getString(item.name) || "unknown",
      arguments: parseToolArguments(item.arguments ?? item.input),
    }));
};

const getString = (value: unknown): string => {
  return typeof value === "string" ? value : "";
};

/**
 * Parse message content - handle strings and content arrays (for vision, etc.)
 */
const parseMessageContent = (content: unknown): string => {
  if (typeof content === "string") {
    return content;
  }

  if (Array.isArray(content)) {
    // Handle content arrays (vision API format)
    return content
      .map((item) => {
        if (typeof item === "string") return item;
        if (!isRecord(item)) return JSON.stringify(item) ?? "";
        if (item.type === "text" || item.type === "input_text" || item.type === "output_text") {
          return getString(item.text);
        }
        if (item.type === "refusal") return getString(item.refusal);
        if (item.type === "image_url" || item.type === "input_image") return "[Image]";
        return JSON.stringify(item) ?? "";
      })
      .join("\n");
  }

  // Fallback to JSON string for complex content
  return JSON.stringify(content) ?? "";
};

/**
 * Parse tool calls from response message
 */
const parseToolCalls = (toolCalls: unknown): ToolCall[] | undefined => {
  const records = getRecordArray(toolCalls);
  if (records.length === 0) return undefined;

  return records.map((toolCall) => {
    const toolFunction = isRecord(toolCall.function) ? toolCall.function : null;
    return {
      id: getString(toolCall.id),
      name: getString(toolFunction?.name) || "unknown",
      arguments: parseToolArguments(toolFunction?.arguments),
    };
  });
};

/**
 * Parse tool arguments - handle both string and object formats
 */
const parseToolArguments = (args: unknown): Record<string, unknown> => {
  if (!args) return {};

  if (typeof args === "string") {
    try {
      const parsed: unknown = JSON.parse(args);
      return isRecord(parsed) ? parsed : { raw: parsed };
    } catch {
      return { raw: args };
    }
  }

  return isRecord(args) ? args : { raw: args };
};
