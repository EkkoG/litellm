import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../../tests/test-utils";
import { LogsTableToolbar } from "./LogsTableToolbar";

describe("LogsTableToolbar", () => {
  it("starts a JSON export from the request logs toolbar", async () => {
    const user = userEvent.setup();
    const onExport = vi.fn();

    renderWithProviders(
      <LogsTableToolbar
        searchTerm=""
        onSearchChange={vi.fn()}
        startTime="2026-07-11T00:00"
        onStartTimeChange={vi.fn()}
        endTime="2026-07-12T00:00"
        onEndTimeChange={vi.fn()}
        isCustomDate={false}
        onIsCustomDateChange={vi.fn()}
        selectedTimeInterval={{ value: 24, unit: "hours" }}
        onSelectedTimeIntervalChange={vi.fn()}
        isLiveTail={false}
        onIsLiveTailChange={vi.fn()}
        currentPage={1}
        onCurrentPageChange={vi.fn()}
        pageSize={50}
        isLoading={false}
        isButtonLoading={false}
        onRefetch={vi.fn()}
        onExport={onExport}
        isExporting={false}
        exportDisabled={false}
        filteredLogs={{ data: [], total: 1, page: 1, page_size: 50, total_pages: 1 }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Export JSON" }));

    expect(onExport).toHaveBeenCalledTimes(1);
  });
});
