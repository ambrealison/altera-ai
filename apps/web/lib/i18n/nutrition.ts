/**
 * Phase Product-UX-E — nutrition surface labels.
 * Populated during the EN translation audit. UI labels only.
 */
import type { I18nDict } from "./types";

export const nutrition: I18nDict = {
  // Status labels (keyed by backend status code; codes unchanged)
  "nutrition.status.ready": {
    fr: "Prêt — haute confiance",
    en: "Ready — high confidence",
  },
  "nutrition.status.ready_medium_confidence": {
    fr: "Prêt — confiance moyenne",
    en: "Ready — medium confidence",
  },
  "nutrition.status.needs_review": {
    fr: "À vérifier",
    en: "Needs review",
  },
  "nutrition.status.needs_review_low_confidence": {
    fr: "À vérifier — confiance faible",
    en: "Needs review — low confidence",
  },
  "nutrition.status.suggested_very_low_confidence": {
    fr: "Suggéré — confiance très faible",
    en: "Suggested — very low confidence",
  },
  "nutrition.status.missing": { fr: "Manquant", en: "Missing" },
  "nutrition.status.excluded": { fr: "Exclu", en: "Excluded" },

  // Source labels (keyed by backend source code; codes unchanged)
  "nutrition.source.retailer_csv": {
    fr: "CSV retailer",
    en: "Retailer CSV",
  },
  "nutrition.source.nevo": { fr: "NEVO", en: "NEVO" },
  "nutrition.source.ciqual": { fr: "CIQUAL", en: "CIQUAL" },
  "nutrition.source.manual": { fr: "Manuel", en: "Manual" },
  "nutrition.source.missing": { fr: "Manquant", en: "Missing" },

  // Errors
  "nutrition.error.load": {
    fr: "Échec du chargement du tableau nutrition.",
    en: "Failed to load the nutrition table.",
  },
  "nutrition.error.save": {
    fr: "Échec de l’enregistrement.",
    en: "Failed to save.",
  },
  "nutrition.error.numeric": {
    fr: "Les trois valeurs doivent être numériques.",
    en: "All three values must be numeric.",
  },
  "nutrition.error.positive": {
    fr: "Les valeurs doivent être positives.",
    en: "Values must be positive.",
  },

  // Counters
  "nutrition.productCount": {
    fr: "{n} produit(s)",
    en: "{n} product(s)",
  },

  // Filters
  "nutrition.filter.searchPlaceholder": {
    fr: "Rechercher (nom de produit)",
    en: "Search (product name)",
  },
  "nutrition.filter.allStatuses": {
    fr: "Tous statuts",
    en: "All statuses",
  },
  "nutrition.filter.statusReady": { fr: "Prêt", en: "Ready" },
  "nutrition.filter.allSources": {
    fr: "Toutes sources",
    en: "All sources",
  },

  // Table headers
  "nutrition.col.product": { fr: "Produit", en: "Product" },
  "nutrition.col.pt": { fr: "PT", en: "PT" },
  "nutrition.col.protein": { fr: "Protéine", en: "Protein" },
  "nutrition.col.plant": { fr: "Végétal", en: "Plant" },
  "nutrition.col.animal": { fr: "Animal", en: "Animal" },
  "nutrition.col.source": { fr: "Source", en: "Source" },
  "nutrition.col.status": { fr: "Statut", en: "Status" },
  "nutrition.col.action": { fr: "Action", en: "Action" },

  // Empty state
  "nutrition.empty": {
    fr: "Aucun produit ne correspond aux filtres",
    en: "No product matches the filters",
  },

  // Edit actions
  "nutrition.saveEdit": {
    fr: "✓ Enregistrer",
    en: "✓ Save",
  },

  // Pagination
  "nutrition.page": { fr: "Page", en: "Page" },
};
