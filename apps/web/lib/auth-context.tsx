"use client";

/**
 * React context that holds the current Supabase session + a derived
 * "current user" loaded from the backend's /api/v1/me.
 *
 * Two modes:
 *
 * 1. Supabase Auth mode (NEXT_PUBLIC_SUPABASE_URL set):
 *    - The provider subscribes to Supabase auth-state changes.
 *    - On every state change it refetches /me to get role + org.
 *    - useAccessToken() returns the live access token for fetch calls.
 *
 * 2. Dev auth mode (Supabase not configured):
 *    - The provider does NOT mount Supabase.
 *    - useAccessToken() returns null; the backend dev fallback
 *      handles "no token" by reading ALTERA_DEV_AUTH_ENABLED.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import type { Session } from "@supabase/supabase-js";
import { getSupabaseClient, isSupabaseConfigured } from "@/lib/supabase";

export interface CurrentUser {
  user_id: string;
  email: string;
  organisation_id: string;
  role: string;
  organisation_type: "gms_client" | "altera_internal";
  auth_provider: "supabase" | "dev";
  is_dev_auth: boolean;
}

interface AuthContextValue {
  /** True while session is being established (initial load). */
  loading: boolean;
  /** Supabase session, or null in dev mode / signed out. */
  session: Session | null;
  /** Backend-loaded current user (role + org). null while loading or unauthenticated. */
  currentUser: CurrentUser | null;
  /** The access token to attach to API calls. null in dev mode. */
  accessToken: string | null;
  /** True iff the app is running with the dev auth fallback. */
  isDevMode: boolean;
  /** True iff the current user belongs to the Altera internal organisation. */
  isAltera: boolean;
  /**
   * Sign in with email/password. Resolves only after the session AND
   * the backend-loaded `currentUser` have been written into context,
   * so callers can navigate without racing the async auth-state
   * listener.
   */
  signIn: (email: string, password: string) => Promise<{ error: Error | null }>;
  /** Sign out and clear local state. */
  signOut: () => Promise<void>;
  /** Refresh /me — call after server-side state changes. */
  refreshCurrentUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

/**
 * Phase 34W — distinguish real auth failure from transient API
 * outage. The previous implementation returned null for ANY failure
 * (network blip, 502, 503, real 401), which then nullified
 * ``currentUser`` and made the wizard appear to log the user out
 * during a Render restart caused by a 60-second 1050-row upload.
 *
 * Returns:
 *  - ``{ kind: "ok", user }`` — /me returned the user.
 *  - ``{ kind: "unauthenticated" }`` — server returned 401/403; the
 *    caller SHOULD clear local state and route to login.
 *  - ``{ kind: "transient" }`` — network error or 5xx; the caller
 *    SHOULD preserve the last known currentUser and retry.
 */
type FetchMeResult =
  | { kind: "ok"; user: CurrentUser }
  | { kind: "unauthenticated" }
  | { kind: "transient" };

async function fetchCurrentUser(token: string | null): Promise<FetchMeResult> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/me`, {
      headers,
      cache: "no-store",
      credentials: "omit",
    });
    if (res.ok) {
      return { kind: "ok", user: (await res.json()) as CurrentUser };
    }
    if (res.status === 401 || res.status === 403) {
      return { kind: "unauthenticated" };
    }
    // 502 / 503 / 504 from Render during restart, or 500 from a
    // serialization failure on a separate request — DO NOT log the
    // user out; just signal transient.
    return { kind: "transient" };
  } catch {
    // Network error (DNS, TLS, connection refused) — same treatment
    // as a 5xx: transient, keep state.
    return { kind: "transient" };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const supabaseConfigured = isSupabaseConfigured();
  const [session, setSession] = useState<Session | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const accessToken = session?.access_token ?? null;

  // Phase 34W — only clear currentUser on real auth failure. A
  // transient outage (502 during Render restart, network blip) keeps
  // the last known user in state so the workspace doesn't appear to
  // log out.
  const refreshCurrentUser = useCallback(async () => {
    const result = await fetchCurrentUser(accessToken);
    if (result.kind === "ok") setCurrentUser(result.user);
    else if (result.kind === "unauthenticated") setCurrentUser(null);
    // result.kind === "transient" → keep previous currentUser.
  }, [accessToken]);

  useEffect(() => {
    let active = true;

    // Phase 34W — collapse the three FetchMeResult cases at each
    // call site. ``transient`` keeps the previous state (which is
    // null at bootstrap; that's fine — the next refresh will retry).
    function applyResult(result: FetchMeResult) {
      if (result.kind === "ok") setCurrentUser(result.user);
      else if (result.kind === "unauthenticated") setCurrentUser(null);
    }

    async function bootstrap() {
      const client = getSupabaseClient();
      if (!client) {
        // Dev-mode bootstrap: just probe /me once.
        const r = await fetchCurrentUser(null);
        if (!active) return;
        applyResult(r);
        setLoading(false);
        return;
      }

      // Supabase mode: read current session + subscribe.
      const { data } = await client.auth.getSession();
      if (!active) return;
      setSession(data.session);
      const r = await fetchCurrentUser(data.session?.access_token ?? null);
      if (!active) return;
      applyResult(r);
      setLoading(false);

      const { data: sub } = client.auth.onAuthStateChange(async (_evt, next) => {
        if (!active) return;
        setSession(next);
        const r2 = await fetchCurrentUser(next?.access_token ?? null);
        if (active) applyResult(r2);
      });
      return () => sub.subscription.unsubscribe();
    }

    let unsub: (() => void) | undefined;
    bootstrap().then((cleanup) => {
      if (cleanup) unsub = cleanup;
    });
    return () => {
      active = false;
      unsub?.();
    };
    // We want to bootstrap exactly once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signIn = useCallback(
    async (email: string, password: string): Promise<{ error: Error | null }> => {
      const client = getSupabaseClient();
      if (!client) {
        return { error: new Error("Supabase is not configured.") };
      }
      const { data, error } = await client.auth.signInWithPassword({ email, password });
      if (error) return { error };

      // Synchronously hydrate context state so callers can navigate
      // without racing the async onAuthStateChange listener.
      setSession(data.session);
      const r = await fetchCurrentUser(data.session?.access_token ?? null);
      if (r.kind === "ok") {
        setCurrentUser(r.user);
        return { error: null };
      }
      if (r.kind === "unauthenticated") {
        setCurrentUser(null);
        return {
          error: new Error(
            "Sign-in succeeded but the backend did not accept the session.",
          ),
        };
      }
      // Phase 34W — transient (5xx / network). Don't kick the user
      // out of the just-completed flow; surface a retry-friendly
      // message so the UI doesn't claim auth failure.
      return {
        error: new Error(
          "Sign-in succeeded but the server is temporarily unavailable. Reload in a few seconds.",
        ),
      };
    },
    [],
  );

  const signOut = useCallback(async () => {
    const client = getSupabaseClient();
    if (client) {
      await client.auth.signOut();
    }
    setSession(null);
    setCurrentUser(null);
  }, []);

  const value: AuthContextValue = {
    loading,
    session,
    currentUser,
    accessToken,
    isDevMode: !supabaseConfigured,
    isAltera: currentUser?.organisation_type === "altera_internal",
    signIn,
    signOut,
    refreshCurrentUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
