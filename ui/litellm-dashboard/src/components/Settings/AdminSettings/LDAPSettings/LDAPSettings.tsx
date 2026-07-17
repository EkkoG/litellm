"use client";

import { useLDAPSettings } from "@/app/(dashboard)/hooks/ldap/useLDAPSettings";
import { useUpdateLDAPSettings } from "@/app/(dashboard)/hooks/ldap/useUpdateLDAPSettings";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { LDAPUserStatusSyncResult, syncLDAPUserStatus } from "@/components/networking";
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Switch, Typography } from "antd";
import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";

const { Title, Text } = Typography;

const LDAP_ACCESS_FILTER_TEMPLATES = [
  {
    label: "Active Directory: account enabled",
    value: "(!(userAccountControl:1.2.840.113556.1.4.803:=2))",
  },
  { label: "OpenLDAP: password not locked", value: "(!(pwdAccountLockedTime=*))" },
  { label: "FreeIPA / 389 DS: account not locked", value: "(!(nsAccountLock=TRUE))" },
];

const notifySyncResult = (result: LDAPUserStatusSyncResult, dryRun: boolean) => {
  if (result.aborted) {
    NotificationsManager.fromBackend(result.abort_reason || "LDAP user status synchronization aborted");
    return;
  }
  NotificationsManager.success(dryRun ? "LDAP dry run completed" : "LDAP user status synchronized");
};

const getSyncAlert = (result: LDAPUserStatusSyncResult) => ({
  type: result.aborted ? ("warning" as const) : ("success" as const),
  message: result.aborted ? "Last sync aborted" : "Last sync completed",
  description:
    result.abort_reason ||
    `Scanned ${result.scanned}; activated ${result.activated || result.would_activate}; deactivated ${result.deactivated || result.would_deactivate}; missing ${result.missing}; unknown ${result.unknown}; unchanged ${result.unchanged}`,
});

