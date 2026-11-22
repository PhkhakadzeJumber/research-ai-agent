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
  return useQuery({ queryKey: keys.me, queryFn: () => api<Me>("/auth/me"), retry: false, staleTime: 15_000 });
}

export type Billing = Schemas["Billing"];
export type Plans = Schemas["Plans"];

/** The plan, credits and credit history (Profile → Plan & billing; step P4). */
export function useBilling() {
  return useQuery({ queryKey: ["billing"], queryFn: () => api<Billing>("/billing") });
}

/** What the plans include and cost: public, for the Pricing page. */
export function usePlans() {
  return useQuery({ queryKey: ["plans"], queryFn: () => api<Plans>("/plans"), staleTime: 3_600_000 });
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
export function useInvestigation(id: string, enabled = true) {
  return useQuery({
    queryKey: investigationKeys.detail(id),
    queryFn: () => api<Investigation>(`/investigations/${id}`),
    enabled: enabled && Boolean(id),
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
    client.invalidateQueries({ queryKey: keys.me }); // credits were reserved or returned
    return client.invalidateQueries({ queryKey: ["investigation", id] });
  };
}

export function useCreateInvestigation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: NewEvaluation | NewDiscovery) =>
      api<InvestigationSummary>("/investigations", { method: "POST", body: body as Record<string, unknown> }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.me });
      return client.invalidateQueries({ queryKey: ["investigations"] });
    },
  });
}

export function useCreateFromDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) => api<InvestigationSummary>("/investigations/document", { method: "POST", body: form }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.me });
      return client.invalidateQueries({ queryKey: ["investigations"] });
    },
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

// --- results, ideas, research packages, the list (step 10c) ----------------------------------------------------

export type Results = Schemas["Results"];
export type RankedIdea = Schemas["RankedIdea"];
export type Landscape = Schemas["Landscape"];
export type CandidateDetail = Schemas["CandidateDetail"];
export type Matrix = Schemas["Matrix"];
export type ClaimOut = Schemas["ClaimOut"];
export type SkepticLog = Schemas["SkepticLog"];
export type ResearchPackage = Schemas["ResearchPackageOut"];

export function useResults(id: string, enabled = true) {
  return useQuery({ queryKey: ["investigation", id, "results"], queryFn: () => api<Results>(`/investigations/${id}/results`), enabled });
}

export function useLandscape(id: string, enabled: boolean) {
  return useQuery({ queryKey: ["investigation", id, "landscape"], queryFn: () => api<Landscape>(`/investigations/${id}/landscape`), enabled });
}

export function useInvestigations(status: string | null) {
  return useQuery({
    queryKey: ["investigations", status],
    queryFn: () => api<Schemas["Page_InvestigationSummary_"]>(`/investigations?limit=100${status ? `&status=${status}` : ""}`),
    refetchInterval: (query) => (query.state.data?.items.some((i) => i.run !== "idle") ? 8_000 : false),
  });
}

export const ideaKeys = {
  detail: (id: string) => ["idea", id] as const,
  part: (id: string, part: string) => ["idea", id, part] as const,
};

export function useIdea(id: string) {
  return useQuery({ queryKey: ideaKeys.detail(id), queryFn: () => api<CandidateDetail>(`/candidates/${id}`) });
}

export function useIdeaPart<T>(id: string, part: "matrix" | "claims" | "challenges", enabled: boolean) {
  return useQuery({ queryKey: ideaKeys.part(id, part), queryFn: () => api<T>(`/candidates/${id}/${part}`), enabled });
}

/** Whether to check for the package again: while it's being written, until it arrives (fixed after hand testing: polling
 * stopped when the run ended, so a package saved after the last check never showed and the page said "Writing" forever). */
export const packagePoll = (waiting: boolean, pkg: unknown) => (waiting && !pkg ? 5_000 : false);

/** The latest research package, or null if none was written yet (a 404 here is an answer, not an error). */
export function usePackage(id: string, waiting: boolean) {
  return useQuery({
    queryKey: ideaKeys.part(id, "proposal"),
    queryFn: async () => {
      try {
        return await api<ResearchPackage>(`/candidates/${id}/proposal`);
      } catch (error) {
        if (error instanceof Error && "status" in error && (error as { status: number }).status === 404) return null;
        throw error;
      }
    },
    refetchInterval: (query) => packagePoll(waiting, query.state.data),
  });
}

export function useWritePackage(ideaId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<InvestigationSummary>(`/candidates/${ideaId}/proposal`, { method: "POST" }),
    onSuccess: (summary) => {
      client.invalidateQueries({ queryKey: keys.me });
      client.invalidateQueries({ queryKey: ["investigation", summary.id] });
      client.invalidateQueries({ queryKey: ideaKeys.part(ideaId, "proposal") });
    },
  });
}

// --- the library (step 10d) -----------------------------------------------------------------------------------------

export type LibraryPaper = Schemas["LibraryPaper"];
export type PaperDetail = Schemas["PaperDetail"];
export type LibraryFilters = { q: string; read: boolean | null; used: boolean | null; yearFrom: number | null };

export function libraryQuery(f: LibraryFilters, offset: number, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.read !== null) params.set("read", String(f.read));
  if (f.used !== null) params.set("used", String(f.used));
  if (f.yearFrom) params.set("year_from", String(f.yearFrom));
  return `/papers?${params}`;
}

export function useLibrary(filters: LibraryFilters, pages: number) {
  return useQuery({
    queryKey: ["library", filters, pages],
    queryFn: () => api<Schemas["Page_LibraryPaper_"]>(libraryQuery(filters, 0, 20 * pages)),
    placeholderData: (previous) => previous, // keep the list on screen while a new search loads
  });
}

export function usePaper(id: string) {
  return useQuery({ queryKey: ["paper", id], queryFn: () => api<PaperDetail>(`/papers/${id}`) });
}

export function useResume(id: string) {
  const refresh = useRefreshInvestigation();
  return useMutation({
    mutationFn: (budget_usd?: number) =>
      api<InvestigationSummary>(`/investigations/${id}/resume`, { method: "POST", body: budget_usd ? { budget_usd } : {} }),
    onSuccess: (summary) => refresh(id, summary),
  });
}
