import { describe, expect, it } from "vitest";
import { TOKEN_LINE_DIMENSIONS, formatTokenLineValue } from "./token_line_dimensions";

describe("TOKEN_LINE_DIMENSIONS", () => {
  const metrics = {
    spend: 1.5,
    prompt_tokens: 100000,
    completion_tokens: 25000,
    total_tokens: 125000,
    api_requests: 10,
    successful_requests: 9,
    failed_requests: 1,
    cache_read_input_tokens: 60000,
    cache_creation_input_tokens: 5000,
  };

  it("exposes a unique key for every dimension", () => {
    const keys = TOKEN_LINE_DIMENSIONS.map((dimension) => dimension.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("reads token counts from daily metrics", () => {
    const byKey = Object.fromEntries(TOKEN_LINE_DIMENSIONS.map((dimension) => [dimension.key, dimension]));
    expect(byKey["metrics.prompt_tokens"].getValue(metrics)).toBe(100000);
    expect(byKey["metrics.completion_tokens"].getValue(metrics)).toBe(25000);
    expect(byKey["metrics.cache_read_input_tokens"].getValue(metrics)).toBe(60000);
    expect(byKey["metrics.cache_creation_input_tokens"].getValue(metrics)).toBe(5000);
  });

  it("computes the cache hit rate as a percentage of prompt tokens", () => {
    const hitRate = TOKEN_LINE_DIMENSIONS.find((dimension) => dimension.key === "metrics.prompt_cache_hit_rate");
    expect(hitRate?.getValue(metrics)).toBe(60);
  });

  it("returns a zero hit rate instead of NaN when prompt tokens are zero", () => {
    const hitRate = TOKEN_LINE_DIMENSIONS.find((dimension) => dimension.key === "metrics.prompt_cache_hit_rate");
    expect(hitRate?.getValue({ ...metrics, prompt_tokens: 0 })).toBe(0);
  });

  it("tolerates missing metric fields", () => {
    for (const dimension of TOKEN_LINE_DIMENSIONS) {
      expect(dimension.getValue({})).toBe(0);
    }
  });
});

describe("formatTokenLineValue", () => {
  it("formats rate dimensions as percentages", () => {
    const hitRate = TOKEN_LINE_DIMENSIONS.find((dimension) => dimension.key === "metrics.prompt_cache_hit_rate");
    expect(formatTokenLineValue(hitRate!, 60)).toBe("60.0%");
  });

  it("formats token dimensions with comma separators", () => {
    const promptTokens = TOKEN_LINE_DIMENSIONS.find((dimension) => dimension.key === "metrics.prompt_tokens");
    expect(formatTokenLineValue(promptTokens!, 1234567)).toBe("1,234,567");
  });
});
