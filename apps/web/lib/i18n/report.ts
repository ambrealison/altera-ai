/**
 * Phase Product-UX-E — report + result-step labels (PART D).
 * Covers RunReport.tsx and the guided workflow Result step.
 * Placeholders ({label}, {pct}) are substituted at call sites.
 */
import type { I18nDict } from "./types";

export const report: I18nDict = {
  // Hero
  "report.badge": { fr: "Résultat", en: "Result" },

  // Phase Product-UX-F — localized executive summary (replaces the raw
  // backend narrative in the hero). {ratio}/{plant}/{animal}/{total}/
  // {weight} are pre-formatted via formatPct/formatKg at the call site.
  "report.summary.ptRatio": {
    fr: "Ratio protéines végétales : {ratio} — {plant} d'origine végétale, {animal} d'origine animale, {total} de protéines totales in-scope.",
    en: "Plant-protein ratio: {ratio} — {plant} plant-source, {animal} animal-source, {total} total in-scope protein.",
  },
  "report.summary.ptEmpty": {
    fr: "Aucun produit protéiné in-scope sur cette période.",
    en: "No in-scope protein products for this period.",
  },
  "report.summary.wwfLead": {
    fr: "{weight} de poids de ventes in-scope réparties sur les 7 groupes alimentaires WWF. La ",
    en: "{weight} of in-scope sales weight across the 7 WWF food groups. The ",
  },
  "report.summary.wwfMethodologyLink": {
    fr: "méthodologie WWF Planet-Based Diets",
    en: "WWF Planet-Based Diets methodology",
  },
  "report.summary.wwfTail": {
    fr: " a été appliquée — elle mesure le poids des produits, pas la teneur en protéines.",
    en: " was applied — it measures product weight, not protein content.",
  },
  "report.combined.title": {
    fr: "Ce que l'on apprend",
    en: "What this tells you",
  },
  "report.combined.body": {
    fr: "Ce projet combine Protein Tracker (ratio protéines) et WWF (groupes alimentaires). Les deux analyses ci-dessous se lisent ensemble : le ratio végétal complète la répartition par groupe.",
    en: "This project combines Protein Tracker (protein ratio) and WWF (food groups). The two analyses below read together: the plant ratio complements the per-group breakdown.",
  },
  "report.toggle.label": {
    fr: "Méthodologie du rapport",
    en: "Report methodology",
  },
  "report.toggle.pt": { fr: "Protein Tracker", en: "Protein Tracker" },
  "report.toggle.wwf": { fr: "WWF", en: "WWF" },
  "report.export.title": {
    fr: "Exporter le catalogue catégorisé",
    en: "Export the categorised catalogue",
  },
  "report.export.body": {
    fr: "Fichier Excel : tous vos produits avec leurs catégories Protein Tracker et WWF, plus un onglet d'analyse par méthodologie avec graphiques.",
    en: "Excel file: all your products with their Protein Tracker and WWF categories, plus one analysis tab per methodology with charts.",
  },
  "report.export.button": { fr: "Télécharger l'Excel", en: "Download Excel" },
  "report.export.downloading": { fr: "Préparation…", en: "Preparing…" },
  "report.export.error": {
    fr: "Le téléchargement a échoué. Réessayez.",
    en: "The download failed. Please try again.",
  },

  // PT KPIs
  "report.kpi.plantProtein": { fr: "Protéines végétales", en: "Plant protein" },
  "report.kpi.animalProtein": { fr: "Protéines animales", en: "Animal protein" },
  "report.kpi.totalProtein": { fr: "Protéines totales", en: "Total protein" },
  "report.kpi.plantRatio": { fr: "Ratio végétal", en: "Plant ratio" },
  "report.categoryAnalysis": {
    fr: "Analyse par catégorie",
    en: "Category analysis",
  },
  "report.pt.coreInsight": {
    fr: "Les produits {label} apportent {pct} des protéines totales.",
    en: "{label} products contribute {pct} of total protein.",
  },

  // PT group labels
  "report.ptGroup.plant_based_core": { fr: "Végétal — cœur", en: "Plant — core" },
  "report.ptGroup.plant_based_non_core": {
    fr: "Végétal — hors cœur",
    en: "Plant — non-core",
  },
  "report.ptGroup.animal_core": { fr: "Animal — cœur", en: "Animal — core" },
  "report.ptGroup.composite_products": { fr: "Composites", en: "Composites" },
  "report.ptGroup.out_of_scope": { fr: "Hors périmètre", en: "Out of scope" },
  "report.ptGroup.unknown": { fr: "Inconnu", en: "Unknown" },

  // PT contributors
  "report.pt.topPositive": {
    fr: "Top produits qui améliorent le ratio",
    en: "Top products improving the ratio",
  },
  "report.pt.topWatchout": {
    fr: "Top produits à surveiller",
    en: "Top products to watch",
  },
  "report.pt.emptyPositive": {
    fr: "Aucun produit végétal identifié pour ce calcul.",
    en: "No plant product identified for this run.",
  },
  "report.pt.emptyWatchout": {
    fr: "Aucun produit animal identifié pour ce calcul.",
    en: "No animal product identified for this run.",
  },

  // Contributors (shared)
  "report.contributors.title": { fr: "Top produits", en: "Top products" },
  "report.contributors.unavailable": {
    fr: "Les contributions produit ne sont pas disponibles pour ce calcul.",
    en: "Per-product contributions are not available for this run.",
  },

  // WWF
  "report.wwf.step1Label": {
    fr: "Méthodologie WWF — Step 1 :",
    en: "WWF methodology — Step 1:",
  },
  "report.wwf.step1Body": {
    fr: "classification au niveau produit. Les produits composés sont comptés à leur poids total et affectés aux buckets meat-based, seafood-based, vegetarian ou vegan. Le Step 2 (décomposition ingrédient par ingrédient des produits marque propre) n'est pas encore activé : il nécessite des données de recette détaillées.",
    en: "product-level classification. Composite products are counted at their whole product weight and assigned to the meat-based, seafood-based, vegetarian or vegan buckets. Step 2 (ingredient-level decomposition of own-brand composites) is not enabled yet; it requires detailed recipe data.",
  },
  "report.wwf.kpiVolume": { fr: "Volume in-scope", en: "In-scope volume" },
  "report.wwf.kpiComposites": { fr: "Composites", en: "Composites" },
  "report.wwf.kpiVegVegan": {
    fr: "Composites végé/végane",
    en: "Vegetarian/vegan composites",
  },
  "report.wwf.fgVsTarget": {
    fr: "Groupes alimentaires vs cible PHD",
    en: "Food groups vs PHD target",
  },
  "report.wwf.target": { fr: "cible", en: "target" },
  "report.wwf.fg4Below": {
    fr: "Fruits & légumes sous la cible.",
    en: "Fruit & vegetables below target.",
  },
  "report.wwf.fg7Watch": {
    fr: "Snacks à surveiller.",
    en: "Snacks to watch.",
  },
  "report.wwf.compositesByBucket": {
    fr: "Composites par bucket (Step 1)",
    en: "Composites by bucket (Step 1)",
  },
  "report.wwf.topAligned": { fr: "Top produits alignés", en: "Top aligned products" },
  "report.wwf.topWatchout": {
    fr: "Top produits à surveiller",
    en: "Top products to watch",
  },
  "report.wwf.emptyAligned": {
    fr: "Aucun produit aligné identifié pour ce calcul.",
    en: "No aligned product identified for this run.",
  },
  "report.wwf.emptyWatchout": {
    fr: "Aucun produit à surveiller identifié pour ce calcul.",
    en: "No product to watch identified for this run.",
  },

  // WWF food-group labels
  "report.fg.FG1": { fr: "Protéines", en: "Proteins" },
  "report.fg.FG2": { fr: "Lait & alternatives", en: "Dairy & alternatives" },
  "report.fg.FG3": { fr: "Matières grasses", en: "Fats" },
  "report.fg.FG4": { fr: "Fruits & légumes", en: "Fruit & vegetables" },
  "report.fg.FG5": { fr: "Céréales", en: "Grains" },
  "report.fg.FG6": { fr: "Tubercules / féculents", en: "Tubers / starches" },
  "report.fg.FG7": { fr: "Snacks (sucre/sel/gras)", en: "Snacks (sugar/salt/fat)" },
  "report.fg.out_of_scope": { fr: "Hors périmètre", en: "Out of scope" },
  "report.fg.unknown": { fr: "Inconnu", en: "Unknown" },

  // WWF composite bucket labels
  "report.bucket.meat": { fr: "À base de viande", en: "Meat-based" },
  "report.bucket.seafood": { fr: "À base de poisson", en: "Seafood-based" },
  "report.bucket.vegetarian": { fr: "Végétarien", en: "Vegetarian" },
  "report.bucket.vegan": { fr: "Végane", en: "Vegan" },

  // Action priorities
  "report.priorities": { fr: "Priorités d'action", en: "Action priorities" },

  // ---- Result step (guided workflow) ----
  "report.step.title": { fr: "Résultat / rapport", en: "Results / report" },
  "report.step.subtitleAfterRun": {
    fr: "Le rapport complet s'affiche ici après un calcul réussi.",
    en: "The full report appears here after a successful calculation.",
  },
  "report.step.noRun": {
    fr: "Aucun calcul effectué. Revenez à l'étape Calcul pour lancer un premier calcul.",
    en: "No calculation yet. Go back to the Calculation step to run your first one.",
  },
  "report.step.technicalLink": {
    fr: "Détail technique (admin)",
    en: "Technical details (admin)",
  },
  "report.step.technicalHint": {
    fr: "Exports CSV/JSON/Markdown et historique d'approbation.",
    en: "CSV/JSON/Markdown exports and approval history.",
  },
  "report.step.loadErrorTitle": {
    fr: "Le rapport complet n'a pas pu être chargé.",
    en: "The full report could not be loaded.",
  },
  "report.step.loadErrorBody": {
    fr: "Une erreur est survenue lors de la génération du rapport pour ce calcul. Réessayez dans un instant ; si le problème persiste, relancez un calcul depuis l'étape Calcul.",
    en: "An error occurred while generating the report for this run. Try again in a moment; if the problem persists, run a new calculation from the Calculation step.",
  },
  "report.step.preparing": {
    fr: "Préparation de votre rapport…",
    en: "Preparing your report…",
  },
  "report.step.refreshWarning": {
    fr: "Impossible d'actualiser le rapport pour le moment.",
    en: "Unable to refresh the report right now.",
  },
};
