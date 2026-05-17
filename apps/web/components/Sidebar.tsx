"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  label: string;
  href: string;
};

const NAV: NavItem[] = [
  { label: "Dashboard", href: "/" },
  { label: "Projects", href: "/projects" },
  { label: "Settings", href: "/settings" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden w-56 shrink-0 border-r border-gray-200 bg-white md:block">
      <div className="px-4 py-5">
        <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Workspace
        </div>
      </div>
      <nav className="flex flex-col gap-0.5 px-2 pb-4">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={
                "rounded-md px-3 py-2 text-sm transition-colors " +
                (active
                  ? "bg-brand-50 font-medium text-brand-700"
                  : "text-gray-700 hover:bg-gray-100")
              }
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-gray-100 px-4 py-3 text-xs text-gray-500">
        Upload, classify, review, and run live inside a project.
      </div>
    </aside>
  );
}
