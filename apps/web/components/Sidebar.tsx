"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

type NavItem = {
  label: string;
  href: string;
};

const BASE_NAV: NavItem[] = [
  { label: "Dashboard", href: "/" },
  { label: "Projects", href: "/projects" },
  { label: "Data Requirements", href: "/data-requirements" },
  { label: "Settings", href: "/settings" },
];

const ALTERA_NAV: NavItem[] = [{ label: "Admin", href: "/admin" }];

export function Sidebar() {
  const pathname = usePathname();
  const { isAltera } = useAuth();

  const nav = isAltera ? [...BASE_NAV, ...ALTERA_NAV] : BASE_NAV;

  return (
    <aside className="hidden w-60 shrink-0 border-r border-line/70 bg-white/60 backdrop-blur-md md:flex md:flex-col">
      <div className="px-5 py-5">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-soft">
          Workspace
        </div>
      </div>
      <nav className="flex flex-1 flex-col gap-1 px-3 pb-4">
        {nav.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={
                "group flex items-center gap-2 rounded-xl px-3 py-2 text-sm transition-all duration-150 " +
                (active
                  ? "bg-mint-100 font-semibold text-brand-700 shadow-soft ring-1 ring-brand-200"
                  : "text-forest-700 hover:bg-mint-50")
              }
            >
              <span
                className={
                  "h-1.5 w-1.5 rounded-full transition-colors " +
                  (active
                    ? "bg-brand-500"
                    : "bg-line group-hover:bg-brand-300")
                }
              />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="m-3 rounded-2xl bg-lime-soft p-4 text-xs leading-relaxed text-forest-700 ring-1 ring-brand-100">
        <span className="font-semibold text-forest-900">Parcours guidé</span>
        <p className="mt-1 text-ink-muted">
          Importez, classez, validez et calculez vos ratios directement
          dans un projet.
        </p>
      </div>
    </aside>
  );
}
