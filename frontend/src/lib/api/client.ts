/**
 * The Eureka API client (step 10a). Every request goes to /api/v1 on this site; Next forwards it to the backend
 * (next.config.ts), so the login cookie is first-party and sent automatically.
 *
 * Errors have the backend's one shape ({"error": {code, message, request_id}}) and become an ApiError, so pages can
 * show the message and, if the user reports it, the request ID.
 */
import type { components } from "./schema";

export type Schemas = components["schemas"];

export const API = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Body = Record<string, unknown> | unknown[] | FormData | undefined;

export async function api<T>(path: string, init: { method?: string; body?: Body; signal?: AbortSignal } = {}) {
  const { method = "GET", body, signal } = init;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const response = await fetch(`${API}${path}`, {
    method,
    signal,
    credentials: "same-origin",
    headers: body && !isForm ? { "Content-Type": "application/json" } : undefined,
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (!response.ok) {
    let code = "error";
    let message = `Request failed (${response.status}).`;
    try {
      const data = await response.json();
      code = data?.error?.code ?? code;
      message = data?.error?.message ?? message;
    } catch {
      // not JSON (e.g. the backend isn't reachable): keep the generic message
    }
    if (response.status >= 500 && code === "error") {
      message = "The Eureka server isn't answering. Is the backend running?";
    }
    throw new ApiError(response.status, code, message, response.headers.get("X-Request-ID"));
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.text()) as Promise<T>;
}

export const isUnauthorized = (error: unknown) => error instanceof ApiError && error.status === 401;
