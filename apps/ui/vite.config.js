import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const REACT_APP_ENV_KEYS = [
  "REACT_APP_FLASK_BASE",
  "REACT_APP_ATLAS_MODE",
  "REACT_APP_UNITY_LEGACY_BRIDGE_COMPAT",
  "REACT_APP_UNITY_URL",
  "REACT_APP_MESH_HEALTHCHECK",
  "REACT_APP_SLICE_AUTO_FOLLOW_FOCUS",
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), ["REACT_APP_", "VITE_"]);
  const defines = Object.fromEntries(
    REACT_APP_ENV_KEYS.map((key) => [
      `process.env.${key}`,
      JSON.stringify(env[key] || process.env[key] || ""),
    ]),
  );
  return {
    plugins: [react()],
    publicDir: "public",
    build: {
      outDir: "build",
      emptyOutDir: true,
      rollupOptions: {
        output: {
          manualChunks(id) {
            const normalized = id.replace(/\\/g, "/");
            if (normalized.includes("/node_modules/react") || normalized.includes("/node_modules/scheduler")) {
              return "vendor-react";
            }
            if (normalized.includes("/node_modules/three")) {
              return "vendor-three";
            }
            if (normalized.includes("/src/components/brain3d/")) {
              return "brain3d";
            }
            if (normalized.includes("/src/components/")) {
              return "ui-components";
            }
            if (normalized.includes("/src/constants/")) {
              return "ui-constants";
            }
            return undefined;
          },
        },
      },
    },
    server: {
      host: "127.0.0.1",
      port: Number(env.VITE_PORT || process.env.PORT || 3000),
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: "./src/setupTests.js",
      include: ["src/**/*.{test,spec}.{js,jsx,ts,tsx}"],
      exclude: ["tests/e2e/**", "node_modules/**", "build/**"],
    },
    define: defines,
  };
});
