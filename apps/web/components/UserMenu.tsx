"use client";

import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";

export function UserMenu() {
  const router = useRouter();
  const { currentUser, isDevMode, signOut } = useAuth();
  const { lang, setLang, t } = useI18n();

  async function onSignOut() {
    await signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-3">
      {/* Phase Product-UX-A — internal FR/EN switch. Translates UI
          labels only; never touches API payloads, mapping canonicals
          or CSV parsing. Always visible (even before sign-in). */}
      <div className="inline-flex items-center rounded-lg border border-line bg-white/70 p-0.5 text-[11px] font-semibold">
        <button
          type="button"
          onClick={() => setLang("fr")}
          aria-pressed={lang === "fr"}
          className={
            "rounded-md px-1.5 py-0.5 transition-colors " +
            (lang === "fr"
              ? "bg-brand-600 text-white"
              : "text-ink-muted hover:bg-mint-100")
          }
        >
          FR
        </button>
        <button
          type="button"
          onClick={() => setLang("en")}
          aria-pressed={lang === "en"}
          className={
            "rounded-md px-1.5 py-0.5 transition-colors " +
            (lang === "en"
              ? "bg-brand-600 text-white"
              : "text-ink-muted hover:bg-mint-100")
          }
        >
          EN
        </button>
      </div>

      {currentUser && (
        <>
          <div className="hidden text-right text-xs sm:block">
            <div className="font-medium text-forest-700">
              {currentUser.email}
            </div>
            <div className="text-[10px] uppercase tracking-wider text-ink-soft">
              {currentUser.role} · org{" "}
              {currentUser.organisation_id.slice(0, 8)}…
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
              {t("account.signout")}
            </button>
          )}
        </>
      )}
    </div>
  );
}
