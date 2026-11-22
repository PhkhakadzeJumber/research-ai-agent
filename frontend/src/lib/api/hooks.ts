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
