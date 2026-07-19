import { formatNumberWithCommas } from "@/utils/dataUtils";
import type { ChartColor } from "@/components/shared/charts";
import type { SpendMetrics } from "@/components/UsagePage/types";

export interface TokenLineDimension {
  key: string;
  label: string;
  color: ChartColor;
  getValue: (metrics: Partial<SpendMetrics>) => number;
  isRate?: boolean;
}

export const TOKEN_LINE_DIMENSIONS: readonly TokenLineDimension[] = [
  {
    key: "metrics.prompt_tokens",
    label: "Prompt Tokens",
    color: "blue",
    getValue: (metrics) => metrics.prompt_tokens || 0,
  },
  {
    key: "metrics.completion_tokens",
    label: "Completion Tokens",
    color: "cyan",
    getValue: (metrics) => metrics.completion_tokens || 0,
  },
  {
    key: "metrics.total_tokens",
    label: "Total Tokens",
    color: "indigo",
    getValue: (metrics) => metrics.total_tokens || 0,
  },
  {
    key: "metrics.api_requests",
    label: "Requests",
    color: "amber",
    getValue: (metrics) => metrics.api_requests || 0,
  },
  {
    key: "metrics.successful_requests",
    label: "Successful Requests",
    color: "lime",
    getValue: (metrics) => metrics.successful_requests || 0,
  },
  {
    key: "metrics.failed_requests",
    label: "Failed Requests",
    color: "red",
    getValue: (metrics) => metrics.failed_requests || 0,
  },
  {
    key: "metrics.cache_read_input_tokens",
    label: "Cached Tokens",
    color: "green",
    getValue: (metrics) => metrics.cache_read_input_tokens || 0,
  },
  {
    key: "metrics.cache_creation_input_tokens",
    label: "Cache Write Tokens",
    color: "purple",
    getValue: (metrics) => metrics.cache_creation_input_tokens || 0,
  },
  {
    key: "metrics.prompt_cache_hit_rate",
    label: "Cache Hit Rate",
    color: "emerald",
    isRate: true,
    getValue: (metrics) => {
      const promptTokens = metrics.prompt_tokens || 0;
      if (promptTokens <= 0) return 0;
      return ((metrics.cache_read_input_tokens || 0) / promptTokens) * 100;
    },
  },
];

export const TOKEN_COUNT_DIMENSIONS = TOKEN_LINE_DIMENSIONS.filter((dimension) => !dimension.isRate);

export const RATE_DIMENSIONS = TOKEN_LINE_DIMENSIONS.filter((dimension) => dimension.isRate);

export const formatTokenLineValue = (dimension: TokenLineDimension, value: number): string =>
  dimension.isRate ? `${value.toFixed(1)}%` : formatNumberWithCommas(value, 0);
