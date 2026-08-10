"use client";

import { useCallback, useEffect, useState } from "react";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useModelDashboardData } from "@/app/(dashboard)/models-and-endpoints/useModelDashboardData";
import ModelGroupAliasSettings, { ModelGroupAliasValue } from "@/components/model_group_alias_settings";
import { getCallbacksCall } from "@/components/networking";
import { isProxyAdminRole } from "@/utils/roles";

export default function ModelGroupAliasPanel() {
  const { accessToken, userId: userID, userRole } = useAuthorized();
  const { availableModelGroups, isLoading: isModelDataLoading } = useModelDashboardData();
  const [modelGroupAlias, setModelGroupAlias] = useState<Record<string, ModelGroupAliasValue>>({});
  const [isLoading, setIsLoading] = useState(true);

  const canEdit = Boolean(userRole && isProxyAdminRole(userRole));

  const loadAliases = useCallback(async () => {
    if (!accessToken || !userID || !userRole) {
      return;
    }
    try {
      const info = await getCallbacksCall(accessToken, userID, userRole);
      setModelGroupAlias(info.router_settings?.model_group_alias || {});
    } catch (error) {
      console.error("Error fetching model group alias:", error);
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, userID, userRole]);

  useEffect(() => {
    void Promise.resolve().then(loadAliases);
  }, [loadAliases]);

  return (
    <ModelGroupAliasSettings
      accessToken={accessToken}
      initialModelGroupAlias={modelGroupAlias}
      availableModelGroups={availableModelGroups}
      canEdit={canEdit}
      isLoading={isLoading || isModelDataLoading}
      onReload={loadAliases}
    />
  );
}
