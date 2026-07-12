import { describe, expect, it, vi } from "vitest";
import type { LogEntry } from "./columns";
import { createLogExport, parseLogDetailsPayload } from "./log_export";

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
  it("includes parsed request and response JSON for every exported log", async () => {
    const loadDetails = vi
      .fn()
      .mockResolvedValueOnce({
        messages: '[{"role":"user","content":"hello"}]',
        proxy_server_request: '{"model":"gpt-test","temperature":0}',
        response: '{"choices":[{"message":{"content":"hi"}}]}',
      })
      .mockResolvedValueOnce({
        messages: [{ role: "user", content: "second" }],
        proxy_server_request: { model: "gpt-test" },
        response: { output: "done" },
      });

    const result = await createLogExport(
      [createLog("request-1"), createLog("request-2")],
      loadDetails,
      new Date("2026-07-12T08:00:00Z"),
    );

    expect(result.status).toBe("success");
    if (result.status !== "success") return;

    expect(result.file.name).toBe("request_logs_2026-07-12T08-00-00-000Z.json");
    expect(JSON.parse(result.file.contents)).toEqual({
      exported_at: "2026-07-12T08:00:00.000Z",
      log_count: 2,
      logs: [
        expect.objectContaining({
          request_id: "request-1",
          messages: [{ role: "user", content: "hello" }],
          proxy_server_request: { model: "gpt-test", temperature: 0 },
          response: { choices: [{ message: { content: "hi" } }] },
        }),
        expect.objectContaining({
          request_id: "request-2",
          messages: [{ role: "user", content: "second" }],
          proxy_server_request: { model: "gpt-test" },
          response: { output: "done" },
        }),
      ],
    });
    expect(loadDetails).toHaveBeenNthCalledWith(1, "request-1");
    expect(loadDetails).toHaveBeenNthCalledWith(2, "request-2");
  });

  it("does not produce a partial export when request or response details fail to load", async () => {
    const result = await createLogExport([createLog("request-1")], async () => {
      throw new Error("detail unavailable");
    });

    expect(result).toEqual({ status: "error", message: "detail unavailable" });
  });

  it("falls back to request and response JSON already present on the log row", async () => {
    const log = {
      ...createLog("request-1"),
      messages: '[{"role":"user","content":"fallback"}]',
      response: '{"output":"fallback response"}',
    };

    const result = await createLogExport([log], async () => null);

    expect(result.status).toBe("success");
    if (result.status !== "success") return;

    expect(JSON.parse(result.file.contents).logs[0]).toEqual(
      expect.objectContaining({
        messages: [{ role: "user", content: "fallback" }],
        response: { output: "fallback response" },
      }),
    );
  });

  it("fails when a log has no usable request or response JSON", async () => {
    const result = await createLogExport([createLog("request-1")], async () => null);

    expect(result).toEqual({
      status: "error",
      message: "Request or response JSON is unavailable for log request-1",
    });
  });

  it("rejects malformed detail envelopes", () => {
    expect(parseLogDetailsPayload(null)).toBeNull();
    expect(parseLogDetailsPayload([])).toBeNull();
    expect(parseLogDetailsPayload("not-an-object")).toBeNull();
    expect(parseLogDetailsPayload({ messages: [], response: {} })).toEqual({
      messages: [],
      response: {},
      proxy_server_request: undefined,
    });
  });
});
