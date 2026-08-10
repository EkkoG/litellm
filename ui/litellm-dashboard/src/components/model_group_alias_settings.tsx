import React, { useEffect, useMemo, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, PencilIcon, PlusCircleIcon, TrashIcon } from "@heroicons/react/outline";
/* eslint-disable no-restricted-imports -- extends the existing alias-settings UI built on antd/tremor; migrating to shadcn is out of scope */
import { Button, Popconfirm, Select, Switch, Tag } from "antd";
import { Card, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow, Text, Title } from "@tremor/react";
/* eslint-enable no-restricted-imports */

import NotificationsManager from "./molecules/notifications_manager";
import { createModelGroupAliasCall, deleteModelGroupAliasCall, updateModelGroupAliasCall } from "./networking";

export type ModelGroupAliasValue = string | { model: string; hidden?: boolean; enabled?: boolean };

interface AliasItem {
  id: string;
  aliasName: string;
  targetModelGroup: string;
  hidden: boolean;
  enabled: boolean;
  isNew?: boolean;
  isDeleted?: boolean;
}

interface ModelGroupAliasSettingsProps {
  accessToken: string;
  initialModelGroupAlias?: Record<string, ModelGroupAliasValue>;
  availableModelGroups: string[];
  canEdit: boolean;
  isLoading?: boolean;
  onAliasesChange?: (updatedAlias: Record<string, ModelGroupAliasValue>) => void;
  onReload?: () => void;
}

const normalizeAliases = (input: Record<string, ModelGroupAliasValue>): AliasItem[] =>
  Object.entries(input).map(([aliasName, value], index) => {
    const isString = typeof value === "string";
    const model = isString ? value : value?.model ?? "";
    const hidden = isString ? false : Boolean(value?.hidden);
    const enabled = isString ? true : value?.enabled !== false;
    return {
      id: `${index}-${aliasName}`,
      aliasName,
      targetModelGroup: model,
      hidden,
      enabled,
    };
  });

const serializeAliases = (items: AliasItem[]): Record<string, ModelGroupAliasValue> =>
  items
    .filter((item) => !item.isDeleted)
    .reduce<Record<string, ModelGroupAliasValue>>((acc, item) => {
      acc[item.aliasName] = {
        model: item.targetModelGroup,
        hidden: item.hidden,
        enabled: item.enabled,
      };
      return acc;
    }, {});

const discoveryLabel = (enabled: boolean, hidden: boolean): string => {
  if (!enabled) {
    return "Not listed while disabled";
  }
  return hidden ? "Hidden" : "Listed";
};

