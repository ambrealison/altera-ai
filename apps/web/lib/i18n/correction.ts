/**
 * Phase Product-UX-E — correction surface labels.
 * Populated during the EN translation audit. UI labels only.
 */
import type { I18nDict } from "./types";

export const correction: I18nDict = {
  // Modal title / header
  "correction.title": {
    fr: "Corriger la classification WWF",
    en: "Correct the WWF classification",
  },

  // Food group selector labels (CODE keys unchanged in the component)
  "correction.fg.FG1": {
    fr: "FG1 — Sources de protéines",
    en: "FG1 — Protein sources",
  },
  "correction.fg.FG2": {
    fr: "FG2 — Produits laitiers et alternatives",
    en: "FG2 — Dairy and alternatives",
  },
  "correction.fg.FG3": {
    fr: "FG3 — Matières grasses et huiles",
    en: "FG3 — Fats and oils",
  },
  "correction.fg.FG4": {
    fr: "FG4 — Fruits et légumes",
    en: "FG4 — Fruits and vegetables",
  },
  "correction.fg.FG5": { fr: "FG5 — Céréales", en: "FG5 — Grains" },
  "correction.fg.FG6": { fr: "FG6 — Tubercules", en: "FG6 — Tubers" },
  "correction.fg.FG7": {
    fr: "FG7 — Snacks riches en gras/sel/sucre",
    en: "FG7 — Snacks high in fat/salt/sugar",
  },
  "correction.fg.out_of_scope": {
    fr: "Hors périmètre",
    en: "Out of scope",
  },
  "correction.fg.unknown": { fr: "Inconnu", en: "Unknown" },

  // FG1 subgroup labels
  "correction.fg1.red_meat": { fr: "Viande rouge", en: "Red meat" },
  "correction.fg1.poultry": { fr: "Volaille", en: "Poultry" },
  "correction.fg1.processed_meats_alternatives": {
    fr: "Viandes transformées / alternatives",
    en: "Processed meats / alternatives",
  },
  "correction.fg1.seafood": {
    fr: "Poisson & fruits de mer",
    en: "Fish & seafood",
  },
  "correction.fg1.eggs": { fr: "Œufs", en: "Eggs" },
  "correction.fg1.legumes": { fr: "Légumineuses", en: "Legumes" },
  "correction.fg1.nuts_seeds": {
    fr: "Noix & graines",
    en: "Nuts & seeds",
  },
  "correction.fg1.alternative_protein_sources": {
    fr: "Sources protéiques alternatives",
    en: "Alternative protein sources",
  },
  "correction.fg1.meat_egg_seafood_alternatives": {
    fr: "Alternatives viande/œuf/poisson",
    en: "Meat/egg/seafood alternatives",
  },

  // FG2 subgroup labels
  "correction.fg2.cheese": {
    fr: "Produit laitier animal — Fromage",
    en: "Animal dairy — Cheese",
  },
  "correction.fg2.other_dairy_animal": {
    fr: "Produit laitier animal — Autre",
    en: "Animal dairy — Other",
  },
  "correction.fg2.dairy_alternative_plant": {
    fr: "Alternative végétale aux produits laitiers",
    en: "Plant-based dairy alternative",
  },

  // FG3 subgroup labels
  "correction.fg3.plant_based_fat": {
    fr: "Matières grasses végétales",
    en: "Plant-based fats",
  },
  "correction.fg3.animal_based_fat": {
    fr: "Matières grasses animales",
    en: "Animal-based fats",
  },

  // FG5 grain kind labels
  "correction.fg5.whole_grain": {
    fr: "Céréales complètes",
    en: "Whole grains",
  },
  "correction.fg5.refined_grain": {
    fr: "Céréales raffinées",
    en: "Refined grains",
  },

  // FG7 snack kind labels
  "correction.fg7.plant_based_snack": {
    fr: "Snack végétal",
    en: "Plant-based snack",
  },
  "correction.fg7.animal_based_snack": {
    fr: "Snack animal",
    en: "Animal-based snack",
  },

  // Composite bucket labels
  "correction.bucket.meat_based": {
    fr: "À base de viande",
    en: "Meat-based",
  },
  "correction.bucket.seafood_based": {
    fr: "À base de poisson/fruits de mer",
    en: "Seafood-based",
  },
  "correction.bucket.vegetarian": { fr: "Végétarien", en: "Vegetarian" },
  "correction.bucket.vegan": { fr: "Végane", en: "Vegan" },

  // Field labels
  "correction.label.foodGroup": {
    fr: "Groupe alimentaire",
    en: "Food group",
  },
  "correction.label.subgroup": { fr: "Sous-groupe", en: "Subgroup" },
  "correction.label.type": { fr: "Type", en: "Type" },
  "correction.label.compositeBucket": {
    fr: "Bucket composite",
    en: "Composite bucket",
  },
  "correction.label.composite": {
    fr: "Produit composite",
    en: "Composite product",
  },

  // Select placeholder
  "correction.choose": { fr: "— Choisir —", en: "— Choose —" },

  // Validation messages
  "correction.validation.systemNotComposite": {
    fr: "Hors périmètre / Inconnu ne peuvent pas être composite.",
    en: "Out of scope / Unknown cannot be composite.",
  },
  "correction.validation.fg1Subgroup": {
    fr: "Choisissez un sous-groupe FG1.",
    en: "Choose an FG1 subgroup.",
  },
  "correction.validation.fg2Subgroup": {
    fr: "Choisissez un sous-groupe FG2.",
    en: "Choose an FG2 subgroup.",
  },
  "correction.validation.fg3Subgroup": {
    fr: "Choisissez un sous-groupe FG3.",
    en: "Choose an FG3 subgroup.",
  },
  "correction.validation.fg5Grain": {
    fr: "Choisissez le type de céréale (FG5).",
    en: "Choose the grain type (FG5).",
  },
  "correction.validation.fg7Snack": {
    fr: "Choisissez le type de snack (FG7).",
    en: "Choose the snack type (FG7).",
  },
  "correction.validation.compositeBucket": {
    fr: "Choisissez le bucket composite (à base de viande / poisson / vegetarien / végane).",
    en: "Choose the composite bucket (meat-based / seafood / vegetarian / vegan).",
  },

  // Submit error fallback
  "correction.error.submit": {
    fr: "Échec de la correction.",
    en: "Correction failed.",
  },
};
