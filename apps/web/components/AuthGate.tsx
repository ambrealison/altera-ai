"use client";

/**
 * Client-side route guard + shell selector.
 *
 * - `/login` (and other public paths) render their own page chrome.
 * - Every other path renders inside the app `<Shell>` and requires
 *   an authenticated user. While auth is loading we render a
 *   placeholder; if the user is signed out we redirect to /login.
 */

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Shell } from "@/components/Shell";

const PUBLIC_PATHS = new Set(["/login"]);

export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { loading, currentUser } = useAuth();

  const isPublic = pathname ? PUBLIC_PATHS.has(pathname) : false;

  useEffect(() => {
    if (loading) return;
    if (isPublic) return;
    if (!currentUser) {
      const next = pathname ? encodeURIComponent(pathname) : "/";
      router.replace(`/login?next=${next}`);
    }
  }, [loading, currentUser, isPublic, pathname, router]);

  if (isPublic) return <>{children}</>;
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-gray-500">
        Loading…
      </div>
    );
  }
  if (!currentUser) {
    // useEffect will redirect; render nothing to avoid flicker.
    return null;
  }
  return <Shell>{children}</Shell>;
}
