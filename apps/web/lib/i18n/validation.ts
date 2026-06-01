/**
 * Phase Product-UX-E — validation surface labels.
 * Populated during the EN translation audit. UI labels only.
 */
import type { I18nDict } from "./types";

export const validation: I18nDict = {
  // WWF food-group display labels (CODE keys preserved in the component).
  "validation.fg.FG1": { fr: "FG1 · Aliments protéiques", en: "FG1 · Protein foods" },
  "validation.fg.FG2": { fr: "FG2 · Lait & alternatives", en: "FG2 · Milk & alternatives" },
  "validation.fg.FG3": { fr: "FG3 · Matières grasses", en: "FG3 · Fats" },
  "validation.fg.FG4": { fr: "FG4 · Fruits & légumes", en: "FG4 · Fruit & vegetables" },
  "validation.fg.FG5": { fr: "FG5 · Céréales", en: "FG5 · Grains" },
  "validation.fg.FG6": {
    fr: "FG6 · Tubercules / féculents",
    en: "FG6 · Tubers / starches",
  },
  "validation.fg.FG7": {
    fr: "FG7 · Snacks (sucre/sel/gras)",
    en: "FG7 · Snacks (sugar/salt/fat)",
  },
  "validation.fg.out_of_scope": { fr: "Hors périmètre", en: "Out of scope" },
  "validation.fg.unknown": { fr: "Inconnu", en: "Unknown" },

  // WWF subgroup display labels.
  "validation.subgroup.red_meat": { fr: "Viande rouge", en: "Red meat" },
  "validation.subgroup.poultry": { fr: "Volaille", en: "Poultry" },
  "validation.subgroup.processed_meats_alternatives": {
    fr: "Viandes transformées / alternatives",
    en: "Processed meats / alternatives",
  },
  "validation.subgroup.seafood": {
    fr: "Poisson & fruits de mer",
    en: "Fish & seafood",
  },
  "validation.subgroup.eggs": { fr: "Œufs", en: "Eggs" },
  "validation.subgroup.legumes": { fr: "Légumineuses", en: "Legumes" },
  "validation.subgroup.nuts_seeds": { fr: "Noix & graines", en: "Nuts & seeds" },
  "validation.subgroup.alternative_protein_sources": {
    fr: "Sources protéiques alternatives",
    en: "Alternative protein sources",
  },
  "validation.subgroup.meat_egg_seafood_alternatives": {
    fr: "Alternatives viande/œuf/poisson",
    en: "Meat/egg/fish alternatives",
  },
  "validation.subgroup.cheese": { fr: "Fromage", en: "Cheese" },
  "validation.subgroup.other_dairy_animal": {
    fr: "Autres produits laitiers",
    en: "Other dairy products",
  },
  "validation.subgroup.dairy_alternative_plant": {
    fr: "Alternatives végétales aux produits laitiers",
    en: "Plant-based dairy alternatives",
  },
  "validation.subgroup.plant_based_fat": {
    fr: "Matières grasses végétales",
    en: "Plant-based fats",
  },
  "validation.subgroup.animal_based_fat": {
    fr: "Matières grasses animales",
    en: "Animal-based fats",
  },
  "validation.subgroup.whole_grain": {
    fr: "Céréales complètes",
    en: "Whole grains",
  },
  "validation.subgroup.refined_grain": {
    fr: "Céréales raffinées",
    en: "Refined grains",
  },
  "validation.subgroup.plant_based_snack": {
    fr: "Snack végétal",
    en: "Plant-based snack",
  },
  "validation.subgroup.animal_based_snack": {
    fr: "Snack animal",
    en: "Animal-based snack",
  },

  // WWF composite bucket display labels.
  "validation.bucket.meat_based": { fr: "À base de viande", en: "Meat-based" },
  "validation.bucket.seafood_based": {
    fr: "À base de poisson/fruits de mer",
    en: "Fish/seafood-based",
  },
  "validation.bucket.vegetarian": { fr: "Végétarien", en: "Vegetarian" },
  "validation.bucket.vegan": { fr: "Végane", en: "Vegan" },

  // Protein Tracker group display labels.
  "validation.ptGroup.plant_based_core": {
    fr: "Végétal — cœur",
    en: "Plant-based — core",
  },
  "validation.ptGroup.plant_based_non_core": {
    fr: "Végétal — hors cœur",
    en: "Plant-based — non-core",
  },
  "validation.ptGroup.composite_products": { fr: "Composite", en: "Composite" },
  "validation.ptGroup.animal_core": { fr: "Animal — cœur", en: "Animal — core" },
  "validation.ptGroup.out_of_scope": { fr: "Hors périmètre", en: "Out of scope" },
  "validation.ptGroup.unknown": { fr: "Inconnu", en: "Unknown" },

  // Classification source display labels.
  "validation.source.deterministic": { fr: "Déterministe", en: "Deterministic" },
  "validation.source.ai": { fr: "IA", en: "AI" },
  "validation.source.manual_review": { fr: "Manuel", en: "Manual" },
  "validation.source.unknown": { fr: "Aucune", en: "None" },

  // Empty / placeholder dash fallbacks.
  "validation.none": { fr: "Aucune", en: "None" },

  // Review status display labels.
  "validation.reviewStatus.in_queue": { fr: "À vérifier", en: "To review" },
  "validation.reviewStatus.reviewing": { fr: "En cours", en: "In progress" },
  "validation.reviewStatus.accepted": { fr: "Accepté", en: "Accepted" },
  "validation.reviewStatus.changed": { fr: "Modifié", en: "Changed" },
  "validation.reviewStatus.deferred": { fr: "Différé", en: "Deferred" },

  // Composite cell label.
  "validation.compositePrefix": { fr: "Composite · {bucket}", en: "Composite · {bucket}" },

  // Load / submit errors.
  "validation.error.load": {
    fr: "Échec du chargement du tableau.",
    en: "Failed to load the table.",
  },
  "validation.error.choosePtCategory": {
    fr: "Choisissez une catégorie avant de changer la classification PT.",
    en: "Choose a category before changing the PT classification.",
  },
  "validation.error.decision": {
    fr: "Erreur lors de la décision.",
    en: "Error while submitting the decision.",
  },

  // Header titles / subtitles.
  "validation.title.products": {
    fr: "Validation des produits",
    en: "Product validation",
  },
  "validation.title.wwf": { fr: "Validation WWF", en: "WWF validation" },
  "validation.title.pt": {
    fr: "Validation Protein Tracker",
    en: "Protein Tracker validation",
  },
  "validation.subtitle.products": {
    fr: "Vue d'ensemble des classifications Protein Tracker et WWF — actions indépendantes par méthodologie.",
    en: "Overview of the Protein Tracker and WWF classifications — independent actions per methodology.",
  },
  "validation.subtitle.wwf": {
    fr: "Vérifiez les groupes alimentaires WWF, sous-groupes et produits composites.",
    en: "Review the WWF food groups, subgroups and composite products.",
  },
  "validation.subtitle.pt": {
    fr: "Vérifiez les catégories Protein Tracker assignées par les règles déterministes et l'IA.",
    en: "Review the Protein Tracker categories assigned by the deterministic rules and the AI.",
  },

  // Counts banner.
  "validation.counts.displayed": {
    fr: "{n} produit(s) affiché(s)",
    en: "{n} product(s) shown",
  },
  "validation.counts.ptToReview": {
    fr: "PT à vérifier : {n}",
    en: "PT to review: {n}",
  },
  "validation.counts.wwfToReview": {
    fr: "WWF à vérifier : {n}",
    en: "WWF to review: {n}",
  },
  "validation.counts.totalToValidate": {
    fr: "Total à valider : {n}",
    en: "Total to validate: {n}",
  },
  "validation.counts.bySource": { fr: "{label} : {n}", en: "{label}: {n}" },

  // Filter bar.
  "validation.view.all": { fr: "Tous", en: "All" },
  "validation.view.toValidate": { fr: "À valider", en: "To validate" },
  "validation.methodology.all": { fr: "Toutes", en: "All" },
  "validation.methodology.pt": { fr: "PT", en: "PT" },
  "validation.methodology.wwf": { fr: "WWF", en: "WWF" },
  "validation.search.placeholder": {
    fr: "Rechercher (nom / marque)",
    en: "Search (name / brand)",
  },
  "validation.filter.allSources": { fr: "Toutes sources", en: "All sources" },
  "validation.filter.unclassified": { fr: "Non classé", en: "Unclassified" },
  "validation.filter.allPtCategories": {
    fr: "Toutes catégories PT",
    en: "All PT categories",
  },
  "validation.filter.allStatuses": { fr: "Tous statuts", en: "All statuses" },

  // Table headers.
  "validation.col.product": { fr: "Produit", en: "Product" },
  "validation.col.retailerCategory": {
    fr: "Catégorie retailer",
    en: "Retailer category",
  },
  "validation.col.proteinTracker": {
    fr: "Protein Tracker",
    en: "Protein Tracker",
  },
  "validation.col.ptStatus": { fr: "Statut PT", en: "PT status" },
  "validation.col.wwf": { fr: "WWF", en: "WWF" },
  "validation.col.wwfStatus": { fr: "Statut WWF", en: "WWF status" },
  "validation.col.actions": { fr: "Actions", en: "Actions" },
  "validation.col.wwfGroup": { fr: "Groupe WWF", en: "WWF group" },
  "validation.col.subgroup": { fr: "Sous-groupe", en: "Subgroup" },
  "validation.col.composite": { fr: "Composite", en: "Composite" },
  "validation.col.source": { fr: "Source", en: "Source" },
  "validation.col.confidence": { fr: "Confiance", en: "Confidence" },
  "validation.col.status": { fr: "Statut", en: "Status" },
  "validation.col.action": { fr: "Action", en: "Action" },
  "validation.col.pt": { fr: "PT", en: "PT" },

  // Empty state.
  "validation.empty.title": {
    fr: "Aucun produit ne correspond aux filtres",
    en: "No product matches the filters",
  },
  "validation.empty.body": {
    fr: "Ajustez la recherche, la source ou la confiance pour élargir les résultats.",
    en: "Adjust the search, source or confidence to broaden the results.",
  },

  // Pagination.
  "validation.pagination.page": { fr: "Page", en: "Page" },

  // Row actions.
  "validation.action.change": { fr: "Changer…", en: "Change…" },
  "validation.action.correct": { fr: "Corriger", en: "Correct" },
  "validation.action.correctPt": { fr: "Corriger PT", en: "Correct PT" },
  "validation.action.acceptPt": { fr: "Accepter PT", en: "Accept PT" },
  "validation.action.correctWwf": { fr: "Corriger WWF", en: "Correct WWF" },
  "validation.action.acceptWwf": { fr: "Accepter WWF", en: "Accept WWF" },

  // WWF correction modal reason.
  "validation.wwf.manualCorrection": {
    fr: "Correction manuelle",
    en: "Manual correction",
  },
};
