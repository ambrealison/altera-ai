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
      <div className="text-right text-xs">
        <div className="font-medium text-gray-700">{currentUser.email}</div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500">
          {currentUser.role} · org {currentUser.organisation_id.slice(0, 8)}…
          {currentUser.is_dev_auth && (
            <span className="ml-1 text-amber-700">· dev</span>
          )}
        </div>
      </div>
      {!isDevMode && (
        <button
          onClick={onSignOut}
          className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          Sign out
        </button>
      )}
    </div>
  );
}
