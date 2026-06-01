import { redirect } from "next/navigation";

/**
 * Phase Product-UX-A — the standalone Dashboard was removed (it
 * duplicated the projects list without adding value). The landing
 * route now redirects to the projects workspace, which is the real
 * starting point for a retailer operator.
 */
export default function RootIndexPage() {
  redirect("/projects");
}
