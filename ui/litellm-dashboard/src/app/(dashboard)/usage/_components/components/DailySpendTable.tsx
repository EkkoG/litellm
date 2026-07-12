import type { DailyData } from "@/components/UsagePage/types";
import { formatPromptCacheHitRate } from "@/components/UsagePage/utils/value_formatters";
import { formatNumberWithCommas } from "@/utils/dataUtils";

interface DailySpendTableProps {
  data: DailyData[];
}

export const DailySpendTable = ({ data }: DailySpendTableProps) => (
  <div className="mt-6 max-h-72 overflow-auto rounded-lg border border-gray-200">
    <table aria-label="Daily Spend" className="w-full min-w-[640px] text-sm">
      <thead className="sticky top-0 bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
        <tr>
          <th className="px-4 py-3">Date</th>
          <th className="px-4 py-3 text-right">Spend</th>
          <th className="px-4 py-3 text-right">Prompt Cache Hit Rate</th>
          <th className="px-4 py-3 text-right">Cached Tokens</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100">
        {data.map((day) => (
          <tr key={day.date} className="text-gray-700">
            <td className="px-4 py-3 font-medium text-gray-900">{day.date}</td>
            <td className="px-4 py-3 text-right">${formatNumberWithCommas(day.metrics.spend, 2)}</td>
            <td className="px-4 py-3 text-right font-medium text-green-600">
              {formatPromptCacheHitRate(day.metrics.prompt_tokens, day.metrics.cache_read_input_tokens)}
            </td>
            <td className="px-4 py-3 text-right">{day.metrics.cache_read_input_tokens.toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);
