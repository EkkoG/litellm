import moment from "moment";
import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { Tab, TabGroup, TabList, TabPanel, TabPanels } from "@tremor/react";
import { Segmented } from "antd";
import NotificationsManager from "../molecules/notifications_manager";
import { internalUserRoles } from "../../utils/roles";
import DeletedKeysPage from "../DeletedKeysPage/DeletedKeysPage";
import DeletedTeamsPage from "../DeletedTeamsPage/DeletedTeamsPage";
import { KeyResponse } from "../key_team_helpers/key_list";
import FilterComponent from "../molecules/filter";
import { keyInfoV1Call, uiSpendLogDetailsCall } from "../networking";
import KeyInfoView from "../templates/key_info_view";
import AuditLogs from "./audit_logs";
import { createColumns, LogEntry, type LogsSortField } from "./columns";
import { getLogFilterOptions } from "./filter_options";
import {
  useLogFilterLogic,
  defaultFilters,
  type LogFilterState,
  type LogsPageRow,
  type LogsViewMode,
} from "./log_filter_logic";
import { LogDetailsDrawer } from "./LogDetailsDrawer";
import { LogsTableToolbar } from "./LogsTableToolbar";
import { DataTable } from "./table";
import { AntDLoadingSpinner } from "../ui/AntDLoadingSpinner";
import { createLogExport, createSessionLogExport, downloadLogExport, parseLogDetailsPayload } from "./log_export";
import { createSessionColumns, type SessionLogEntry } from "./session_columns";

interface SpendLogsTableProps {
  accessToken: string | null;
  token: string | null;
  userRole: string | null;
  userID: string | null;
  premiumUser: boolean;
}

const isSessionLogEntry = (row: LogsPageRow): row is SessionLogEntry => "row_type" in row;

const toStandaloneLogEntry = (row: SessionLogEntry): LogEntry | null => {
  if (!row.request_id) return null;

  return {
    request_id: row.request_id,
    api_key: row.api_keys?.[0] ?? "",
    team_id: row.team_ids?.[0] ?? "",
    model: row.primary_model ?? row.models?.[0] ?? "",
    model_id: row.model_id ?? "",
    api_base: row.api_base ?? undefined,
    call_type: row.call_type,
    spend: row.session_spend,
    total_tokens: row.session_total_tokens,
    prompt_tokens: row.session_prompt_tokens,
    completion_tokens: row.session_completion_tokens,
    startTime: row.session_start_time,
    endTime: row.session_end_time,
    user: row.users?.[0],
    end_user: row.end_users?.[0],
    metadata: {
      status: row.failure_count > 0 ? "failure" : "success",
      user_api_key_team_alias: row.team_names?.[0],
      user_api_key_alias: row.key_aliases?.[0],
      additional_usage_values: {
        prompt_tokens_details: { cached_tokens: row.session_cache_read_tokens },
      },
    },
    cache_hit: row.session_cache_read_tokens > 0 ? "true" : "false",
    messages: [],
    response: {},
    request_duration_ms: row.session_duration_ms,
  };
};

