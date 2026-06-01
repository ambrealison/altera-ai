/**
 * Phase Product-UX-E — review surface labels.
 * Populated during the EN translation audit. UI labels only.
 */
import type { I18nDict } from "./types";

export const review: I18nDict = {
  // Protein Tracker group display labels (CODE -> label; codes unchanged).
  "review.ptGroup.plant_based_core": {
    fr: "Végétal — cœur",
    en: "Plant-based — core",
  },
  "review.ptGroup.plant_based_non_core": {
    fr: "Végétal — hors cœur",
    en: "Plant-based — non-core",
  },
  "review.ptGroup.composite_products": {
    fr: "Composite",
    en: "Composite",
  },
  "review.ptGroup.animal_core": {
    fr: "Animal — cœur",
    en: "Animal — core",
  },
  "review.ptGroup.out_of_scope": {
    fr: "Hors périmètre",
    en: "Out of scope",
  },
  "review.ptGroup.unknown": {
    fr: "Inconnu",
    en: "Unknown",
  },
  "review.noSuggestion": {
    fr: "Aucune suggestion",
    en: "No suggestion",
  },

  // Review reason display labels (CODE -> label; codes unchanged).
  "review.reason.low_confidence": {
    fr: "Faible confiance IA",
    en: "Low AI confidence",
  },
  "review.reason.ai_parse_failed": {
    fr: "IA — parse échoué",
    en: "AI — parse failed",
  },
  "review.reason.ai_provider_error": {
    fr: "IA indisponible",
    en: "AI unavailable",
  },
  "review.reason.rule_collision": {
    fr: "Règles en conflit",
    en: "Conflicting rules",
  },
  "review.reason.contradiction_detected": {
    fr: "Contradiction",
    en: "Contradiction",
  },
  "review.reason.requested": {
    fr: "À valider",
    en: "Needs validation",
  },

  // Errors
  "review.loadError": {
    fr: "Échec du chargement de la file.",
    en: "Failed to load the queue.",
  },
  "review.decisionError": {
    fr: "Erreur lors de la décision.",
    en: "Error while submitting the decision.",
  },
  "review.chooseCategoryFirst": {
    fr: "Choisissez une catégorie avant de changer la suggestion.",
    en: "Choose a category before changing the suggestion.",
  },

  // Empty / loading states
  "review.loading": {
    fr: "Chargement de la file…",
    en: "Loading the queue…",
  },
  "review.empty": {
    fr: "Aucun produit à valider — la file de validation est vide.",
    en: "No product to validate — the validation queue is empty.",
  },

  // List chrome
  "review.pageStatus": {
    fr: "{n} produit(s) à valider — page {page} / {pages}",
    en: "{n} product(s) to validate — page {page} / {pages}",
  },
  "review.suggestionLabel": {
    fr: "Suggestion :",
    en: "Suggestion:",
  },
  "review.chooseCategory": {
    fr: "Choisir une catégorie…",
    en: "Choose a category…",
  },

  // Action buttons
  "review.change": {
    fr: "Changer",
    en: "Change",
  },
  "review.accept": {
    fr: "Accepter",
    en: "Accept",
  },
};
