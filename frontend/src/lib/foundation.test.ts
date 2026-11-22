/** Step 10a: the small pieces of logic the foundation relies on. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { cleanLimits } from "@/app/(app)/profile/page";
import { safeNext } from "@/components/auth-screen";
import { initials } from "@/components/top-nav";
import { api, ApiError, isUnauthorized } from "@/lib/api/client";

describe("after logging in", () => {
  it("goes back only to a page on this site", () => {
    expect(safeNext("/investigations/42")).toBe("/investigations/42");
    expect(safeNext(null)).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("//evil.example/x")).toBe("/");
  });
});

describe("the avatar", () => {
  it("shows the initials of the name, else of the email", () => {
    expect(initials("Jumber Phkhakadze", "j@x.ge")).toBe("JP");
    expect(initials("Ana  Maria Beridze", "a@x.ge")).toBe("AB");
    expect(initials("Ana", "a@x.ge")).toBe("AN");
    expect(initials(null, "jp@x.ge")).toBe("JP");
  });
});

describe("the research limits form", () => {
  it("sends only what was filled in", () => {
    expect(
      cleanLimits({
        academic_level: "undergraduate", weeks_available: 12, hours_per_week: null, compute: null,
        compute_note: "  ", data_access: "public_only", budget: null, skills: " Python ", technologies: "",
        ethics: null, supervision: null, other: null,
      }),
    ).toEqual({ academic_level: "undergraduate", weeks_available: 12, data_access: "public_only", skills: "Python" });
  });
});

describe("the API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  const respond = (status: number, body: unknown, headers: Record<string, string> = {}) =>
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), {
      status, headers: { "content-type": "application/json", ...headers },
    })));

  it("returns the JSON of a good answer and sends JSON bodies", async () => {
    respond(200, { email: "a@b.c" });
    await expect(api("/auth/login", { method: "POST", body: { email: "a@b.c" } })).resolves.toEqual({ email: "a@b.c" });
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/auth/login");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(init.credentials).toBe("same-origin");
  });

  it("turns the API's error shape into an ApiError", async () => {
    respond(401, { error: { code: "not_logged_in", message: "Log in to continue.", request_id: "abc" } }, { "X-Request-ID": "abc" });
    const error = (await api("/auth/me").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.code, error.message, error.requestId]).toEqual([401, "not_logged_in", "Log in to continue.", "abc"]);
    expect(isUnauthorized(error)).toBe(true);
  });

  it("explains a server that isn't answering", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad Gateway", { status: 502 })));
    const error = (await api("/auth/me").catch((e: unknown) => e)) as ApiError;
    expect(error.message).toMatch(/isn't answering/);
  });
});
