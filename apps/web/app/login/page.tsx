"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getSupabaseClient, isSupabaseConfigured } from "@/lib/supabase";
import { useAuth } from "@/lib/auth-context";
import { Button, Card, CardHeader, Field } from "@/components/ui";

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") ?? "/";
  const supabaseConfigured = isSupabaseConfigured();
  const { signIn } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forgotMode, setForgotMode] = useState(false);
  const [resetSent, setResetSent] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabaseConfigured) {
      setError(
        "Supabase is not configured. Either set NEXT_PUBLIC_SUPABASE_URL + " +
          "NEXT_PUBLIC_SUPABASE_ANON_KEY and rebuild, or run the backend " +
          "with ALTERA_DEV_AUTH_ENABLED=true and skip this page.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    const { error: signInError } = await signIn(email, password);
    if (signInError) {
      setError(signInError.message);
      setBusy(false);
      return;
    }
    router.replace(next);
    router.refresh();
  }

  async function onForgotPassword(e: React.FormEvent) {
    e.preventDefault();
    if (!supabaseConfigured) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    // redirectTo must be registered in Supabase dashboard → Auth → URL Configuration
    const { error: resetErr } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback`,
    });
    setBusy(false);
    if (resetErr) {
      setError(resetErr.message);
      return;
    }
    setResetSent(true);
  }

  return (
    <div className="mx-auto mt-16 max-w-md p-6">
      <h1 className="text-2xl font-semibold tracking-tight">
        {forgotMode ? "Reset password" : "Sign in"}
      </h1>
      <p className="mt-1 text-sm text-gray-600">
        {forgotMode
          ? "Enter your email and we'll send a reset link."
          : "Email + password sign-in via Supabase Auth."}
      </p>

      {!supabaseConfigured && (
        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
          Supabase is not configured for this build. If you are developing
          locally, the backend may be running with{" "}
          <code>ALTERA_DEV_AUTH_ENABLED=true</code> — in that case the dashboard
          works without a sign-in step.
        </div>
      )}

      <div className="mt-6">
        {forgotMode ? (
          <Card>
            <CardHeader title="Send reset link" />
            {resetSent ? (
              <div className="mt-4 space-y-3">
                <p className="text-sm text-green-700">
                  Reset link sent to <strong>{email}</strong>. Check your inbox.
                </p>
                <button
                  onClick={() => { setForgotMode(false); setResetSent(false); }}
                  className="text-xs text-brand-600 hover:underline"
                >
                  Back to sign in
                </button>
              </div>
            ) : (
              <form onSubmit={onForgotPassword} className="mt-4 space-y-4">
                <Field label="Email">
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoComplete="email"
                    className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                  />
                </Field>
                {error && (
                  <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                    {error}
                  </div>
                )}
                <div className="flex items-center gap-3">
                  <Button type="submit" disabled={busy || !supabaseConfigured}>
                    {busy ? "Sending…" : "Send reset link"}
                  </Button>
                  <button
                    type="button"
                    onClick={() => setForgotMode(false)}
                    className="text-xs text-gray-500 hover:underline"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </Card>
        ) : (
          <Card>
            <CardHeader title="Sign in to Altera AI" />
            <form onSubmit={onSubmit} className="mt-4 space-y-4">
              <Field label="Email">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                />
              </Field>
              <Field label="Password">
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                />
              </Field>
              {error && (
                <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                  {error}
                </div>
              )}
              <div className="flex items-center justify-between">
                <Button type="submit" disabled={busy || !supabaseConfigured}>
                  {busy ? "Signing in…" : "Sign in"}
                </Button>
                {supabaseConfigured && (
                  <button
                    type="button"
                    onClick={() => { setForgotMode(true); setError(null); }}
                    className="text-xs text-gray-500 hover:underline"
                  >
                    Forgot password?
                  </button>
                )}
              </div>
            </form>
          </Card>
        )}
      </div>
    </div>
  );
}
