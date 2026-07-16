const CHATGPT_TIER_LABELS: Record<string, string> = {
  five_hour: "5h",
  seven_day: "7d",
  "30_day": "30d",
};

export const chatgptTierLabel = (name: string): string => CHATGPT_TIER_LABELS[name] || name.replace(/_/g, " ");

export const formatChatGPTQuotaPercent = (remainingPercent: number): string =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(remainingPercent);

export const formatChatGPTQuotaPercentLabel = (remainingPercent: number): string =>
  `${formatChatGPTQuotaPercent(remainingPercent)}%`;
