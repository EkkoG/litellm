"use client";

import * as React from "react";
import { Bar, CartesianGrid, Cell, ComposedChart, Line, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { cn } from "@/lib/cva.config";
import { ValueTooltip, type ChartTooltipComponent } from "./chart_tooltip";
import { categoryFills, type ChartColor } from "./colors";

export type BarChartProps<TDatum extends Record<string, unknown>> = {
  data: readonly TDatum[];
  index: string;
  categories: readonly string[];
  colors?: readonly ChartColor[];
  colorByDatum?: boolean;
  maxBarSize?: number;
  valueFormatter?: (value: number) => string;
  stack?: boolean;
  layout?: "horizontal" | "vertical";
  yAxisWidth?: number;
  tickGap?: number;
  showLegend?: boolean;
  showXAxis?: boolean;
  showGridLines?: boolean;
  showTooltip?: boolean;
  customTooltip?: ChartTooltipComponent;
  onValueChange?: (item: TDatum & { categoryClicked: string }) => void;
  lineCategories?: readonly string[];
  lineColors?: readonly ChartColor[];
  lineValueFormatter?: (value: number) => string;
  lineYAxisWidth?: number;
  className?: string;
  style?: React.CSSProperties;
};

export function BarChart<TDatum extends Record<string, unknown>>({
  data,
  index,
  categories,
  colors,
  colorByDatum = false,
  maxBarSize,
  valueFormatter,
  stack = false,
  layout = "horizontal",
  yAxisWidth = 56,
  tickGap = 5,
  showLegend = true,
  showXAxis = true,
  showGridLines = true,
  showTooltip = true,
  customTooltip,
  onValueChange,
  lineCategories = [],
  lineColors,
  lineValueFormatter,
  lineYAxisWidth = 64,
  className,
  style,
}: BarChartProps<TDatum>) {
  if (data.length === 0) {
    return (
      <div
        className={cn("flex h-80 w-full items-center justify-center rounded-lg border border-dashed", className)}
        style={style}
      >
        <p className="text-sm text-muted-foreground">No data</p>
      </div>
    );
  }

  const fills = categoryFills(colorByDatum ? data.length : categories.length, colors);
  const lineFills = categoryFills(lineCategories.length, lineColors);
  const allSeries = [...categories, ...lineCategories];
  const config: ChartConfig = Object.fromEntries(allSeries.map((category) => [category, { label: category }]));
  const vertical = layout === "vertical";
  const TooltipContent = customTooltip ?? ValueTooltip;

  return (
    <ChartContainer config={config} className={cn("aspect-auto h-80 w-full", className)} style={style}>
      <ComposedChart data={[...data]} layout={layout}>
        {showGridLines && <CartesianGrid horizontal={!vertical} vertical={vertical} />}
        {vertical ? (
          <XAxis
            type="number"
            hide={!showXAxis}
            tickLine={false}
            axisLine={false}
            minTickGap={tickGap}
            tickFormatter={valueFormatter}
          />
        ) : (
          <XAxis
            dataKey={index}
            hide={!showXAxis}
            tickLine={false}
            axisLine={false}
            minTickGap={tickGap}
            interval="equidistantPreserveStart"
          />
        )}
        {vertical ? (
          <YAxis type="category" dataKey={index} width={yAxisWidth} tickLine={false} axisLine={false} interval={0} />
        ) : (
          <YAxis yAxisId="left" width={yAxisWidth} tickLine={false} axisLine={false} tickFormatter={valueFormatter} />
        )}
        {!vertical && lineCategories.length > 0 && (
          <YAxis
            yAxisId="right"
            orientation="right"
            width={lineYAxisWidth}
            tickLine={false}
            axisLine={false}
            tickFormatter={lineValueFormatter ?? valueFormatter}
          />
        )}
        {showTooltip && (
          <ChartTooltip
            content={({ active, payload, label }) => (
              <TooltipContent
                active={active}
                payload={payload}
                label={label}
                {...(customTooltip ? {} : { valueFormatter })}
              />
            )}
          />
        )}
        {showLegend && (
          <ChartLegend
            verticalAlign="top"
            content={<ChartLegendContent className="justify-end text-muted-foreground" />}
          />
        )}
        {categories.map((category, i) => (
          <Bar
            key={category}
            yAxisId={vertical ? undefined : "left"}
            dataKey={category}
            fill={fills[i]}
            stackId={stack ? "stack" : undefined}
            isAnimationActive={false}
            maxBarSize={maxBarSize}
            onClick={
              onValueChange
                ? (item: { payload?: TDatum }) => {
                    if (item.payload) onValueChange({ ...item.payload, categoryClicked: category });
                  }
                : undefined
            }
          >
            {colorByDatum && data.map((_, dataIndex) => <Cell key={dataIndex} fill={fills[dataIndex]} />)}
          </Bar>
        ))}
        {lineCategories.map((category, i) => (
          <Line
            key={category}
            yAxisId={vertical ? undefined : "right"}
            type="linear"
            dataKey={(datum: unknown) =>
              category.split(".").reduce<unknown>((acc, part) => (acc as Record<string, number> | null)?.[part], datum)
            }
            stroke={lineFills[i]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        ))}
      </ComposedChart>
    </ChartContainer>
  );
}
