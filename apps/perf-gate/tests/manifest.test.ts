import { describe, expect, test } from "vitest";
import { DEFAULT_LOCAL_REPLAY_IDS, DEFAULT_REPO_REPLAY_IDS } from "../src/config/defaults.js";

describe("default case set", () => {
  test("contains exactly ten fixed replay ids", () => {
    const ids = [...DEFAULT_REPO_REPLAY_IDS, ...DEFAULT_LOCAL_REPLAY_IDS];
    expect(ids).toHaveLength(10);
    expect(new Set(ids).size).toBe(10);
  });
});