export default function SpendLogsTable({ accessToken, token, userRole, userID, premiumUser }: SpendLogsTableProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(50);

  // New state variables for Start and End Time
  const [startTime, setStartTime] = useState<string>(moment().subtract(24, "hours").format("YYYY-MM-DDTHH:mm"));
  const [endTime, setEndTime] = useState<string>(moment().format("YYYY-MM-DDTHH:mm"));

  const [isCustomDate, setIsCustomDate] = useState(false);
  const [filters, setFilters] = useState<LogFilterState>(defaultFilters);
  const [selectedKeyInfo, setSelectedKeyInfo] = useState<KeyResponse | null>(null);
  const [selectedKeyIdInfoView, setSelectedKeyIdInfoView] = useState<string | null>(null);
  const [filterByCurrentUser, setFilterByCurrentUser] = useState(userRole && internalUserRoles.includes(userRole));
  const [activeTab, setActiveTab] = useState("request logs");
  const [viewMode, setViewMode] = useState<LogsViewMode>(() => {
    if (typeof window === "undefined") return "session";
    return new URLSearchParams(window.location.search).get("view") === "request" ? "request" : "session";
  });

  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const [sortBy, setSortBy] = useState<LogsSortField>("startTime");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [isExporting, setIsExporting] = useState(false);

  const [selectedTimeInterval, setSelectedTimeInterval] = useState<{ value: number; unit: string }>({
    value: 24,
    unit: "hours",
  });

  const [isLiveTail, setIsLiveTail] = useState<boolean>(() => {
    const storedValue = sessionStorage.getItem("isLiveTail");
    // default to true if nothing is stored
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

  useEffect(() => {
    const fetchKeyInfo = async () => {
      if (selectedKeyIdInfoView && accessToken) {
        const keyData = await keyInfoV1Call(accessToken, selectedKeyIdInfoView);

        const keyResponse: KeyResponse = {
          ...keyData["info"],
          token: selectedKeyIdInfoView,
          api_key: selectedKeyIdInfoView,
        };
        setSelectedKeyInfo(keyResponse);
      }
    };
    fetchKeyInfo();
  }, [selectedKeyIdInfoView, accessToken]);

  useEffect(() => {
    if (userRole && internalUserRoles.includes(userRole)) {
      setFilterByCurrentUser(true);
    }
  }, [userRole]);

  const {
    logsQuery,
    filteredLogs,
    allTeams,
    handleFilterChange,
    handleFilterReset: handleFilterResetFromHook,
  } = useLogFilterLogic({
    accessToken,
    token,
    userRole,
    userID,
    filters,
    setFilters,
    filterByCurrentUser: !!filterByCurrentUser,
    activeTab,
    isLiveTail,
    startTime,
    endTime,
    pageSize,
    isCustomDate,
    setCurrentPage,
    sortBy,
    sortOrder,
    currentPage,
    viewMode,
  });

  const handleFilterReset = useCallback(() => {
    handleFilterResetFromHook();
    setStartTime(moment().subtract(24, "hours").format("YYYY-MM-DDTHH:mm"));
    setEndTime(moment().format("YYYY-MM-DDTHH:mm"));
    setIsCustomDate(false);
    setSelectedTimeInterval({ value: 24, unit: "hours" });
    setCurrentPage(1);
  }, [handleFilterResetFromHook]);

  const handleSortChange = useCallback((newSortBy: LogsSortField, newSortOrder: "asc" | "desc") => {
    setSortBy(newSortBy);
    setSortOrder(newSortOrder);
    setCurrentPage(1);
  }, []);

  const requestColumns = useMemo(
    () => createColumns({ sortBy, sortOrder, onSortChange: handleSortChange }),
    [sortBy, sortOrder, handleSortChange],
  );

  const sessionColumns = useMemo(
    () => createSessionColumns({ sortBy, sortOrder, onSortChange: handleSortChange }),
    [sortBy, sortOrder, handleSortChange],
  );

  const requestLogs = useMemo(
    () => filteredLogs.data.filter((row): row is LogEntry => !isSessionLogEntry(row)),
    [filteredLogs.data],
  );

  const sessionLogs = useMemo(() => filteredLogs.data.filter(isSessionLogEntry), [filteredLogs.data]);

  const searchedRequestLogs = useMemo(
    () =>
      requestLogs.filter(
        (log) =>
          !searchTerm ||
          log.request_id.includes(searchTerm) ||
          log.model.includes(searchTerm) ||
          Boolean(log.user?.includes(searchTerm)),
      ),
    [requestLogs, searchTerm],
  );

  const searchedSessionLogs = useMemo(
    () =>
      sessionLogs.filter(
        (row) =>
          !searchTerm ||
          Boolean(row.session_id?.includes(searchTerm)) ||
          Boolean(row.request_id?.includes(searchTerm)) ||
          row.models?.some((model) => model.includes(searchTerm)) ||
          row.users?.some((user) => user.includes(searchTerm)),
      ),
    [sessionLogs, searchTerm],
  );

  const requestTableData = useMemo(
    () =>
      searchedRequestLogs.map((log) => ({
        ...log,
        onKeyHashClick: (keyHash: string) => setSelectedKeyIdInfoView(keyHash),
        onSessionClick: (sessionId: string) => {
          if (!sessionId) return;
          setSelectedSessionId(sessionId);
          setSelectedLog(log);
          setIsDrawerOpen(true);
        },
      })),
    [searchedRequestLogs],
  );

  const handleExport = useCallback(async () => {
    const exportCount = viewMode === "session" ? searchedSessionLogs.length : searchedRequestLogs.length;
    if (!accessToken || exportCount === 0) return;

    setIsExporting(true);
    try {
      const result =
        viewMode === "session"
          ? createSessionLogExport(searchedSessionLogs)
          : await createLogExport(searchedRequestLogs, async (requestId) => {
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
  }, [accessToken, searchedRequestLogs, searchedSessionLogs, startTime, viewMode]);

  // Keep the Fetch button busy until the table has actually committed the new
  // rows. `keepPreviousData` leaves logsQuery.isLoading false on refetch, so
  // without this the button clears while stale rows are still on screen.
  const deferredRequestData = useDeferredValue(requestTableData);
  const deferredSessionData = useDeferredValue(searchedSessionLogs);
  const isStale =
    viewMode === "session" ? deferredSessionData !== searchedSessionLogs : deferredRequestData !== requestTableData;
  const isButtonLoading = logsQuery.isFetching || isStale;
  const isRefiltering = logsQuery.isPlaceholderData;
  const isLogsLoading = logsQuery.isLoading || isRefiltering;
  const sessionDrawerLogs = selectedLog ? [selectedLog] : [];
  const drawerLogs = viewMode === "request" ? requestTableData : sessionDrawerLogs;

  if (!accessToken || !token || !userRole || !userID) {
    return (
      <div className="flex items-center justify-center h-64">
        <AntDLoadingSpinner size="large" />
      </div>
    );
  }

  const handleRequestRowClick = (log: LogEntry) => {
    setSelectedSessionId(null);
    setSelectedLog(log);
    setIsDrawerOpen(true);
  };

  const handleSessionRowClick = (row: SessionLogEntry) => {
    if (row.row_type === "session" && row.session_id) {
      setSelectedSessionId(row.session_id);
      setSelectedLog(null);
      setIsDrawerOpen(true);
      return;
    }

    const standaloneLog = toStandaloneLogEntry(row);
    if (!standaloneLog) return;
    setSelectedSessionId(null);
    setSelectedLog(standaloneLog);
    setIsDrawerOpen(true);
  };

  const handleViewModeChange = (nextViewMode: LogsViewMode) => {
    if (nextViewMode === viewMode) return;
    setViewMode(nextViewMode);
    setCurrentPage(1);
    setSearchTerm("");
    setSortBy("startTime");
    setSortOrder("desc");
    setSelectedSessionId(null);
    setSelectedLog(null);
    setIsDrawerOpen(false);
  };

  return (
    <div className="w-full p-6 overflow-x-hidden box-border">
      <TabGroup defaultIndex={0} onIndexChange={(index) => setActiveTab(index === 0 ? "request logs" : "audit logs")}>
        <TabList>
          <Tab>Request Logs</Tab>
          <Tab>Audit Logs</Tab>
          <Tab>Deleted Keys</Tab>
          <Tab>Deleted Teams</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            <div className="flex items-center justify-between mb-4">
              <h1 className="text-xl font-semibold">Request Logs</h1>
              <Segmented
                options={[
                  { label: "Sessions", value: "session" },
                  { label: "Requests", value: "request" },
                ]}
                value={viewMode}
                onChange={(value) => handleViewModeChange(value as LogsViewMode)}
              />
            </div>
            {selectedKeyInfo && selectedKeyIdInfoView && selectedKeyInfo.api_key === selectedKeyIdInfoView ? (
              <KeyInfoView
                keyId={selectedKeyIdInfoView}
                keyData={selectedKeyInfo}
                teams={allTeams ?? []}
                onClose={() => setSelectedKeyIdInfoView(null)}
                backButtonText="Back to Logs"
              />
            ) : (
              <>
                <FilterComponent
                  options={getLogFilterOptions(accessToken)}
                  onApplyFilters={handleFilterChange}
                  onResetFilters={handleFilterReset}
                />
                <div className="bg-white rounded-lg shadow-sm w-full max-w-full box-border">
                  <LogsTableToolbar
                    searchTerm={searchTerm}
                    onSearchChange={setSearchTerm}
                    searchPlaceholder={viewMode === "session" ? "Search by Session ID" : "Search by Request ID"}
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
                    currentPage={currentPage}
                    onCurrentPageChange={setCurrentPage}
                    pageSize={pageSize}
                    isLoading={isLogsLoading}
                    isButtonLoading={isButtonLoading}
                    onRefetch={() => logsQuery.refetch()}
                    onExport={handleExport}
                    isExporting={isExporting}
                    exportDisabled={
                      (viewMode === "session" ? searchedSessionLogs.length === 0 : searchedRequestLogs.length === 0) ||
                      isLogsLoading
                    }
                    exportLabel={viewMode === "session" ? "Export Sessions" : "Export Requests"}
                    filteredLogs={filteredLogs}
                  />
                  {viewMode === "session" ? (
                    <DataTable
                      columns={sessionColumns}
                      data={deferredSessionData}
                      getRowId={(row) => row.group_id}
                      onRowClick={handleSessionRowClick}
                      isLoading={isLogsLoading}
                    />
                  ) : (
                    <DataTable
                      columns={requestColumns}
                      data={deferredRequestData}
                      getRowId={(row) => row.request_id}
                      onRowClick={handleRequestRowClick}
                      isLoading={isLogsLoading}
                    />
                  )}
                </div>
              </>
            )}
          </TabPanel>
          <TabPanel>
            <AuditLogs
              userID={userID}
              userRole={userRole}
              token={token}
              accessToken={accessToken}
              isActive={activeTab === "audit logs"}
              premiumUser={premiumUser}
            />
          </TabPanel>
          <TabPanel>
            <DeletedKeysPage />
          </TabPanel>
          <TabPanel>
            <DeletedTeamsPage />
          </TabPanel>
        </TabPanels>
      </TabGroup>

      {/* Log Details Drawer */}
      <LogDetailsDrawer
        open={isDrawerOpen}
        onClose={() => {
          setIsDrawerOpen(false);
          setSelectedSessionId(null);
        }}
        logEntry={selectedLog}
        sessionId={selectedSessionId}
        accessToken={accessToken}
        allLogs={drawerLogs}
        onSelectLog={setSelectedLog}
        startTime={moment(startTime).utc().format("YYYY-MM-DD HH:mm:ss")}
      />
    </div>
  );
}
