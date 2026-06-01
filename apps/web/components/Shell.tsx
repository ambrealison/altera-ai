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
          <div className="flex items-center gap-2.5">
            <Link href="/" className="flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-forest-hero text-sm font-bold text-white shadow-soft">
                A
              </span>
              <span className="text-[15px] font-semibold tracking-tight text-forest-900">
                Altera AI
              </span>
            </Link>
            <span className="hidden rounded-full bg-mint-100 px-2.5 py-0.5 text-[11px] font-medium text-brand-700 ring-1 ring-brand-200 sm:inline">
              Climate intelligence
            </span>
          </div>
          <UserMenu />
        </header>
        <main className="flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}
