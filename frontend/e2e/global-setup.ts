/**
 * Before the browser tests: make sure the test accounts exist and are logged in, and save their sessions for the
 * tests (git-ignored). The accounts are the tests' own, never a real user's:
 *   - E2E (e2e/.auth.json): has run nothing, so the tests can check nobody else's work ever shows;
 *   - E2E_RUNS (e2e/.auth-runs.json): holds finished runs for the step pages, copied there for free with
 *     `python -m scripts.clone_run <id> --to e2e-runs@eureka.test`.
 */
import { request, type FullConfig } from "@playwright/test";

export const E2E = {
  email: process.env.E2E_EMAIL ?? "e2e@eureka.test",
  password: process.env.E2E_PASSWORD ?? "e2e-local-password-only",
  name: "End To End",
};

export const E2E_RUNS = {
  email: process.env.E2E_RUNS_EMAIL ?? "e2e-runs@eureka.test",
  password: process.env.E2E_PASSWORD ?? "e2e-local-password-only",
  name: "End To End Runs",
};

export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL ?? "http://localhost:3000";
  for (const [account, file] of [[E2E, "e2e/.auth.json"], [E2E_RUNS, "e2e/.auth-runs.json"]] as const) {
    const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } });
    const health = await api.get("/api/v1/auth/me").catch(() => null);
    if (!health) throw new Error(`The site isn't reachable at ${baseURL}: start it (docker compose up --build -d).`);
    let response = await api.post("/api/v1/auth/login", { data: { email: account.email, password: account.password } });
    if (response.status() === 401) {
      response = await api.post("/api/v1/auth/signup", { data: account });
    }
    if (!response.ok()) throw new Error(`Couldn't log in ${account.email}: ${response.status()} ${await response.text()}`);
    await api.storageState({ path: file });
    await api.dispose();
  }
}
