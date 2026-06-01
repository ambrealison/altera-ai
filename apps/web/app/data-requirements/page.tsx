import { redirect } from "next/navigation";

/**
 * Phase Product-UX-A — "Data Requirements" was renamed/replaced by
 * "Templates". This route redirects so any existing bookmarks or
 * links keep working.
 */
export default function DataRequirementsRedirect() {
  redirect("/templates");
}