export default function LDAPSettings() {
  const [form] = Form.useForm();
  const { accessToken } = useAuthorized();
  const { data, isLoading, isError, error, refetch } = useLDAPSettings();
  const { mutate: updateSettings, isPending } = useUpdateLDAPSettings(accessToken || "");
  const [syncPending, setSyncPending] = useState(false);
  const [lastSyncResult, setLastSyncResult] = useState<LDAPUserStatusSyncResult | null>(null);

  useEffect(() => {
    if (!data?.values) {
      return;
    }
    form.setFieldsValue({
      ...data.values,
      ldap_bind_password: undefined,
    });
  }, [data, form]);

  const handleSubmit = (values: Record<string, unknown>) => {
    const payload: Record<string, unknown> = {
      ...values,
      ldap_enabled: Boolean(values.ldap_enabled),
      ldap_use_ssl: Boolean(values.ldap_use_ssl),
      ldap_start_tls: Boolean(values.ldap_start_tls),
      ldap_allow_insecure: Boolean(values.ldap_allow_insecure),
      ldap_sync_enabled: Boolean(values.ldap_sync_enabled),
      ldap_sync_run_on_startup: Boolean(values.ldap_sync_run_on_startup),
    };
    if (!payload.ldap_bind_password) {
      delete payload.ldap_bind_password;
    }

    updateSettings(payload, {
      onSuccess: () => {
        NotificationsManager.success("LDAP settings updated successfully");
        refetch();
      },
      onError: (updateError) => {
        NotificationsManager.fromBackend(`Failed to update LDAP settings: ${updateError.message}`);
      },
    });
  };

  const handleSync = async (dryRun: boolean) => {
    if (!accessToken) {
      return;
    }
    setSyncPending(true);
    try {
      const result = await syncLDAPUserStatus(accessToken, dryRun);
      setLastSyncResult(result);
      notifySyncResult(result, dryRun);
    } catch (syncError) {
      const message = syncError instanceof Error ? syncError.message : "Unknown error";
      NotificationsManager.fromBackend(`Failed to synchronize LDAP user status: ${message}`);
    } finally {
      setSyncPending(false);
    }
  };

  const isConfigured = Boolean(data?.values.ldap_enabled && data?.values.ldap_url && data?.values.ldap_base_dn);

  return (
    <Card>
      <Space direction="vertical" size="large" className="w-full">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <KeyRound className="w-6 h-6 text-gray-400" />
            <div>
              <Title level={3}>LDAP Configuration</Title>
              <Text type="secondary">Directory login for the Admin UI</Text>
            </div>
          </div>
          <Text type={isConfigured ? "success" : "secondary"}>{isConfigured ? "Configured" : "Not configured"}</Text>
        </div>

        {isError && <Alert type="error" showIcon message={error?.message || "Failed to load LDAP settings"} />}

        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          disabled={isLoading || isPending}
          initialValues={{
            ldap_enabled: false,
            ldap_user_search_filter: "(|(uid={username})(sAMAccountName={username})(userPrincipalName={username}))",
            ldap_email_attribute: "mail",
            ldap_display_name_attribute: "displayName",
            ldap_group_attribute: "memberOf",
            ldap_use_ssl: false,
            ldap_start_tls: false,
            ldap_allow_insecure: false,
            ldap_sync_enabled: false,
            ldap_sync_interval_seconds: 300,
            ldap_sync_run_on_startup: false,
            ldap_sync_lock_ttl_seconds: 900,
            ldap_missing_user_action: "ignore",
            ldap_sync_max_deactivation_ratio: 0.2,
          }}
        >
          <Form.Item name="ldap_enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="ldap_url" label="LDAP URL" rules={[{ required: true, message: "LDAP URL is required" }]}>
            <Input placeholder="ldap://ldap.example.com:389" />
          </Form.Item>
          <Form.Item name="ldap_base_dn" label="Base DN" rules={[{ required: true, message: "Base DN is required" }]}>
            <Input placeholder="dc=example,dc=com" />
          </Form.Item>
          <Form.Item name="ldap_search_base" label="Search Base">
            <Input placeholder="ou=People,dc=example,dc=com" />
          </Form.Item>
          <Form.Item name="ldap_bind_dn" label="Bind DN">
            <Input placeholder="cn=admin,dc=example,dc=com" />
          </Form.Item>
          <Form.Item name="ldap_bind_password" label="Bind Password">
            <Input.Password
              placeholder={data?.values.ldap_bind_password ? "Configured" : ""}
              autoComplete="new-password"
            />
          </Form.Item>
          <Form.Item
            name="ldap_user_search_filter"
            label="User Search Filter"
            rules={[{ required: true, message: "User search filter is required" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="ldap_access_filter"
            label="Access Filter"
            extra="Users must match this LDAP filter to log in and remain active. Leave blank to preserve existing behavior."
          >
            <Input placeholder="(!(pwdAccountLockedTime=*))" />
          </Form.Item>
          <Form.Item label="Access Filter Template">
            <Select
              allowClear
              placeholder="Choose a directory template"
              onChange={(value: string | undefined) => value && form.setFieldValue("ldap_access_filter", value)}
              options={LDAP_ACCESS_FILTER_TEMPLATES}
            />
          </Form.Item>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Form.Item
              name="ldap_user_id_attribute"
              label="Stable User ID Attribute"
              extra="For Active Directory Domain Services (AD DS), use objectGUID. For OpenLDAP, use entryUUID. If left blank, LiteLLM uses the LDAP DN, which can change when an entry is renamed or moved."
            >
              <Input placeholder="objectGUID or entryUUID" />
            </Form.Item>
            <Form.Item name="ldap_email_attribute" label="Email Attribute">
              <Input />
            </Form.Item>
            <Form.Item name="ldap_display_name_attribute" label="Display Name Attribute">
              <Input />
            </Form.Item>
            <Form.Item name="ldap_group_attribute" label="Group Attribute">
              <Input />
            </Form.Item>
          </div>
          <Form.Item name="ldap_admin_group_dn" label="Admin Group DN">
            <Input placeholder="cn=litellm-admins,ou=Groups,dc=example,dc=com" />
          </Form.Item>
          <Space size="large">
            <Form.Item name="ldap_use_ssl" label="Use SSL" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="ldap_start_tls" label="StartTLS" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="ldap_allow_insecure" label="Allow Insecure LDAP" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Card size="small" title="LDAP User Status Synchronization">
            <Space direction="vertical" className="w-full">
              <Space size="large" wrap>
                <Form.Item name="ldap_sync_enabled" label="Scheduled Sync" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="ldap_sync_run_on_startup" label="Run on Startup" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </Space>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Form.Item
                  name="ldap_sync_interval_seconds"
                  label="Sync Interval (seconds)"
                  rules={[{ type: "number", min: 60 }]}
                >
                  <InputNumber min={60} className="w-full" />
                </Form.Item>
                <Form.Item
                  name="ldap_sync_lock_ttl_seconds"
                  label="Distributed Lock TTL (seconds)"
                  rules={[{ type: "number", min: 1 }]}
                >
                  <InputNumber min={1} className="w-full" />
                </Form.Item>
                <Form.Item name="ldap_missing_user_action" label="Missing User Action">
                  <Select
                    options={[
                      { label: "Ignore and preserve current access", value: "ignore" },
                      { label: "Disable local identity", value: "disable" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  name="ldap_sync_max_deactivation_ratio"
                  label="Maximum Deactivation Ratio"
                  rules={[{ type: "number", min: 0, max: 1 }]}
                  extra="Abort the run if more than this fraction of LDAP users would be newly deactivated."
                >
                  <InputNumber min={0} max={1} step={0.05} className="w-full" />
                </Form.Item>
              </div>
              <Space wrap>
                <Button
                  onClick={() => handleSync(true)}
                  loading={syncPending}
                  disabled={!data?.values.ldap_access_filter}
                >
                  Dry Run Sync
                </Button>
                <Button
                  danger
                  onClick={() => handleSync(false)}
                  loading={syncPending}
                  disabled={!data?.values.ldap_access_filter}
                >
                  Run Sync Now
                </Button>
              </Space>
              <Text type="secondary">Save LDAP settings before running a synchronization.</Text>
              {lastSyncResult && (
                <Alert
                  type={getSyncAlert(lastSyncResult).type}
                  showIcon
                  message={getSyncAlert(lastSyncResult).message}
                  description={getSyncAlert(lastSyncResult).description}
                />
              )}
            </Space>
          </Card>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={isPending}>
              Save LDAP Settings
            </Button>
          </Form.Item>
        </Form>
      </Space>
    </Card>
  );
}
