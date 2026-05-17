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

async function fetchCurrentUser(token: string | null): Promise<CurrentUser | null> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/me`, {
      headers,
      cache: "no-store",
      credentials: "omit",
    });
    if (!res.ok) return null;
    return (await res.json()) as CurrentUser;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const supabaseConfigured = isSupabaseConfigured();
  const [session, setSession] = useState<Session | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const accessToken = session?.access_token ?? null;

  const refreshCurrentUser = useCallback(async () => {
    const me = await fetchCurrentUser(accessToken);
    setCurrentUser(me);
  }, [accessToken]);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      const client = getSupabaseClient();
      if (!client) {
        // Dev-mode bootstrap: just probe /me once.
        const me = await fetchCurrentUser(null);
        if (!active) return;
        setCurrentUser(me);
        setLoading(false);
        return;
      }

      // Supabase mode: read current session + subscribe.
      const { data } = await client.auth.getSession();
      if (!active) return;
      setSession(data.session);
      const me = await fetchCurrentUser(data.session?.access_token ?? null);
      if (!active) return;
      setCurrentUser(me);
      setLoading(false);

      const { data: sub } = client.auth.onAuthStateChange(async (_evt, next) => {
        if (!active) return;
        setSession(next);
        const me2 = await fetchCurrentUser(next?.access_token ?? null);
        if (active) setCurrentUser(me2);
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