const ModelGroupAliasSettings: React.FC<ModelGroupAliasSettingsProps> = ({
  accessToken,
  initialModelGroupAlias = {},
  availableModelGroups,
  canEdit,
  isLoading = false,
  onAliasesChange,
  onReload,
}) => {
  const [aliases, setAliases] = useState<AliasItem[]>([]);
  const [newAlias, setNewAlias] = useState({ aliasName: "", targetModelGroup: "" });
  const [editingAlias, setEditingAlias] = useState<AliasItem | null>(null);
  const [isExpanded, setIsExpanded] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setAliases(normalizeAliases(initialModelGroupAlias));
    setEditingAlias(null);
  }, [initialModelGroupAlias]);

  const targetOptions = useMemo(() => {
    const existing = new Set(availableModelGroups);
    const currentTargets = aliases.map((alias) => alias.targetModelGroup).filter(Boolean);
    const options = new Set([...availableModelGroups, ...currentTargets]);
    return Array.from(options)
      .filter((option) => option && !aliases.some((alias) => alias.aliasName === option))
      .map((option) => ({
        label: existing.has(option) ? option : `${option} (invalid target)`,
        value: option,
      }));
  }, [aliases, availableModelGroups]);

  const dirty = useMemo(() => {
    const normalizedInitial = normalizeAliases(initialModelGroupAlias);
    return JSON.stringify(normalizedInitial) !== JSON.stringify(aliases);
  }, [aliases, initialModelGroupAlias]);

  const visibleAliases = aliases.filter((alias) => !alias.isDeleted);

  const updateAliases = (nextAliases: AliasItem[]) => {
    setAliases(nextAliases);
    onAliasesChange?.(serializeAliases(nextAliases));
  };

  const validateAlias = (alias: AliasItem) => {
    const aliasName = alias.aliasName.trim();
    const target = alias.targetModelGroup.trim();
    if (!aliasName || !target) {
      return "Please provide both alias name and target model group";
    }
    if (aliasName === target) {
      return "Alias cannot point to itself";
    }
    if (aliases.some((item) => item.id !== alias.id && !item.isDeleted && item.aliasName === aliasName)) {
      return "An alias with this name already exists";
    }
    if (aliases.some((item) => item.aliasName === target)) {
      return "Alias cannot point to another alias";
    }
    if (!availableModelGroups.includes(target)) {
      return "Target model group is invalid";
    }
    return null;
  };

  const handleAddAlias = () => {
    const candidate: AliasItem = {
      id: `${Date.now()}-${newAlias.aliasName}`,
      aliasName: newAlias.aliasName.trim(),
      targetModelGroup: newAlias.targetModelGroup,
      hidden: false,
      enabled: true,
      isNew: true,
    };

    const error = validateAlias(candidate);
    if (error) {
      NotificationsManager.fromBackend(error);
      return;
    }

    updateAliases([...aliases, candidate]);
    setNewAlias({ aliasName: "", targetModelGroup: "" });
  };

  const handleEditAlias = (alias: AliasItem) => {
    setEditingAlias({ ...alias });
  };

  const handleCancelEdit = () => {
    setEditingAlias(null);
  };

  const handleSaveEdit = () => {
    if (!editingAlias) return;
    const error = validateAlias(editingAlias);
    if (error) {
      NotificationsManager.fromBackend(error);
      return;
    }
    const nextAliases = aliases.map((alias) => (alias.id === editingAlias.id ? editingAlias : alias));
    updateAliases(nextAliases);
    setEditingAlias(null);
  };

  const handleToggleAlias = (aliasId: string, updates: Partial<AliasItem>) => {
    const nextAliases = aliases.map((alias) => (alias.id === aliasId ? { ...alias, ...updates } : alias));
    updateAliases(nextAliases);
  };

  const handleDeleteAlias = (aliasId: string) => {
    const nextAliases = aliases.map((alias) => (alias.id === aliasId ? { ...alias, isDeleted: true } : alias));
    updateAliases(nextAliases);
  };

  const handleDiscard = () => {
    setAliases(normalizeAliases(initialModelGroupAlias));
    setEditingAlias(null);
  };

  const handleSaveAll = async () => {
    if (!accessToken) {
      NotificationsManager.fromBackend("Missing access token");
      return;
    }

    setIsSaving(true);
    try {
      const initialItems = normalizeAliases(initialModelGroupAlias);
      const initialByName = new Map(initialItems.map((item) => [item.aliasName, item]));
      const currentByName = new Map(visibleAliases.map((item) => [item.aliasName, item]));

      const deleted = initialItems.filter((item) => !currentByName.has(item.aliasName));
      const created = visibleAliases.filter((item) => item.isNew || !initialByName.has(item.aliasName));
      const updated = visibleAliases.filter((item) => {
        const initial = initialByName.get(item.aliasName);
        if (!initial) return false;
        return (
          initial.targetModelGroup !== item.targetModelGroup ||
          initial.hidden !== item.hidden ||
          initial.enabled !== item.enabled
        );
      });

      for (const item of created) {
        const createRequest = {
          name: item.aliasName,
          model: item.targetModelGroup,
          hidden: item.hidden,
          enabled: item.enabled,
        };
        await createModelGroupAliasCall(accessToken, createRequest);
      }

      for (const item of updated) {
        await updateModelGroupAliasCall(accessToken, item.aliasName, {
          model: item.targetModelGroup,
          hidden: item.hidden,
          enabled: item.enabled,
        });
      }

      for (const item of deleted) {
        await deleteModelGroupAliasCall(accessToken, item.aliasName);
      }

      NotificationsManager.success("Model group aliases saved");
      onReload?.();
    } catch (error) {
      console.error("Failed to save model group alias settings:", error);
      NotificationsManager.fromBackend("Failed to save model group alias settings");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <Card className="mb-6">
        <Title className="mb-2">Model Group Alias Settings</Title>
        <Text className="text-sm text-gray-500">Loading aliases...</Text>
      </Card>
    );
  }

  return (
    <Card className="mb-6">
      <div className="flex items-center justify-between cursor-pointer" onClick={() => setIsExpanded(!isExpanded)}>
        <div className="flex flex-col">
          <Title className="mb-0">Model Group Alias Settings</Title>
          <p className="text-sm text-gray-500">
            Create aliases for your model groups to simplify API calls. For example, you can create an alias
            &apos;gpt-4o&apos; that points to &apos;gpt-4o-mini-openai&apos; model group.
          </p>
        </div>
        <div className="flex items-center">
          {isExpanded ? (
            <ChevronDownIcon className="w-5 h-5 text-gray-500" />
          ) : (
            <ChevronRightIcon className="w-5 h-5 text-gray-500" />
          )}
        </div>
      </div>

      {isExpanded && (
        <div className="mt-4">
          <div className="mb-6">
            <Text className="text-sm font-medium text-gray-700 mb-2">Add New Alias</Text>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Alias Name</label>
                <input
                  type="text"
                  value={newAlias.aliasName}
                  onChange={(e) =>
                    setNewAlias({
                      ...newAlias,
                      aliasName: e.target.value,
                    })
                  }
                  placeholder="e.g., gpt-4o"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  disabled={!canEdit}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Target Model Group</label>
                <Select
                  value={newAlias.targetModelGroup || undefined}
                  onChange={(value) =>
                    setNewAlias({
                      ...newAlias,
                      targetModelGroup: value,
                    })
                  }
                  options={targetOptions}
                  showSearch
                  placeholder="Select a model group"
                  className="w-full"
                  disabled={!canEdit}
                />
              </div>
              <div className="flex items-end">
                <button
                  onClick={handleAddAlias}
                  disabled={!canEdit || !newAlias.aliasName || !newAlias.targetModelGroup}
                  className={`flex items-center px-4 py-2 rounded-md text-sm ${
                    !canEdit || !newAlias.aliasName || !newAlias.targetModelGroup
                      ? "bg-gray-300 text-gray-500 cursor-not-allowed"
                      : "bg-green-600 text-white hover:bg-green-700"
                  }`}
                >
                  <PlusCircleIcon className="w-4 h-4 mr-1" />
                  Add Alias
                </button>
              </div>
            </div>
          </div>

          <Text className="text-sm font-medium text-gray-700 mb-2">Manage Existing Aliases</Text>
          <div className="rounded-lg custom-border relative mb-6">
            <div className="overflow-x-auto">
              <Table className="[&_td]:py-0.5 [&_th]:py-1">
                <TableHead>
                  <TableRow>
                    <TableHeaderCell className="py-1 h-8">Alias Name</TableHeaderCell>
                    <TableHeaderCell className="py-1 h-8">Target Model Group</TableHeaderCell>
                    <TableHeaderCell className="py-1 h-8">Status</TableHeaderCell>
                    <TableHeaderCell className="py-1 h-8">Discovery</TableHeaderCell>
                    <TableHeaderCell className="py-1 h-8">Actions</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {visibleAliases.map((alias) => (
                    <TableRow key={alias.id} className="h-8">
                      {editingAlias && editingAlias.id === alias.id ? (
                        <>
                          <TableCell className="py-0.5">
                            <input
                              type="text"
                              value={editingAlias.aliasName}
                              onChange={(e) =>
                                setEditingAlias({
                                  ...editingAlias,
                                  aliasName: e.target.value,
                                })
                              }
                              className="w-full px-2 py-1 border border-gray-300 rounded-md text-sm"
                              disabled={!canEdit}
                            />
                          </TableCell>
                          <TableCell className="py-0.5">
                            <Select
                              value={editingAlias.targetModelGroup || undefined}
                              onChange={(value) =>
                                setEditingAlias({
                                  ...editingAlias,
                                  targetModelGroup: value,
                                })
                              }
                              options={targetOptions}
                              showSearch
                              className="w-full"
                              disabled={!canEdit}
                            />
                          </TableCell>
                          <TableCell className="py-0.5">
                            <Switch
                              checked={editingAlias.enabled}
                              onChange={(checked) =>
                                setEditingAlias({
                                  ...editingAlias,
                                  enabled: checked,
                                })
                              }
                              disabled={!canEdit}
                            />
                            <span className="ml-2 text-xs text-gray-500">
                              {editingAlias.enabled ? "Enabled" : "Disabled"}
                            </span>
                          </TableCell>
                          <TableCell className="py-0.5">
                            <Switch
                              checked={!editingAlias.hidden}
                              onChange={(checked) =>
                                setEditingAlias({
                                  ...editingAlias,
                                  hidden: !checked,
                                })
                              }
                              disabled={!canEdit || !editingAlias.enabled}
                            />
                            <span className="ml-2 text-xs text-gray-500">
                              {discoveryLabel(editingAlias.enabled, editingAlias.hidden)}
                            </span>
                          </TableCell>
                          <TableCell className="py-0.5 whitespace-nowrap">
                            <div className="flex space-x-2">
                              <button
                                onClick={handleSaveEdit}
                                className="text-xs bg-blue-50 text-blue-600 px-2 py-1 rounded-sm hover:bg-blue-100"
                                disabled={!canEdit}
                              >
                                Save
                              </button>
                              <button
                                onClick={handleCancelEdit}
                                className="text-xs bg-gray-50 text-gray-600 px-2 py-1 rounded-sm hover:bg-gray-100"
                              >
                                Cancel
                              </button>
                            </div>
                          </TableCell>
                        </>
                      ) : (
                        <>
                          <TableCell className="py-0.5 text-sm text-gray-900">{alias.aliasName}</TableCell>
                          <TableCell className="py-0.5 text-sm text-gray-500">
                            {alias.targetModelGroup}
                            {!availableModelGroups.includes(alias.targetModelGroup) && (
                              <Tag color="orange" className="ml-2">
                                Invalid target
                              </Tag>
                            )}
                          </TableCell>
                          <TableCell className="py-0.5">
                            <Switch
                              checked={alias.enabled}
                              onChange={(checked) => handleToggleAlias(alias.id, { enabled: checked })}
                              disabled={!canEdit}
                            />
                            <span className="ml-2 text-xs text-gray-500">{alias.enabled ? "Enabled" : "Disabled"}</span>
                          </TableCell>
                          <TableCell className="py-0.5">
                            <Switch
                              checked={!alias.hidden}
                              onChange={(checked) => handleToggleAlias(alias.id, { hidden: !checked })}
                              disabled={!canEdit || !alias.enabled}
                            />
                            <span className="ml-2 text-xs text-gray-500">
                              {discoveryLabel(alias.enabled, alias.hidden)}
                            </span>
                          </TableCell>
                          <TableCell className="py-0.5 whitespace-nowrap">
                            <div className="flex space-x-2">
                              <button
                                onClick={() => handleEditAlias(alias)}
                                className="text-xs bg-blue-50 text-blue-600 px-2 py-1 rounded-sm hover:bg-blue-100"
                                disabled={!canEdit}
                              >
                                <PencilIcon className="w-3 h-3" />
                              </button>
                              <Popconfirm
                                title="Delete alias"
                                description="Deleting removes the alias permanently. Disable it if you want to keep the configuration."
                                okText="Delete"
                                cancelText="Cancel"
                                onConfirm={() => handleDeleteAlias(alias.id)}
                                disabled={!canEdit}
                              >
                                <button
                                  className="text-xs bg-red-50 text-red-600 px-2 py-1 rounded-sm hover:bg-red-100"
                                  disabled={!canEdit}
                                >
                                  <TrashIcon className="w-3 h-3" />
                                </button>
                              </Popconfirm>
                            </div>
                          </TableCell>
                        </>
                      )}
                    </TableRow>
                  ))}
                  {visibleAliases.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={5} className="py-0.5 text-sm text-gray-500 text-center">
                        No aliases added yet. Add a new alias above.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </div>

          {canEdit && (
            <div className="flex items-center justify-end gap-3">
              <Button onClick={handleDiscard} disabled={!dirty || isSaving}>
                Discard
              </Button>
              <Button type="primary" onClick={handleSaveAll} disabled={!dirty || isSaving} loading={isSaving}>
                Save changes
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
};

export default ModelGroupAliasSettings;
