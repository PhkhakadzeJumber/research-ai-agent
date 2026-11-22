import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests (step 10d): `npm run e2e`, against the running site (e.g. `docker compose up --build -d`), in
 * Chromium. They use their own account (E2E_EMAIL / E2E_PASSWORD, defaults below), created on the first run, never
 * a real user's. No test starts a run: nothing here calls paid APIs.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    storageState: "e2e/.auth.json",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "phone", use: { ...devices["Pixel 7"] }, grep: /@phone/ },
  ],
});
