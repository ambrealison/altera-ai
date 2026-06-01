/**
 * Phase Product-UX-E — leftover Projects-surface labels not covered by
 * the base dictionary (resilience / error / offline copy).
 */
import type { I18nDict } from "./types";

export const projectsExtra: I18nDict = {
  "projects.partialLoad": { fr: "Chargement partiel", en: "Partial load" },
  "projects.error.unreachable": {
    fr: "Impossible de joindre le serveur. Réessayez.",
    en: "Could not reach the server. Please retry.",
  },
  "projects.error.loadFailed": {
    fr: "Le chargement des projets a échoué.",
    en: "Failed to load projects.",
  },
  "projects.error.offlineHint": {
    fr: "Les projets seront affichés une fois la connexion rétablie.",
    en: "Projects will appear once the connection is restored.",
  },
  "projects.guidedWorkflow": { fr: "Parcours guidé", en: "Guided workflow" },
};
