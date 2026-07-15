import { BarChart } from "@/components/shared/charts";
import { IdentityCell, MoneyCell } from "@/components/shared/table_cells";
import type { TopUserData } from "@/components/UsagePage/types";
import { valueFormatterSpend } from "@/components/UsagePage/utils/value_formatters";
import { DataTable } from "@/components/view_logs/table";
import type { ColumnDef } from "@tanstack/react-table";
import { Segmented } from "antd";
import { useState } from "react";

interface TopUserViewProps {
  topUsers: TopUserData[];
  topUsersLimit: number;
  setTopUsersLimit: (limit: number) => void;
}

const getUserLabel = (user: TopUserData) => user.user_alias || user.user_email || user.user_id;

const getUserSubtitle = (user: TopUserData) => {
  if (user.user_alias && user.user_email) {
    return user.user_email;
  }
  if (getUserLabel(user) !== user.user_id) {
    return user.user_id;
  }
  return undefined;
};

const columns: ColumnDef<TopUserData>[] = [
  {
    header: "User",
    accessorKey: "user_id",
    cell: ({ row }) => <IdentityCell title={getUserLabel(row.original)} subtitle={getUserSubtitle(row.original)} />,
  },
  {
    header: "Spend (USD)",
    accessorKey: "spend",
    meta: { numeric: true },
    cell: ({ getValue }) => <MoneyCell value={getValue<number>()} decimals={2} />,
  },
];

const TopUserView = ({ topUsers, topUsersLimit, setTopUsersLimit }: TopUserViewProps) => {
  const [viewMode, setViewMode] = useState<"chart" | "table">("table");
  const chartData = topUsers.map((user) => {
    const label = getUserLabel(user);
    return {
      ...user,
      display_user: label.length > 18 ? `${label.slice(0, 18)}...` : label,
    };
  });

  return (
    <>
      <div className="mb-4 flex justify-between items-center">
        <Segmented
          options={[
            { label: "5", value: 5 },
            { label: "10", value: 10 },
            { label: "25", value: 25 },
            { label: "50", value: 50 },
          ]}
          value={topUsersLimit}
          onChange={(value) => setTopUsersLimit(value as number)}
        />
        <div className="flex space-x-2">
          <button
            type="button"
            onClick={() => setViewMode("table")}
            className={`px-3 py-1 text-sm rounded-md ${viewMode === "table" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-700"}`}
          >
            Table View
          </button>
          <button
            type="button"
            onClick={() => setViewMode("chart")}
            className={`px-3 py-1 text-sm rounded-md ${viewMode === "chart" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-700"}`}
          >
            Chart View
          </button>
        </div>
      </div>

      {viewMode === "chart" ? (
        <div className="relative max-h-[600px] overflow-y-auto">
          <BarChart
            className="mt-4"
            style={{ height: Math.min(chartData.length, topUsersLimit) * 52 }}
            data={chartData}
            index="display_user"
            categories={["spend"]}
            colors={["cyan"]}
            yAxisWidth={180}
            tickGap={5}
            layout="vertical"
            showLegend={false}
            valueFormatter={valueFormatterSpend}
            customTooltip={({ payload, active }) => {
              if (!active || !payload?.[0]) return null;
              const user = payload[0].payload as TopUserData;
              return (
                <div className="bg-white p-4 shadow-lg rounded-lg border">
                  <p className="font-bold">{getUserLabel(user)}</p>
                  {user.user_alias && user.user_email && <p className="text-gray-600">{user.user_email}</p>}
                  <p className="font-mono text-xs text-gray-500">{user.user_id}</p>
                  <p className="text-cyan-500">Spend: {valueFormatterSpend(user.spend)}</p>
                </div>
              );
            }}
          />
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden max-h-[600px] overflow-y-auto">
          <DataTable columns={columns} data={topUsers} isLoading={false} />
        </div>
      )}
    </>
  );
};

export default TopUserView;
