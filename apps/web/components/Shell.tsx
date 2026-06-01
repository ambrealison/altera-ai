import Link from "next/link";
import { Sidebar } from "@/components/Sidebar";
import { UserMenu } from "@/components/UserMenu";

/**
 * Phase Design-A — premium app shell. Sticky translucent topbar with
 * a wordmark lockup, a refined sidebar, and a roomy content area. The
 * page gradient is painted by ``globals.css`` at the body level; the
 * shell keeps its chrome on translucent surfaces above it.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-line/70 bg-white/70 px-6 backdrop-blur-md">
          <Link
            href="/"
            className="flex items-baseline transition-opacity hover:opacity-80"
            aria-label="Altera.ai — accueil"
          >
            <span className="text-2xl font-black italic tracking-tight text-forest-900">
              Altera
            </span>
            <span className="ml-0.5 text-xl font-bold text-brand-600">
              .ai
            </span>
          </Link>
          <UserMenu />
        </header>
        <main className="flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}
