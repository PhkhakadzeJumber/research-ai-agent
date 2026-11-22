import path from "node:path";

import { defineConfig } from "vitest/config";

// Tests for the frontend's logic (npm test): a browser-like DOM (jsdom), the same "@/..." imports as the app.
export default defineConfig({
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
});
