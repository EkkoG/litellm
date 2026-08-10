"use client";

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import type { ColumnFiltersState, OnChangeFn, PaginationState, SortingState } from "@tanstack/react-table";
import moment from "moment";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AutoRouterModelGroupsProvider } from "@/components/shared/table_cells";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { internalUserRoles } from "../../utils/roles";
import type { KeyResponse } from "../key_team_helpers/key_list";
import NotificationsManager from "../molecules/notifications_manager";
import { keyInfoV1Call, uiSpendLogDetailsCall, uiSpendLogsCall } from "../networking";
import KeyInfoView from "../templates/key_info_view";
import type { LogEntry } from "./columns";
import {
  DEFAULT_LOGS_SORTING,
  formatLogsWindow,
  getLogsWindowEndBound,
  LOG_FILTER_IDS,
  type LogsPageRow,
  type LogsViewMode,
  type PaginatedResponse,
  useLogFilterLogic,
} from "./log_filter_logic";
import { createLogExport, createSessionLogExport, downloadLogExport, parseLogDetailsPayload } from "./log_export";
import { useLogDetailRouting } from "./logDetailRouting";
import { LogDetailsDrawer } from "./LogDetailsDrawer";
import { LiveTailBanner, LogsTableToolbar } from "./LogsTableToolbar";
import { RequestLogsTable } from "./RequestLogsTable";
import { SessionLogsTable } from "./SessionLogsTable";
import type { SessionLogEntry } from "./SessionLogsTableColumns";

const PAGE_SIZE = 50;
const DEFAULT_INTERVAL = { value: 24, unit: "hours" };

interface RequestLogsPanelProps {
  accessToken: string;
  token: string;
  userRole: string;
  userID: string;
  isActive: boolean;
}

const isSessionLogEntry = (row: LogsPageRow): row is SessionLogEntry => "row_type" in row;

