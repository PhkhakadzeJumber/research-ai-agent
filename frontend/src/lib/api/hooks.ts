/**
 * React Query hooks for the API (step 10a): one place that knows each route, its response type and when its data
 * goes stale. Pages use these instead of calling fetch themselves.
 */
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas } from "./client";

export type Me = Schemas["Me"];
export type Profile = Schemas["ProfileOut"];
export type ResearchLimits = Schemas["ResearchLimits"];

export const keys = {
  me: ["me"] as const,
  profile: ["profile"] as const,
};

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => api<Me>("/auth/me"), retry: false, staleTime: 60_000 });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["LoginRequest"]) => api<Me>("/auth/login", { method: "POST", body }),
    onSuccess: (me) => client.setQueryData(keys.me, me),
  });
}

export function useSignup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["SignupRequest"]) => api<Me>("/auth/signup", { method: "POST", body }),
    onSuccess: (me) => client.setQueryData(keys.me, me),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => client.clear(),
  });
}

export function useProfile() {
  return useQuery({ queryKey: keys.profile, queryFn: () => api<Profile>("/profile") });
}

export function useSaveProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ProfileUpdate"]) => api<Profile>("/profile", { method: "PUT", body }),
    onSuccess: (profile) => {
      client.setQueryData(keys.profile, profile);
      client.invalidateQueries({ queryKey: keys.me });
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: Schemas["PasswordChange"]) => api<void>("/auth/password", { method: "POST", body }),
  });
}

// --- investigations: starting, clarifying, watching (step 10b) --------------------------------------------------

export type Investigation = Schemas["InvestigationDetail"];
export type InvestigationSummary = Schemas["InvestigationSummary"];
export type Clarification = Schemas["Clarification"];
export type Progress = Schemas["Progress"];
export type ProgressEvent = Schemas["ProgressEventOut"];
export type NewEvaluation = Schemas["EvaluateRequest"];
export type NewDiscovery = Schemas["DiscoverRequest"];

export const investigationKeys = {
  detail: (id: string) => ["investigation", id] as const,
  clarification: (id: string) => ["investigation", id, "clarification"] as const,
  progress: (id: string) => ["investigation", id, "progress"] as const,
  activity: (id: string) => ["investigation", id, "activity"] as const,
};

/** One investigation; refreshed every few seconds while a run works on it, so the page follows the stage. */
export function useInvestigation(id: string) {
  return useQuery({
    queryKey: investigationKeys.detail(id),
    queryFn: () => api<Investigation>(`/investigations/${id}`),
    refetchInterval: (query) => (query.state.data && query.state.data.run !== "idle" ? 4_000 : false),
  });
}

export function useClarification(id: string, enabled = true) {
  return useQuery({
    queryKey: investigationKeys.clarification(id),
    queryFn: () => api<Clarification>(`/investigations/${id}/clarification`),
    enabled,
  });
}

export function useProgress(id: string, running: boolean) {
  return useQuery({
    queryKey: investigationKeys.progress(id),
    queryFn: () => api<Progress>(`/investigations/${id}/progress`),
    refetchInterval: running ? 10_000 : false,
  });
}

export function useActivity(id: string) {
  return useQuery({
    queryKey: investigationKeys.activity(id),
    queryFn: () => api<Schemas["Activity"]>(`/investigations/${id}/activity?limit=500`),
  });
}

function useRefreshInvestigation() {
  const client = useQueryClient();
  return (id: string, summary?: InvestigationSummary) => {
    if (summary) client.setQueryData(investigationKeys.detail(id), (old: Investigation | undefined) => (old ? { ...old, ...summary } : old));
    return client.invalidateQueries({ queryKey: ["investigation", id] });
  };
}

export function useCreateInvestigation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: NewEvaluation | NewDiscovery) =>
      api<InvestigationSummary>("/investigations", { method: "POST", body: body as Record<string, unknown> }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["investigations"] }),
  });
}

export function useCreateFromDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) => api<InvestigationSummary>("/investigations/document", { method: "POST", body: form }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["investigations"] }),
  });
}

export function useAnswerClarification(id: string) {
  const refresh = useRefreshInvestigation();
  return useMutation({
    mutationFn: (body: Schemas["ClarificationAnswer"]) =>
      api<InvestigationSummary>(`/investigations/${id}/clarification`, { method: "POST", body }),
    onSuccess: (summary) => refresh(id, summary),
  });
}

export function useStop(id: string) {
  const refresh = useRefreshInvestigation();
  return useMutation({
    mutationFn: () => api<InvestigationSummary>(`/investigations/${id}/stop`, { method: "POST" }),
    onSuccess: (summary) => refresh(id, summary),
  });
}

export function useResume(id: string) {
  const refresh = useRefreshInvestigation();
  return useMutation({
    mutationFn: (budget_usd?: number) =>
      api<InvestigationSummary>(`/investigations/${id}/resume`, { method: "POST", body: budget_usd ? { budget_usd } : {} }),
    onSuccess: (summary) => refresh(id, summary),
  });
}
