/**
 * Browser-side Supabase client.
 *
 * Sessions are stored in localStorage (Supabase default). Server
 * components don't see the session at all in Phase 13C; every page
 * that needs data is a client component that reads the session via
 * the `AuthProvider` from `./auth-context.tsx`.
 *
 * Phase 13B (Postgres-backed store) and Phase 13D (storage) layer on
 * top of this client — no architectural change is needed there.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

const _supabaseKey =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export function getSupabaseClient(): SupabaseClient | null {
  if (_client) return _client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!url || !_supabaseKey) {
    // Allow the app to render without Supabase configured — useful
    // for the dev-auth-only path during local development.
    return null;
  }
  _client = createClient(url, _supabaseKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
  return _client;
}

export function isSupabaseConfigured(): boolean {
  return !!process.env.NEXT_PUBLIC_SUPABASE_URL && !!_supabaseKey;
}
