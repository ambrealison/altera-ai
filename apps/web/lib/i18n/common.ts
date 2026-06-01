/**
 * Phase Product-UX-E — shared/common UI labels reused across surfaces.
 * Verbs, statuses, and generic nouns live here so every surface uses
 * the same wording (and the same key).
 */
import type { I18nDict } from "./types";

export const common: I18nDict = {
  // Actions / verbs
  "common.retry": { fr: "Réessayer", en: "Retry" },
  "common.cancel": { fr: "Annuler", en: "Cancel" },
  "common.save": { fr: "Enregistrer", en: "Save" },
  "common.saving": { fr: "Enregistrement…", en: "Saving…" },
  "common.next": { fr: "Suivant", en: "Next" },
  "common.previous": { fr: "Précédent", en: "Previous" },
  "common.continue": { fr: "Continuer", en: "Continue" },
  "common.close": { fr: "Fermer", en: "Close" },
  "common.confirm": { fr: "Confirmer", en: "Confirm" },
  "common.edit": { fr: "Modifier", en: "Edit" },
  "common.delete": { fr: "Supprimer", en: "Delete" },
  "common.download": { fr: "Télécharger", en: "Download" },
  "common.search": { fr: "Rechercher", en: "Search" },
  "common.apply": { fr: "Appliquer", en: "Apply" },
  "common.open": { fr: "Ouvrir", en: "Open" },
  "common.back": { fr: "Retour", en: "Back" },
  "common.viewAll": { fr: "Tout voir", en: "View all" },
  "common.reset": { fr: "Réinitialiser", en: "Reset" },

  // Statuses
  "common.loading": { fr: "Chargement…", en: "Loading…" },
  "common.inProgress": { fr: "En cours", en: "In progress" },
  "common.done": { fr: "Terminé", en: "Done" },
  "common.error": { fr: "Erreur", en: "Error" },
  "common.pending": { fr: "En attente", en: "Pending" },
  "common.completed": { fr: "Complété", en: "Completed" },
  "common.notRequired": { fr: "Non requis", en: "Not required" },
  "common.required": { fr: "Requis", en: "Required" },
  "common.optional": { fr: "Optionnel", en: "Optional" },
  "common.toReview": { fr: "À vérifier", en: "To review" },
  "common.refreshing": { fr: "Mise à jour…", en: "Refreshing…" },

  // Generic nouns
  "common.products": { fr: "Produits", en: "Products" },
  "common.rows": { fr: "Lignes", en: "Rows" },
  "common.file": { fr: "Fichier", en: "File" },
  "common.fields": { fr: "Champs", en: "Fields" },
  "common.category": { fr: "Catégorie", en: "Category" },
  "common.methodology": { fr: "Méthodologie", en: "Methodology" },
  "common.none": { fr: "Aucun", en: "None" },
  "common.all": { fr: "Tous", en: "All" },
  "common.yes": { fr: "Oui", en: "Yes" },
  "common.no": { fr: "Non", en: "No" },
};
