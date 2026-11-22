/**
 * Before the browser tests: make sure the test account exists and is logged in, and save its session for the tests
 * (e2e/.auth.json, git-ignored). The account is the tests' own (E2E_EMAIL), never a real user's.
 */
import { request, type FullConfig } from "@playwright/test";

export const E2E = {
  email: process.env.E2E_EMAIL ?? "e2e@eureka.test",
  password: process.env.E2E_PASSWORD ?? "e2e-local-password-only",
  name: "End To End",
};

export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL ?? "http://localhost:3000";
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } });
  const health = await api.get("/api/v1/auth/me").catch(() => null);
  if (!health) throw new Error(`The site isn't reachable at ${baseURL}: start it (docker compose up --build -d).`);

  let response = await api.post("/api/v1/auth/login", { data: { email: E2E.email, password: E2E.password } });
  if (response.status() === 401) {
    response = await api.post("/api/v1/auth/signup", { data: E2E });
  }
  if (!response.ok()) throw new Error(`Couldn't log in the test account: ${response.status()} ${await response.text()}`);
  await api.storageState({ path: "e2e/.auth.json" });
  await api.dispose();
}