export default function RequestLogsPanel({ accessToken, token, userRole, userID, isActive }: RequestLogsPanelProps) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: PAGE_SIZE });
  const [sorting, setSorting] = useState<SortingState>(DEFAULT_LOGS_SORTING);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const [startTime, setStartTime] = useState<string>(moment().subtract(24, "hours").format("YYYY-MM-DDTHH:mm"));
  const [endTime, setEndTime] = useState<string>(moment().format("YYYY-MM-DDTHH:mm"));
  const [isCustomDate, setIsCustomDate] = useState(false);
  const [selectedTimeInterval, setSelectedTimeInterval] = useState<{ value: number; unit: string }>(DEFAULT_INTERVAL);

  const [selectedKeyIdInfoView, setSelectedKeyIdInfoView] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [viewMode, setViewMode] = useState<LogsViewMode>(() => {
    if (typeof window === "undefined") return "session";
    return new URLSearchParams(window.location.search).get("view") === "request" ? "request" : "session";
  });

  const {
    logId: urlLogId,
    sessionId: urlSessionId,
    openLog,
    openSession,
    selectLog,
    close: closeUrlLog,
  } = useLogDetailRouting();

  const [isLiveTail, setIsLiveTail] = useState<boolean>(() => {
    const storedValue = sessionStorage.getItem("isLiveTail");
    return storedValue !== null ? JSON.parse(storedValue) : true;
  });

  useEffect(() => {
    sessionStorage.setItem("isLiveTail", JSON.stringify(isLiveTail));
  }, [isLiveTail]);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", viewMode);
    window.history.replaceState(window.history.state, "", url);
  }, [viewMode]);

  const filterByCurrentUser = internalUserRoles.includes(userRole);

  const { logsQuery, filteredLogs, allTeams } = useLogFilterLogic<LogsPageRow>({
    accessToken,
    token,
    userRole,
    userID,
    columnFilters,
    filterByCurrentUser,
    activeTab: isActive ? "request logs" : "inactive",
    isLiveTail,
    startTime,
    endTime,
    pagination,
    isCustomDate,
    sorting,
    viewMode,
  });

  // Follow the table's own last fetch so a live-tail refresh carries the filter
  // window with it; before the first fetch, fall back to the stored end time.
  const windowEndBound = getLogsWindowEndBound(logsQuery.dataUpdatedAt || Date.parse(endTime));
  const logsWindow = useMemo(
    () => formatLogsWindow(startTime, endTime, isCustomDate, windowEndBound),
    [startTime, endTime, isCustomDate, windowEndBound],
  );

  const keyInfoQueryOptions: UseQueryOptions<KeyResponse | null> = {
    queryKey: ["requestLogsKeyInfo", selectedKeyIdInfoView, accessToken],
    queryFn: async () => {
      if (selectedKeyIdInfoView === null) return null;
      const keyData = await keyInfoV1Call(accessToken, selectedKeyIdInfoView);
      return {
        ...keyData["info"],
        token: selectedKeyIdInfoView,
        api_key: selectedKeyIdInfoView,
      };
    },
    enabled: selectedKeyIdInfoView !== null,
  };

  const { data: selectedKeyInfo } = useQuery(keyInfoQueryOptions);

  const urlLogQueryOptions: UseQueryOptions<LogEntry | null> = {
    queryKey: ["logs", "byId", urlLogId, accessToken],
    queryFn: async () => {
      if (urlLogId === null) return null;
      const window = formatLogsWindow(startTime, endTime, isCustomDate);
      const response: PaginatedResponse<LogEntry> = await uiSpendLogsCall({
        accessToken,
        start_date: window.start_date,
        end_date: window.end_date,
        page: 1,
        page_size: 1,
        params: { request_id: urlLogId, view: "request" },
      });
      return response.data.find((log) => log.request_id === urlLogId) ?? null;
    },
    enabled: urlLogId !== null && selectedLog?.request_id !== urlLogId,
    staleTime: Infinity,
  };

  const { data: urlLog } = useQuery(urlLogQueryOptions);

  const requestRows = useMemo<LogEntry[]>(
    () => (viewMode === "request" ? filteredLogs.data.filter((row): row is LogEntry => !isSessionLogEntry(row)) : []),
    [filteredLogs.data, viewMode],
  );
  const sessionRows = useMemo<SessionLogEntry[]>(
    () => (viewMode === "session" ? filteredLogs.data.filter(isSessionLogEntry) : []),
    [filteredLogs.data, viewMode],
  );

  const displayLog = useMemo<LogEntry | null>(() => {
    if (urlLogId === null) return null;
    if (selectedLog?.request_id === urlLogId) return selectedLog;
    return requestRows.find((log) => log.request_id === urlLogId) ?? urlLog ?? null;
  }, [urlLogId, selectedLog, requestRows, urlLog]);

  const displaySessionId = useMemo<string | null>(() => {
    if (urlSessionId !== null) return urlSessionId;
    if (displayLog?.session_id !== undefined && (displayLog.session_total_count || 1) > 1) {
      return displayLog.session_id;
    }
    return null;
  }, [urlSessionId, displayLog]);

  const isDrawerOpen = displayLog !== null || displaySessionId !== null;

  const searchTerm = useMemo(() => {
    const searchFilterId = viewMode === "session" ? LOG_FILTER_IDS.SESSION_ID : LOG_FILTER_IDS.REQUEST_ID;
    const entry = columnFilters.find((filter) => filter.id === searchFilterId);
    return typeof entry?.value === "string" ? entry.value : "";
  }, [columnFilters, viewMode]);

  const handleSearchChange = useCallback(
    (value: string) => {
      const searchFilterId = viewMode === "session" ? LOG_FILTER_IDS.SESSION_ID : LOG_FILTER_IDS.REQUEST_ID;
      setColumnFilters((previous) => {
        const others = previous.filter((filter) => filter.id !== searchFilterId);
        return value === "" ? others : [...others, { id: searchFilterId, value }];
      });
      setPagination((previous) => ({ ...previous, pageIndex: 0 }));
    },
    [viewMode],
  );

  const handleSortingChange = useCallback<OnChangeFn<SortingState>>((updaterOrValue) => {
    setSorting(updaterOrValue);
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
  }, []);

  const handleColumnFiltersChange = useCallback<OnChangeFn<ColumnFiltersState>>((updaterOrValue) => {
    setColumnFilters(updaterOrValue);
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
  }, []);

  const resetToFirstPage = useCallback(() => {
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
  }, []);

  const handleResetFilters = useCallback(() => {
    setColumnFilters([]);
    setStartTime(moment().subtract(24, "hours").format("YYYY-MM-DDTHH:mm"));
    setEndTime(moment().format("YYYY-MM-DDTHH:mm"));
    setIsCustomDate(false);
    setSelectedTimeInterval(DEFAULT_INTERVAL);
    resetToFirstPage();
  }, [resetToFirstPage]);

  const handleRowClick = useCallback(
    (log: LogEntry) => {
      setSelectedLog(log);
      openLog(log.request_id);
    },
    [openLog],
  );

  const handleSessionClick = useCallback(
    (sessionId: string) => {
      if (!sessionId) return;
      const log = requestRows.find((candidate) => candidate.session_id === sessionId) ?? null;
      setSelectedLog(log);
      openSession(sessionId, log?.request_id ?? null);
    },
    [requestRows, openSession],
  );

  const handleSessionRowClick = useCallback(
    (row: SessionLogEntry) => {
      setSelectedLog(null);
      if (row.session_id) {
        openSession(row.session_id, row.request_id);
      } else if (row.request_id) {
        openLog(row.request_id);
      }
    },
    [openLog, openSession],
  );

  const handleSelectLog = useCallback(
    (log: LogEntry) => {
      setSelectedLog(log);
      selectLog(log.request_id, displaySessionId);
    },
    [selectLog, displaySessionId],
  );

  const handleKeyHashClick = useCallback((keyHash: string) => {
    setSelectedKeyIdInfoView(keyHash);
  }, []);

  const handleViewModeChange = useCallback(
    (nextViewMode: string) => {
      const resolvedViewMode: LogsViewMode = nextViewMode === "request" ? "request" : "session";
      setViewMode(resolvedViewMode);
      setPagination((previous) => ({ ...previous, pageIndex: 0 }));
      setSorting(DEFAULT_LOGS_SORTING);
      setColumnFilters((previous) =>
        previous.filter((filter) => filter.id !== LOG_FILTER_IDS.REQUEST_ID && filter.id !== LOG_FILTER_IDS.SESSION_ID),
      );
      setSelectedLog(null);
      closeUrlLog();
    },
    [closeUrlLog],
  );

  const handleExport = useCallback(async () => {
    const exportCount = viewMode === "session" ? sessionRows.length : requestRows.length;
    if (exportCount === 0) return;

    setIsExporting(true);
    try {
      const result =
        viewMode === "session"
          ? createSessionLogExport(sessionRows)
          : await createLogExport(requestRows, async (requestId) => {
              const details: unknown = await uiSpendLogDetailsCall(
                accessToken,
                requestId,
                moment(startTime).utc().format("YYYY-MM-DD HH:mm:ss"),
              );
              return parseLogDetailsPayload(details);
            });

      if (result.status === "error") {
        NotificationsManager.fromBackend(`Failed to export ${viewMode} logs: ${result.message}`);
        return;
      }

      downloadLogExport(result.file);
      NotificationsManager.success(`Exported ${exportCount} ${viewMode} logs`);
    } catch (error) {
      const message = error instanceof Error ? error.message : `Failed to download ${viewMode} logs`;
      NotificationsManager.fromBackend(`Failed to export ${viewMode} logs: ${message}`);
    } finally {
      setIsExporting(false);
    }
  }, [accessToken, requestRows, sessionRows, startTime, viewMode]);

  if (selectedKeyInfo && selectedKeyIdInfoView && selectedKeyInfo.api_key === selectedKeyIdInfoView) {
    return (
      <KeyInfoView
        keyId={selectedKeyIdInfoView}
        keyData={selectedKeyInfo}
        teams={allTeams ?? []}
        onClose={() => setSelectedKeyIdInfoView(null)}
        backButtonText="Back to Logs"
      />
    );
  }

  return (
    <AutoRouterModelGroupsProvider>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Request Logs</h1>
        <Tabs value={viewMode} onValueChange={handleViewModeChange}>
          <TabsList>
            <TabsTrigger value="session">Sessions</TabsTrigger>
            <TabsTrigger value="request">Requests</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {isLiveTail && pagination.pageIndex === 0 && <LiveTailBanner onStop={() => setIsLiveTail(false)} />}

      {viewMode === "session" ? (
        <SessionLogsTable
          data={sessionRows}
          rowCount={filteredLogs.total}
          isLoading={logsQuery.isLoading}
          isRefreshing={logsQuery.isFetching}
          pagination={pagination}
          onPaginationChange={setPagination}
          sorting={sorting}
          onSortingChange={handleSortingChange}
          columnFilters={columnFilters}
          onColumnFiltersChange={handleColumnFiltersChange}
          searchValue={searchTerm}
          onSearchChange={handleSearchChange}
          onRefresh={() => void logsQuery.refetch()}
          onRowClick={handleSessionRowClick}
          teams={allTeams ?? []}
          logsWindow={logsWindow}
          toolbarChildren={
            <LogsTableToolbar
              startTime={startTime}
              onStartTimeChange={setStartTime}
              endTime={endTime}
              onEndTimeChange={setEndTime}
              isCustomDate={isCustomDate}
              onIsCustomDateChange={setIsCustomDate}
              selectedTimeInterval={selectedTimeInterval}
              onSelectedTimeIntervalChange={setSelectedTimeInterval}
              isLiveTail={isLiveTail}
              onIsLiveTailChange={setIsLiveTail}
              onResetToFirstPage={resetToFirstPage}
              onResetFilters={handleResetFilters}
              onExport={() => void handleExport()}
              isExporting={isExporting}
              exportDisabled={sessionRows.length === 0 || logsQuery.isLoading}
              exportLabel="Export Sessions"
            />
          }
        />
      ) : (
        <RequestLogsTable
          data={requestRows}
          rowCount={filteredLogs.total}
          isLoading={logsQuery.isLoading}
          isRefreshing={logsQuery.isFetching}
          pagination={pagination}
          onPaginationChange={setPagination}
          sorting={sorting}
          onSortingChange={handleSortingChange}
          columnFilters={columnFilters}
          onColumnFiltersChange={handleColumnFiltersChange}
          searchValue={searchTerm}
          onSearchChange={handleSearchChange}
          onRefresh={() => void logsQuery.refetch()}
          onRowClick={handleRowClick}
          onKeyHashClick={handleKeyHashClick}
          onSessionClick={handleSessionClick}
          teams={allTeams ?? []}
          logsWindow={logsWindow}
          toolbarChildren={
            <LogsTableToolbar
              startTime={startTime}
              onStartTimeChange={setStartTime}
              endTime={endTime}
              onEndTimeChange={setEndTime}
              isCustomDate={isCustomDate}
              onIsCustomDateChange={setIsCustomDate}
              selectedTimeInterval={selectedTimeInterval}
              onSelectedTimeIntervalChange={setSelectedTimeInterval}
              isLiveTail={isLiveTail}
              onIsLiveTailChange={setIsLiveTail}
              onResetToFirstPage={resetToFirstPage}
              onResetFilters={handleResetFilters}
              onExport={() => void handleExport()}
              isExporting={isExporting}
              exportDisabled={requestRows.length === 0 || logsQuery.isLoading}
              exportLabel="Export Requests"
            />
          }
        />
      )}

      <LogDetailsDrawer
        open={isDrawerOpen}
        onClose={closeUrlLog}
        logEntry={displayLog}
        sessionId={displaySessionId}
        accessToken={accessToken}
        allLogs={requestRows}
        onSelectLog={handleSelectLog}
        startTime={moment(startTime).utc().format("YYYY-MM-DD HH:mm:ss")}
      />
    </AutoRouterModelGroupsProvider>
  );
}
