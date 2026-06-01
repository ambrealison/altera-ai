"use client";

import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export function UserMenu() {
  const router = useRouter();
  const { currentUser, isDevMode, signOut } = useAuth();
  if (!currentUser) return null;

  async function onSignOut() {
    await signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-3">
      <div className="hidden text-right text-xs sm:block">
        <div className="font-medium text-forest-700">{currentUser.email}</div>
        <div className="text-[10px] uppercase tracking-wider text-ink-soft">
          {currentUser.role} · org {currentUser.organisation_id.slice(0, 8)}…
          {currentUser.is_dev_auth && (
            <span className="ml-1 text-warn-700">· dev</span>
          )}
        </div>
      </div>
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-mint-100 text-xs font-semibold text-brand-700 ring-1 ring-brand-200">
        {(currentUser.email?.[0] ?? "?").toUpperCase()}
      </span>
      {!isDevMode && (
        <button
          onClick={onSignOut}
          className="rounded-lg border border-line bg-white px-2.5 py-1 text-xs font-medium text-forest-700 transition-colors hover:bg-mint-50"
        >
          Déconnexion
        </button>
      )}
    </div>
  );
}
