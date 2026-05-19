"use client";

/**
 * Supabase Auth callback handler.
 *
 * Supabase redirects here after:
 *  - Invite email link  (#type=invite)
 *  - Password recovery  (#type=recovery)
 *  - OAuth sign-in      (no type, or type=signup)
 *
 * With detectSessionInUrl: true the Supabase client processes the URL
 * hash automatically and fires onAuthStateChange. This page listens for
 * that event and redirects accordingly.
 */

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { getSupabaseClient } from "@/lib/supabase";

export default function AuthCallbackPage() {
  return (
    <Suspense>
      <CallbackInner />
    </Suspense>
  );
}

function CallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!supabase) {
      router.replace("/projects");
      return;
    }

    // Detect token type from the URL hash (e.g. #type=invite&access_token=…)
    const hash = window.location.hash;
    const hashParams = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
    const tokenType = hashParams.get("type");
    const needsPassword = tokenType === "recovery" || tokenType === "invite";

    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session) return;
      data.subscription.unsubscribe();
      if (needsPassword) {
        router.replace("/reset-password");
      } else {
        const next = searchParams.get("next") ?? "/projects";
        router.replace(next);
      }
    });

    // Handle the case where the session was already established before
    // the listener was registered (e.g. fast tab restore).
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        data.subscription.unsubscribe();
        if (needsPassword) {
          router.replace("/reset-password");
        } else {
          const next = searchParams.get("next") ?? "/projects";
          router.replace(next);
        }
      }
    });

    return () => {
      data.subscription.unsubscribe();
    };
  }, [router, searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-sm text-gray-500">Signing you in…</p>
    </div>
  );
}
