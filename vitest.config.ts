import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["apps/perf-gate/tests/**/*.test.ts"],
    environment: "node",
  },
});
