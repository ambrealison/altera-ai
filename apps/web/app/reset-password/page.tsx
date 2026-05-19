"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabaseClient } from "@/lib/supabase";
import { Button, Card, CardHeader, Field } from "@/components/ui";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) {
      setError("Supabase is not configured.");
      return;
    }
    setBusy(true);
    setError(null);
    const { error: supaErr } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (supaErr) {
      setError(supaErr.message);
      return;
    }
    router.replace("/projects");
  }

  return (
    <div className="mx-auto mt-16 max-w-md p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Set your password</h1>
      <p className="mt-1 text-sm text-gray-600">
        Choose a password for your Altera AI account.
      </p>
      <div className="mt-6">
        <Card>
          <CardHeader title="Create password" />
          <form onSubmit={handleSubmit} className="mt-4 space-y-4">
            <Field label="New password">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
                placeholder="Min. 8 characters"
                className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />
            </Field>
            <Field label="Confirm password">
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
                className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />
            </Field>
            {error && (
              <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                {error}
              </div>
            )}
            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : "Set password"}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
