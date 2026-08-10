import { describe, expect, it, vi } from "vitest";
import type { LogEntry } from "./columns";
import { createLogExport, createSessionLogExport, parseLogDetailsPayload } from "./log_export";
import type { SessionLogEntry } from "./SessionLogsTableColumns";

const createLog = (requestId: string): LogEntry => ({
  request_id: requestId,
  api_key: "hashed-key",
  team_id: "team-1",
  model: "gpt-test",
  model_id: "model-1",
  call_type: "acompletion",
  spend: 0.01,
  total_tokens: 3,
  prompt_tokens: 2,
  completion_tokens: 1,
  startTime: "2026-07-12T00:00:00Z",
  endTime: "2026-07-12T00:00:01Z",
  cache_hit: "False",
  messages: "{}",
  response: "{}",
});

describe("createLogExport", () => {
  it("loads and parses request and response JSON", async () => {
    const loadDetails = vi.fn().mockResolvedValue({
      messages: '[{"role":"user","content":"hello"}]',
      proxy_server_request: '{"model":"gpt-test"}',
      response: '{"output":"done"}',
    });

    const result = await createLogExport([createLog("request-1")], loadDetails, new Date("2026-07-12T08:00:00Z"));

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(result.file.name).toBe("request_logs_2026-07-12T08-00-00-000Z.json");
    expect(JSON.parse(result.file.contents).logs[0]).toEqual(
      expect.objectContaining({
        request_id: "request-1",
        messages: [{ role: "user", content: "hello" }],
        proxy_server_request: { model: "gpt-test" },
        response: { output: "done" },
      }),
    );
  });

  it("accepts an empty proxy request object when a response is available", async () => {
    const result = await createLogExport([createLog("request-1")], async () => ({
      proxy_server_request: "{}",
      response: '{"output":"done"}',
    }));

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(JSON.parse(result.file.contents).logs[0].proxy_server_request).toEqual({});
  });

  it("exports failed logs without a response but still requires request JSON", async () => {
    const failedLog = { ...createLog("request-1"), status: "failure", response: "" };
    const result = await createLogExport([failedLog], async () => ({ proxy_server_request: '{"model":"gpt-test"}' }));

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(JSON.parse(result.file.contents).logs[0].response).toBeNull();
  });

  it("rejects malformed detail envelopes", () => {
    expect(parseLogDetailsPayload(null)).toBeNull();
    expect(parseLogDetailsPayload([])).toBeNull();
    expect(parseLogDetailsPayload({ messages: [], response: {} })).toEqual({
      messages: [],
      response: {},
      proxy_server_request: undefined,
    });
  });
});

describe("createSessionLogExport", () => {
  it("exports aggregate session rows without request detail fetches", () => {
    const session = {
      row_type: "session",
      group_id: "session:sess-1",
      session_id: "sess-1",
      request_id: null,
      request_count: 2,
    } as SessionLogEntry;

    const result = createSessionLogExport([session], new Date("2026-07-12T08:00:00Z"));

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(result.file.name).toBe("session_logs_2026-07-12T08-00-00-000Z.json");
    expect(JSON.parse(result.file.contents).sessions).toEqual([session]);
  });
});
