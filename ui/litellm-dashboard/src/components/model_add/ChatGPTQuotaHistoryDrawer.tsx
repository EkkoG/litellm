import {
  chatgptCredentialQuotaHistoryCall,
  type ChatGPTQuotaHistoryDays,
  type ChatGPTSubscriptionTier,
} from "@/components/networking";
import { LineChart } from "@/components/shared/charts";
import { useQuery } from "@tanstack/react-query";
import { Drawer, Empty, Segmented, Select, Spin, Typography } from "antd";
import { useMemo, useState } from "react";
import { chatgptTierLabel, formatChatGPTQuotaPercentLabel } from "./chatgptQuotaDisplay";

interface ChatGPTQuotaHistoryDrawerProps {
  open: boolean;
  onClose: () => void;
  accessToken: string;
  credentialName: string;
  planLabel: string;
  currentTiers: ChatGPTSubscriptionTier[];
  timezone?: string;
}

interface HistoryChartDatum extends Record<string, unknown> {
  date: string;
  "Start remaining": number;
}

const HISTORY_DAY_OPTIONS: ChatGPTQuotaHistoryDays[] = [7, 30, 90];
const formatChange = (change: number | null): string => {
  if (change === null) {
    return "-";
  }
  const prefix = change > 0 ? "+" : "";
  return `${prefix}${formatChatGPTQuotaPercentLabel(change)}`;
};

const isHistoryDays = (value: string | number): value is ChatGPTQuotaHistoryDays =>
  value === 7 || value === 30 || value === 90;

export function ChatGPTQuotaHistoryDrawer({
  open,
  onClose,
  accessToken,
  credentialName,
  planLabel,
  currentTiers,
  timezone,
}: ChatGPTQuotaHistoryDrawerProps) {
  const [days, setDays] = useState<ChatGPTQuotaHistoryDays>(30);
  const [requestedTierName, setRequestedTierName] = useState("");
  const historyQuery = useQuery({
    queryKey: ["chatgpt-credential-quota-history", credentialName, days],
    queryFn: async () => chatgptCredentialQuotaHistoryCall(accessToken, credentialName, days),
    enabled: open,
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });
  const snapshots = historyQuery.data?.snapshots ?? [];
  const tierNames = useMemo(
    () =>
      Array.from(
        new Set([
          ...currentTiers.map((tier) => tier.name),
          ...snapshots.flatMap((snapshot) => snapshot.tiers.map((tier) => tier.name)),
        ]),
      ),
    [currentTiers, snapshots],
  );
  const selectedTierName = tierNames.includes(requestedTierName) ? requestedTierName : tierNames[0] ?? "";
  const chartData = useMemo<HistoryChartDatum[]>(
    () =>
      snapshots.flatMap((snapshot) => {
        const tier = snapshot.tiers.find((candidate) => candidate.name === selectedTierName);
        return tier ? [{ date: snapshot.date, "Start remaining": tier.remaining_percent }] : [];
      }),
    [selectedTierName, snapshots],
  );
  const dailyStart =
    chartData.find((snapshot) => snapshot.date === historyQuery.data?.current_date)?.["Start remaining"] ?? null;
  const currentRemaining = currentTiers.find((tier) => tier.name === selectedTierName)?.remaining_percent ?? null;
  const change = currentRemaining !== null && dailyStart !== null ? currentRemaining - dailyStart : null;
  const displayedTimezone = historyQuery.data?.timezone ?? timezone;
  const hasHistoryData = selectedTierName.length > 0 && chartData.length > 0;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={680}
      title={
        <div>
          <Typography.Title level={4} className="!mb-0">
            {planLabel} quota history
          </Typography.Title>
          <Typography.Text type="secondary" className="text-xs">
            {credentialName} · {displayedTimezone ? `Daily boundary: ${displayedTimezone}` : "Loading daily boundary"}
          </Typography.Text>
        </div>
      }
    >
      <div className="flex min-h-full flex-col gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-gray-50 p-3">
          <Segmented
            aria-label="History range"
            value={days}
            options={HISTORY_DAY_OPTIONS.map((value) => ({ label: `${value} days`, value }))}
            onChange={(value) => {
              if (isHistoryDays(value)) {
                setDays(value);
              }
            }}
          />
          <Select
            aria-label="Quota tier"
            value={selectedTierName || undefined}
            placeholder="Quota tier"
            className="min-w-36"
            options={tierNames.map((name) => ({ label: chatgptTierLabel(name), value: name }))}
            onChange={setRequestedTierName}
          />
        </div>

        {historyQuery.isLoading && (
          <div className="flex min-h-72 items-center justify-center">
            <Spin />
          </div>
        )}
        {historyQuery.isError && <Empty description="Unable to load quota history" />}
        {!historyQuery.isLoading && !historyQuery.isError && hasHistoryData && (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {[
                ["Daily start", dailyStart === null ? "-" : formatChatGPTQuotaPercentLabel(dailyStart)],
                ["Current", currentRemaining === null ? "-" : formatChatGPTQuotaPercentLabel(currentRemaining)],
                ["Change", formatChange(change)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                  <Typography.Text type="secondary" className="text-xs uppercase tracking-wide">
                    {label}
                  </Typography.Text>
                  <div className="mt-1 text-2xl font-semibold text-gray-900">{value}</div>
                </div>
              ))}
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="mb-3 flex items-baseline justify-between gap-3">
                <Typography.Title level={5} className="!mb-0">
                  {chatgptTierLabel(selectedTierName)} daily start
                </Typography.Title>
                <Typography.Text type="secondary" className="text-xs">
                  Remaining quota
                </Typography.Text>
              </div>
              <LineChart
                data={chartData}
                index="date"
                categories={["Start remaining"]}
                colors={["blue"]}
                valueFormatter={formatChatGPTQuotaPercentLabel}
                showLegend={false}
                curveType="monotone"
                className="h-64"
              />
            </div>

            <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
              <div className="border-b border-gray-200 px-4 py-3">
                <Typography.Title level={5} className="!mb-0">
                  Daily values
                </Typography.Title>
              </div>
              <div className="max-h-72 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-4 py-2 font-medium">Date</th>
                      <th className="px-4 py-2 text-right font-medium">Start remaining</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {chartData.toReversed().map((row) => (
                      <tr key={row.date}>
                        <td className="px-4 py-2 text-gray-700">{row.date}</td>
                        <td className="px-4 py-2 text-right font-medium text-gray-900">
                          {formatChatGPTQuotaPercentLabel(row["Start remaining"])}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
        {!historyQuery.isLoading && !historyQuery.isError && !hasHistoryData && (
          <Empty description="No quota history recorded yet" />
        )}
      </div>
    </Drawer>
  );
}
