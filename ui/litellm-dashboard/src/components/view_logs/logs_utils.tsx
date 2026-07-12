import moment from "moment";

const getRecord = (value: unknown): Record<string, unknown> | undefined =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;

const getTokenCount = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;

export const getPromptCacheReadTokens = (metadata: Record<string, unknown> | undefined): number | undefined => {
  const usage = getRecord(metadata?.additional_usage_values);
  const cacheReadTokens = getTokenCount(usage?.cache_read_input_tokens);
  if (cacheReadTokens !== undefined) return cacheReadTokens;

  const promptTokenDetails = getRecord(usage?.prompt_tokens_details);
  return getTokenCount(promptTokenDetails?.cached_tokens);
};

// Add this function to format the time range display
export const getTimeRangeDisplay = (isCustomDate: boolean, startTime: string, endTime: string) => {
  if (isCustomDate) {
    return `${moment(startTime).format("MMM D, h:mm A")} - ${moment(endTime).format("MMM D, h:mm A")}`;
  }

  const now = moment();
  const start = moment(startTime);
  const diffMinutes = now.diff(start, "minutes");

  // Use exact ranges to prevent drift
  if (diffMinutes >= 0 && diffMinutes < 2) return "Last 1 Minute";
  if (diffMinutes >= 2 && diffMinutes < 16) return "Last 15 Minutes";
  if (diffMinutes >= 16 && diffMinutes < 61) return "Last Hour";

  const diffHours = now.diff(start, "hours");
  if (diffHours >= 1 && diffHours < 5) return "Last 4 Hours";
  if (diffHours >= 5 && diffHours < 25) return "Last 24 Hours";
  if (diffHours >= 25 && diffHours < 169) return "Last 7 Days";
  return `${start.format("MMM D")} - ${now.format("MMM D")}`;
};
