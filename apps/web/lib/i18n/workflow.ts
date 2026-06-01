/**
 * Phase Product-UX-E — workflow surface labels.
 * Populated during the EN translation audit. UI labels only.
 */
import type { I18nDict } from "./types";

export const workflow: I18nDict = {
  // ----- Wizard step labels (stepper) -----
  "workflow.step.import": { fr: "Import", en: "Import" },
  "workflow.step.methodology": { fr: "Méthodologie", en: "Methodology" },
  "workflow.step.aiClass": { fr: "Classification IA", en: "AI classification" },
  "workflow.step.validation": { fr: "Validation", en: "Review" },
  "workflow.step.nevo": { fr: "NEVO", en: "NEVO" },
  "workflow.step.nutritionVal": {
    fr: "Validation nutritionnelle",
    en: "Nutrition review",
  },
  "workflow.step.calculation": { fr: "Calcul", en: "Calculation" },
  "workflow.step.report": { fr: "Résultat", en: "Result" },
  // WWF-only flavoured step labels
  "workflow.step.aiClass.wwf": { fr: "Catégorisation WWF", en: "WWF categorisation" },
  "workflow.step.validation.wwf": { fr: "Validation WWF", en: "WWF review" },
  "workflow.step.calculation.wwf": { fr: "Calcul WWF", en: "WWF calculation" },
  "workflow.step.report.wwf": { fr: "Rapport WWF", en: "WWF report" },

  // ----- Stepper chip -----
  "workflow.stepLocked": { fr: "Étape verrouillée", en: "Step locked" },

  // ----- Count badge labels -----
  "workflow.count.uploads": { fr: "Imports", en: "Uploads" },
  "workflow.count.products": { fr: "Produits", en: "Products" },
  "workflow.count.classified": { fr: "Classifiés", en: "Classified" },
  "workflow.count.remaining": { fr: "Restants", en: "Remaining" },
  "workflow.count.inReview": { fr: "En revue", en: "In review" },
  "workflow.count.unknown": { fr: "Inconnus", en: "Unknown" },
  "workflow.count.pending": { fr: "En attente", en: "Pending" },
  "workflow.count.matched": { fr: "Correspondances NEVO", en: "NEVO matches" },
  "workflow.count.withSplit": {
    fr: "Avec split plant/animal",
    en: "With plant/animal split",
  },
  "workflow.count.noMatch": { fr: "Sans correspondance", en: "No match" },
  "workflow.count.matchedTotalOnly": {
    fr: "Correspondances CIQUAL",
    en: "CIQUAL matches",
  },
  "workflow.count.eligibleRows": { fr: "Lignes éligibles", en: "Eligible rows" },
  "workflow.count.runs": { fr: "Calculs", en: "Runs" },

  // ----- Step: Import -----
  "workflow.import.title": {
    fr: "Importer le fichier CSV",
    en: "Import the CSV file",
  },
  "workflow.import.desc": {
    fr: "Chargez le fichier produits du retailer. Altera vérifiera le mapping des colonnes automatiquement et génèrera les identifiants manquants.",
    en: "Upload the retailer's product file. Altera will check the column mapping automatically and generate any missing identifiers.",
  },
  "workflow.import.warnings": {
    fr: "{n} avertissement(s) à l’import.",
    en: "{n} warning(s) on import.",
  },
  "workflow.import.continue": {
    fr: "Continuer vers Méthodologie",
    en: "Continue to Methodology",
  },

  // ----- Step: Methodology -----
  "workflow.methodology.title": { fr: "Méthodologie", en: "Methodology" },
  "workflow.methodology.desc": {
    fr: "La méthodologie détermine le type de calcul effectué sur les produits importés.",
    en: "The methodology determines the type of calculation run on the imported products.",
  },
  "workflow.methodology.desc.pt": {
    fr: "Calcule le ratio protéines végétales / protéines totales à partir des données d'achat et de nutrition.",
    en: "Computes the plant protein / total protein ratio from purchase and nutrition data.",
  },
  "workflow.methodology.desc.wwf": {
    fr: "Step 1 (niveau produit) : classe les achats alimentaires selon les groupes PHD du WWF (FG1–FG7) et affecte les produits composés à leur poids total dans les buckets meat-based, seafood-based, vegetarian ou vegan. Requiert le poids unitaire et le volume des ventes. Le Step 2 ingrédient-level (recettes marque propre) n'est pas encore activé.",
    en: "Step 1 (product-level): classifies food purchases according to WWF's PHD food groups (FG1–FG7) and assigns composite products at their whole weight to the meat-based, seafood-based, vegetarian or vegan buckets. Requires unit weight and sales volume. Step 2 ingredient-level (own-brand recipes) is not enabled yet.",
  },
  "workflow.methodology.enabled": { fr: "Activée", en: "Enabled" },
  "workflow.methodology.continue": {
    fr: "Continuer vers la classification",
    en: "Continue to classification",
  },
  "workflow.methodology.fixedNote": {
    fr: "La méthodologie est définie à la création du projet. Retournez aux paramètres du projet pour la modifier.",
    en: "The methodology is set when the project is created. Return to the project settings to change it.",
  },
  "workflow.methodology.noneSelected": {
    fr: "Aucune méthodologie sélectionnée",
    en: "No methodology selected",
  },

  // ----- Classification job progress -----
  "workflow.job.queued": { fr: "En file d'attente", en: "Queued" },
  "workflow.job.running": { fr: "En cours…", en: "Running…" },
  "workflow.job.completed": { fr: "Terminé", en: "Completed" },
  "workflow.job.completedWithErrors": {
    fr: "Terminé avec erreurs",
    en: "Completed with errors",
  },
  "workflow.job.failed": { fr: "Échec", en: "Failed" },
  "workflow.job.cancelled": { fr: "Annulé", en: "Cancelled" },
  "workflow.job.countsLine": {
    fr: "{categorized} catégorisé(s) · {accepted} accepté(s) · {review} à vérifier · {failed} échec",
    en: "{categorized} categorised · {accepted} accepted · {review} to review · {failed} failed",
  },
  "workflow.job.retry": { fr: "{n} retry", en: "{n} retry" },
  "workflow.job.recovered": {
    fr: " ({n} récupéré(s))",
    en: " ({n} recovered)",
  },
  "workflow.job.outOfScope": {
    fr: " · {n} hors périmètre",
    en: " · {n} out of scope",
  },
  "workflow.job.unknown": { fr: " · {n} inconnu(s)", en: " · {n} unknown" },
  "workflow.job.keepOpen": {
    fr: "Vous pouvez laisser cette page ouverte — la progression est sauvegardée.",
    en: "You can leave this page open — progress is saved.",
  },

  // ----- Step: AI classification -----
  "workflow.ai.title": { fr: "Classification IA", en: "AI classification" },
  "workflow.ai.title.wwf": { fr: "Catégorisation WWF", en: "WWF categorisation" },
  "workflow.ai.desc": {
    fr: "L'IA aide à catégoriser les produits restants à partir de champs non commerciaux.",
    en: "The AI helps categorise the remaining products from non-commercial fields.",
  },
  "workflow.ai.desc.wwf": {
    fr: "Cette étape classe les produits en groupes alimentaires WWF (FG1–FG7) et identifie les produits composites.",
    en: "This step classifies products into WWF food groups (FG1–FG7) and identifies composite products.",
  },
  "workflow.ai.privacyNote": {
    fr: "Les champs commerciaux comme volumes, ventes, prix et marges ne sont pas envoyés à l'IA.",
    en: "Commercial fields such as volumes, sales, prices and margins are not sent to the AI.",
  },
  "workflow.ai.banner.deterministicOnly": {
    fr: "Classification IA volontairement désactivée pour cette exécution (mode déterministe seul).",
    en: "AI classification intentionally disabled for this run (deterministic-only mode).",
  },
  "workflow.ai.banner.classifierDisabled": {
    fr: "Classification IA indisponible : ALTERA_AI_CLASSIFIER_ENABLED n’est pas activé sur ce serveur.",
    en: "AI classification unavailable: ALTERA_AI_CLASSIFIER_ENABLED is not enabled on this server.",
  },
  "workflow.ai.banner.providerDisabled": {
    fr: "Classification IA indisponible : ALTERA_AI_PROVIDER vaut 'disabled'.",
    en: "AI classification unavailable: ALTERA_AI_PROVIDER is set to 'disabled'.",
  },
  "workflow.ai.banner.providerMisconfigured": {
    fr: "Classification IA indisponible : OPENAI_API_KEY est manquant (provider OpenAI sélectionné).",
    en: "AI classification unavailable: OPENAI_API_KEY is missing (OpenAI provider selected).",
  },
  "workflow.ai.banner.generic": {
    fr: "Classification IA indisponible — vérifier ALTERA_AI_CLASSIFIER_ENABLED, ALTERA_AI_PROVIDER, et OPENAI_API_KEY sur le serveur. Les produits non reconnus partent en validation manuelle.",
    en: "AI classification unavailable — check ALTERA_AI_CLASSIFIER_ENABLED, ALTERA_AI_PROVIDER, and OPENAI_API_KEY on the server. Unrecognised products go to manual review.",
  },
  "workflow.ai.resultLine": {
    fr: "{categorized} catégorisé(s) · {accepted} accepté(s) · {review} à vérifier · {failed} échec.",
    en: "{categorized} categorised · {accepted} accepted · {review} to review · {failed} failed.",
  },
  "workflow.ai.ranOn": {
    fr: "IA exécutée sur {attempted} produit(s) en {batches} batch(s)",
    en: "AI ran on {attempted} product(s) across {batches} batch(es)",
  },
  "workflow.ai.diagnostics": { fr: "Diagnostics IA :", en: "AI diagnostics:" },
  "workflow.ai.parseFailures": {
    fr: "{n} réponse(s) IA non analysables (JSON invalide / id manquant)",
    en: "{n} AI response(s) could not be parsed (invalid JSON / missing id)",
  },
  "workflow.ai.unsupportedCategory": {
    fr: "{n} catégorie(s) inconnue(s) renvoyée(s) par le modèle",
    en: "{n} unknown categor(ies) returned by the model",
  },
  "workflow.ai.providerErrors": {
    fr: "{n} erreur(s) fournisseur (réseau / 5xx / clé invalide)",
    en: "{n} provider error(s) (network / 5xx / invalid key)",
  },
  "workflow.ai.viewSampleErrors": {
    fr: "Voir un échantillon des erreurs",
    en: "View a sample of the errors",
  },
  "workflow.ai.notNeeded": {
    fr: "Aucune classification IA nécessaire — tous les produits ont été classifiés déterministement.",
    en: "No AI classification needed — all products were classified deterministically.",
  },
  "workflow.ai.continueToValidation": {
    fr: "Continuer vers Validation",
    en: "Continue to Review",
  },
  "workflow.ai.inProgressWithCount": {
    fr: "Classification en cours… ({processed}/{total})",
    en: "Classification in progress… ({processed}/{total})",
  },
  "workflow.ai.resumeWithCount": {
    fr: "Reprendre la classification ({processed}/{total})",
    en: "Resume classification ({processed}/{total})",
  },
  "workflow.ai.retryFailures": {
    fr: "Réessayer {n} échec(s)",
    en: "Retry {n} failure(s)",
  },
  "workflow.ai.reclassify": { fr: "Reclassifier", en: "Reclassify" },
  "workflow.ai.reclassifyWwf": { fr: "Reclassifier WWF", en: "Reclassify WWF" },
  "workflow.ai.retryWwf": {
    fr: "Réessayer la catégorisation WWF",
    en: "Retry WWF categorisation",
  },
  "workflow.ai.retryAi": {
    fr: "Réessayer la classification IA",
    en: "Retry AI classification",
  },
  "workflow.ai.runningWwf": {
    fr: "Catégorisation WWF en cours…",
    en: "WWF categorisation in progress…",
  },
  "workflow.ai.runningAi": {
    fr: "Classification IA en cours…",
    en: "AI classification in progress…",
  },
  "workflow.ai.runWwf": {
    fr: "Lancer la catégorisation WWF",
    en: "Run WWF categorisation",
  },
  "workflow.ai.runAi": {
    fr: "Lancer la classification IA",
    en: "Run AI classification",
  },
  "workflow.ai.importFirst": {
    fr: "Importez d'abord un fichier à l'étape 1.",
    en: "Import a file at step 1 first.",
  },

  // ----- Methodology classification card (dual panel) -----
  "workflow.card.title.wwf": { fr: "Catégorisation WWF", en: "WWF categorisation" },
  "workflow.card.title.pt": {
    fr: "Catégorisation Protein Tracker",
    en: "Protein Tracker categorisation",
  },
  "workflow.card.desc.wwf": {
    fr: "Classe les produits en groupes alimentaires WWF (FG1–FG7), sous-groupes et composites.",
    en: "Classifies products into WWF food groups (FG1–FG7), sub-groups and composites.",
  },
  "workflow.card.desc.pt": {
    fr: "Classe les produits en groupes Protein Tracker (plant-based core, animal core, composite, etc.).",
    en: "Classifies products into Protein Tracker groups (plant-based core, animal core, composite, etc.).",
  },
  "workflow.card.run.wwf": {
    fr: "Lancer la catégorisation WWF",
    en: "Run WWF categorisation",
  },
  "workflow.card.run.pt": {
    fr: "Lancer la catégorisation Protein Tracker",
    en: "Run Protein Tracker categorisation",
  },
  "workflow.card.resume.wwf": {
    fr: "Reprendre la catégorisation WWF",
    en: "Resume WWF categorisation",
  },
  "workflow.card.resume.pt": {
    fr: "Reprendre la catégorisation Protein Tracker",
    en: "Resume Protein Tracker categorisation",
  },
  "workflow.card.viewValidation.wwf": {
    fr: "Voir la validation WWF",
    en: "View WWF review",
  },
  "workflow.card.viewValidation.pt": {
    fr: "Voir la validation Protein Tracker",
    en: "View Protein Tracker review",
  },
  "workflow.card.pill.done": { fr: "Terminée", en: "Completed" },
  "workflow.card.pill.doneToValidate": {
    fr: "Terminée · à valider",
    en: "Completed · to review",
  },
  "workflow.card.pill.doneWithErrors": {
    fr: "Terminée avec erreurs",
    en: "Completed with errors",
  },
  "workflow.card.pill.running": { fr: "En cours", en: "In progress" },
  "workflow.card.pill.locked": { fr: "Verrouillée", en: "Locked" },
  "workflow.card.pill.toRun": { fr: "À lancer", en: "To run" },
  "workflow.card.counts.successUnresolved": {
    fr: "{success} réussies / {unresolved} à résoudre",
    en: "{success} succeeded / {unresolved} to resolve",
  },
  "workflow.card.counts.inReview": { fr: " · {n} en revue", en: " · {n} in review" },
  "workflow.card.counts.categorized": {
    fr: "{classified}/{total} catégorisé(s)",
    en: "{classified}/{total} categorised",
  },
  "workflow.card.counts.pending": { fr: " · {n} en attente", en: " · {n} pending" },
  "workflow.card.failedBanner": {
    fr: "Échec · {message}.",
    en: "Failed · {message}.",
  },
  "workflow.card.failedBanner.unknownError": {
    fr: "erreur inconnue",
    en: "unknown error",
  },
  "workflow.card.doneWithErrorsBanner": {
    fr: "Terminée avec erreurs · {n} ligne(s) à résoudre.",
    en: "Completed with errors · {n} row(s) to resolve.",
  },
  "workflow.card.retryRows": {
    fr: "Réessayer {n} ligne(s)",
    en: "Retry {n} row(s)",
  },
  "workflow.card.resumeWithCount": {
    fr: "{label} ({processed}/{total})",
    en: "{label} ({processed}/{total})",
  },

  // ----- Step: AI classification dual panel -----
  "workflow.dual.title": {
    fr: "Classification IA — Protein Tracker + WWF",
    en: "AI classification — Protein Tracker + WWF",
  },
  "workflow.dual.desc": {
    fr: "Ce projet a deux méthodologies activées. Vous pouvez lancer les deux catégorisations en un clic, ou les piloter indépendamment via les cartes ci-dessous.",
    en: "This project has two methodologies enabled. You can run both categorisations in one click, or drive them independently from the cards below.",
  },
  "workflow.dual.privacyNote": {
    fr: "Les champs commerciaux (volumes, ventes, prix, marges) ne sont jamais envoyés à l'IA.",
    en: "Commercial fields (volumes, sales, prices, margins) are never sent to the AI.",
  },
  "workflow.dual.runBoth": {
    fr: "Lancer les deux catégorisations",
    en: "Run both categorisations",
  },
  "workflow.dual.runRemainingPt": {
    fr: "Lancer la catégorisation Protein Tracker restante",
    en: "Run remaining Protein Tracker categorisation",
  },
  "workflow.dual.runRemainingWwf": {
    fr: "Lancer la catégorisation WWF restante",
    en: "Run remaining WWF categorisation",
  },
  "workflow.dual.allDone": { fr: "Tout est terminé", en: "Everything is done" },
  "workflow.dual.hint.both": {
    fr: "Lance les deux jobs en parallèle. Vous pouvez fermer cette page — chaque job est sauvegardé et reprenable.",
    en: "Runs both jobs in parallel. You can close this page — each job is saved and resumable.",
  },
  "workflow.dual.hint.ptNeeds": {
    fr: "WWF est terminée. Cliquez pour lancer Protein Tracker.",
    en: "WWF is done. Click to run Protein Tracker.",
  },
  "workflow.dual.hint.wwfNeeds": {
    fr: "Protein Tracker est terminée. Cliquez pour lancer WWF.",
    en: "Protein Tracker is done. Click to run WWF.",
  },
  "workflow.dual.errors.both": {
    fr: "Les deux catégorisations ont des lignes à résoudre.",
    en: "Both categorisations have rows to resolve.",
  },
  "workflow.dual.errors.pt": {
    fr: "La catégorisation Protein Tracker a {n} ligne(s) à résoudre.",
    en: "The Protein Tracker categorisation has {n} row(s) to resolve.",
  },
  "workflow.dual.errors.wwf": {
    fr: "La catégorisation WWF a {n} ligne(s) à résoudre.",
    en: "The WWF categorisation has {n} row(s) to resolve.",
  },
  "workflow.dual.errors.hint": {
    fr: "Vous pouvez continuer vers la validation pour les corriger manuellement, ou cliquer sur « Réessayer » pour relancer les lignes en échec.",
    en: "You can continue to review to fix them manually, or click « Retry » to re-run the failed rows.",
  },
  "workflow.dual.continueToValidation": {
    fr: "Continuer vers Validation",
    en: "Continue to Review",
  },

  // ----- Step: Validation -----
  "workflow.validation.title": {
    fr: "Validation des catégories",
    en: "Category review",
  },
  "workflow.validation.title.wwf": { fr: "Validation WWF", en: "WWF review" },
  "workflow.validation.desc": {
    fr: "Tableau de validation : voir et corriger les catégories assignées par les règles déterministes et par l'IA.",
    en: "Review table: see and correct the categories assigned by the deterministic rules and the AI.",
  },
  "workflow.validation.desc.wwf": {
    fr: "Tableau de validation WWF : inspectez les groupes alimentaires (FG1–FG7), les sous-groupes et les buckets composites attribués par l'IA et les règles déterministes.",
    en: "WWF review table: inspect the food groups (FG1–FG7), sub-groups and composite buckets assigned by the AI and the deterministic rules.",
  },
  "workflow.validation.privacyNote": {
    fr: "Seuls les champs non commerciaux sont affichés. Volumes, ventes, prix et marges ne sont jamais utilisés pour la classification ni envoyés à l'IA.",
    en: "Only non-commercial fields are shown. Volumes, sales, prices and margins are never used for classification nor sent to the AI.",
  },
  "workflow.validation.noPending": {
    fr: "Aucun produit en attente de validation manuelle.",
    en: "No products awaiting manual review.",
  },
  "workflow.validation.pendingNote": {
    fr: "{n} produit(s) à vérifier — la validation manuelle est recommandée mais non bloquante.",
    en: "{n} product(s) to review — manual review is recommended but not blocking.",
  },
  "workflow.validation.continueToCalc.wwf": {
    fr: "Continuer vers Calcul WWF",
    en: "Continue to WWF Calculation",
  },
  "workflow.validation.continueToNevo": {
    fr: "Continuer vers NEVO",
    en: "Continue to NEVO",
  },

  // ----- Step: NEVO -----
  "workflow.nevo.title": { fr: "Enrichissement NEVO", en: "NEVO enrichment" },
  "workflow.nevo.desc": {
    fr: "NEVO est utilisé en priorité car il peut fournir les protéines totales, végétales et animales lorsque disponibles.",
    en: "NEVO is used first because it can provide total, plant and animal protein when available.",
  },
  "workflow.nevo.privacyNote": {
    fr: "L'IA peut aider à sélectionner une référence NEVO, mais les valeurs nutritionnelles viennent de NEVO, pas de l'IA.",
    en: "The AI can help select a NEVO reference, but the nutrition values come from NEVO, not the AI.",
  },
  "workflow.nevo.noneEnriched": {
    fr: "Aucun produit n’a été enrichi par NEVO.",
    en: "No product was enriched by NEVO.",
  },
  "workflow.nevo.tableLoaded": {
    fr: "Table NEVO : {n} référence(s) chargée(s).",
    en: "NEVO table: {n} reference(s) loaded.",
  },
  "workflow.nevo.notNeeded": {
    fr: "Tous les produits disposent déjà d'une donnée protéique du retailer — NEVO non requis.",
    en: "All products already have retailer protein data — NEVO not required.",
  },
  "workflow.nevo.enrichedHeading": {
    fr: "{n} produit(s) enrichi(s)",
    en: "{n} product(s) enriched",
  },
  "workflow.nevo.fallbackRef": { fr: "NEVO", en: "NEVO" },
  "workflow.nevo.splitFlag": { fr: " (split ✓)", en: " (split ✓)" },
  "workflow.nevo.noMatchHeading": {
    fr: "{n} produit(s) sans correspondance NEVO",
    en: "{n} product(s) with no NEVO match",
  },
  "workflow.nevo.noRefFound": {
    fr: "{name} — aucune référence NEVO trouvée",
    en: "{name} — no NEVO reference found",
  },
  "workflow.nevo.tryCiqualNext": {
    fr: "Ces produits seront tentés avec CIQUAL à l'étape suivante.",
    en: "These products will be tried with CIQUAL at the next step.",
  },
  "workflow.nevo.continueToNutrition": {
    fr: "Continuer vers la validation nutritionnelle",
    en: "Continue to nutrition review",
  },
  "workflow.nevo.rerun": { fr: "Relancer NEVO", en: "Re-run NEVO" },
  "workflow.nevo.running": {
    fr: "Enrichissement NEVO en cours…",
    en: "NEVO enrichment in progress…",
  },
  "workflow.nevo.run": { fr: "Enrichir avec NEVO", en: "Enrich with NEVO" },

  // ----- Step: CIQUAL -----
  "workflow.ciqual.title": { fr: "Fallback CIQUAL + IA", en: "CIQUAL + AI fallback" },
  "workflow.ciqual.desc": {
    fr: "Uniquement pour les produits encore sans donnée protéique après NEVO. CIQUAL fournit une protéine totale. Comme CIQUAL ne fournit pas de split végétal/animal, l'IA peut aider à sélectionner une référence — qui doit être tracée.",
    en: "Only for products still missing protein data after NEVO. CIQUAL provides total protein. Since CIQUAL does not provide a plant/animal split, the AI can help select a reference — which must be traced.",
  },
  "workflow.ciqual.notNeeded": {
    fr: "Tous les produits disposent d'une donnée protéique exploitable après NEVO — CIQUAL non requis.",
    en: "All products have usable protein data after NEVO — CIQUAL not required.",
  },
  "workflow.ciqual.locked": {
    fr: "Complétez d'abord l'étape NEVO avant d'utiliser CIQUAL.",
    en: "Complete the NEVO step first before using CIQUAL.",
  },
  "workflow.ciqual.continueToCalc": {
    fr: "Continuer vers Calcul",
    en: "Continue to Calculation",
  },
  "workflow.ciqual.nevoFirst": { fr: "NEVO d'abord", en: "NEVO first" },
  "workflow.ciqual.running": { fr: "CIQUAL en cours…", en: "CIQUAL in progress…" },
  "workflow.ciqual.run": { fr: "Essayer CIQUAL + IA", en: "Try CIQUAL + AI" },
  "workflow.ciqual.reEnrich": { fr: "Ré-enrichir CIQUAL", en: "Re-enrich CIQUAL" },
  "workflow.ciqual.continueWithout": {
    fr: "Continuer sans CIQUAL →",
    en: "Continue without CIQUAL →",
  },

  // ----- Step: Calculation -----
  "workflow.calc.title": { fr: "Calcul", en: "Calculation" },
  "workflow.calc.title.wwf": { fr: "Calcul WWF", en: "WWF calculation" },
  "workflow.calc.desc": {
    fr: "Lance le calcul du ratio protéines végétales / totales pour tous les produits éligibles. Le calcul est bloqué tant que des pré-requis sont manquants.",
    en: "Runs the plant / total protein ratio calculation for all eligible products. The calculation is blocked while prerequisites are missing.",
  },
  "workflow.calc.desc.wwf": {
    fr: "Lance le calcul des volumes WWF par groupe alimentaire (FG1–FG7) et la répartition des composites selon les buckets Step 1. Le calcul est bloqué tant que des pré-requis sont manquants.",
    en: "Runs the WWF volume calculation per food group (FG1–FG7) and the composite split across the Step 1 buckets. The calculation is blocked while prerequisites are missing.",
  },
  "workflow.calc.requirements": { fr: "Conditions requises", en: "Requirements" },
  "workflow.calc.readyLine": {
    fr: "{ready} sur {total} produit(s) prêt(s) pour le calcul.",
    en: "{ready} of {total} product(s) ready for calculation.",
  },
  "workflow.calc.missingNutritionLine": {
    fr: " {n} sans donnée protéique exploitable.",
    en: " {n} without usable protein data.",
  },
  "workflow.calc.check.fileImported": { fr: "Fichier importé", en: "File imported" },
  "workflow.calc.check.classification.wwf": {
    fr: "Classification WWF terminée",
    en: "WWF classification complete",
  },
  "workflow.calc.check.classification": {
    fr: "Classification terminée",
    en: "Classification complete",
  },
  "workflow.calc.check.manualReview.pending": {
    fr: "Validation manuelle — {n} à vérifier (non bloquant)",
    en: "Manual review — {n} to review (non-blocking)",
  },
  "workflow.calc.check.manualReview.complete": {
    fr: "Validation manuelle complète",
    en: "Manual review complete",
  },
  "workflow.calc.check.nutrition": {
    fr: "Données nutritionnelles disponibles",
    en: "Nutrition data available",
  },
  "workflow.calc.check.volume": {
    fr: "Données de volume / poids disponibles",
    en: "Volume / weight data available",
  },
  "workflow.calc.blocker.classifTitle": {
    fr: "Catégorisation incomplète",
    en: "Categorisation incomplete",
  },
  "workflow.calc.blocker.classifDesc.wwf": {
    fr: "Certains produits n'ont pas encore de groupe alimentaire WWF.",
    en: "Some products do not yet have a WWF food group.",
  },
  "workflow.calc.blocker.classifDesc": {
    fr: "Certains produits n'ont pas encore de catégorie Protein Tracker validée.",
    en: "Some products do not yet have a validated Protein Tracker category.",
  },
  "workflow.calc.blocker.fix": { fr: "Corriger →", en: "Fix →" },
  "workflow.calc.blocker.nutritionTitle": {
    fr: "Données protéiques manquantes",
    en: "Missing protein data",
  },
  "workflow.calc.blocker.nutritionDesc": {
    fr: "Certains produits sont catégorisés, mais n'ont pas encore de protéine exploitable.",
    en: "Some products are categorised but do not yet have usable protein data.",
  },
  "workflow.calc.reviewBacklogTitle": {
    fr: "{n} produit(s) encore à vérifier",
    en: "{n} product(s) still to review",
  },
  "workflow.calc.reviewBacklogBody": {
    fr: "Le calcul peut être lancé avec les catégories actuelles. Les corrections manuelles affineront le résultat lors du prochain calcul. ",
    en: "The calculation can be run with the current categories. Manual corrections will refine the result on the next calculation. ",
  },
  "workflow.calc.viewReviewProducts": {
    fr: "Voir les produits à vérifier →",
    en: "View products to review →",
  },
  "workflow.calc.incompleteNutritionTitle": {
    fr: "Données nutritionnelles incomplètes",
    en: "Incomplete nutrition data",
  },
  "workflow.calc.incompleteNutritionBody": {
    fr: "{missing} produit(s) sans donnée protéique exploitable. {ready} produit(s) prêts seront inclus dans le calcul. Le rapport indiquera explicitement le pourcentage de produits couverts.",
    en: "{missing} product(s) without usable protein data. {ready} ready product(s) will be included in the calculation. The report will explicitly state the percentage of products covered.",
  },
  "workflow.calc.viewExcludedSample": {
    fr: "Voir un échantillon des produits exclus",
    en: "View a sample of the excluded products",
  },
  "workflow.calc.running": { fr: "Calcul en cours…", en: "Calculation in progress…" },
  "workflow.calc.run": { fr: "Lancer le calcul", en: "Run the calculation" },
  "workflow.calc.runPartial": {
    fr: "Calculer sur les données disponibles",
    en: "Calculate on the available data",
  },
  "workflow.calc.noUsableProduct": {
    fr: "Aucun produit exploitable",
    en: "No usable product",
  },

  // ----- Step: Nutrition validation (inline) -----
  "workflow.nutrition.title": {
    fr: "Validation nutritionnelle",
    en: "Nutrition review",
  },
  "workflow.nutrition.desc": {
    fr: "Inspectez les valeurs protéiques attribuées par NEVO et complétez manuellement les produits restants.",
    en: "Inspect the protein values assigned by NEVO and manually complete the remaining products.",
  },
  "workflow.nutrition.privacyNote": {
    fr: "L'IA ne génère jamais de valeurs protéiques. Les valeurs proviennent du CSV retailer, de NEVO, ou de la saisie manuelle.",
    en: "The AI never generates protein values. The values come from the retailer CSV, from NEVO, or from manual entry.",
  },
  "workflow.nutrition.continueToCalc": {
    fr: "Continuer vers Calcul",
    en: "Continue to Calculation",
  },

  // ----- Action error messages -----
  "workflow.err.classifyFailed": {
    fr: "La classification IA a échoué côté serveur. Réessayez ou contactez l'équipe Altera.",
    en: "AI classification failed on the server. Retry or contact the Altera team.",
  },
  "workflow.err.uploadNotFound": {
    fr: "Fichier introuvable — il a peut-être été supprimé. Re-importez le CSV.",
    en: "File not found — it may have been deleted. Re-import the CSV.",
  },
  "workflow.err.classifyInvalidRequest": {
    fr: "Requête invalide : {message}",
    en: "Invalid request: {message}",
  },
  "workflow.err.classifyInvalidRequest.fallback": {
    fr: "vérifier les options",
    en: "check the options",
  },
  "workflow.err.zeroUsableNutrition": {
    fr: "Aucun produit ne dispose de données protéiques exploitables. Complétez au moins une ligne dans la validation nutritionnelle (ou exécutez NEVO si ce n'est pas encore fait).",
    en: "No product has usable protein data. Complete at least one row in the nutrition review (or run NEVO if not yet done).",
  },
  "workflow.err.runNotReady": {
    fr: "Le calcul ne peut pas être lancé : {message}.",
    en: "The calculation cannot be run: {message}.",
  },
  "workflow.err.runNotReady.fallback": {
    fr: "des étapes restent à compléter",
    en: "some steps remain to be completed",
  },
  "workflow.err.serializationFailed": {
    fr: "Le serveur a renvoyé une réponse invalide. L'équipe Altera a été notifiée — réessayez dans quelques instants.",
    en: "The server returned an invalid response. The Altera team has been notified — retry in a few moments.",
  },
  "workflow.err.jobConflict": {
    fr: "Une autre exécution est en cours. Patientez quelques secondes puis réessayez.",
    en: "Another run is in progress. Wait a few seconds then retry.",
  },
  "workflow.err.failedToFetch": {
    fr: "Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.",
    en: "Unable to reach the server. Check your connection then retry.",
  },
  "workflow.err.unexpected": { fr: "Erreur inattendue", en: "Unexpected error" },
  "workflow.err.loadFailed": { fr: "Échec du chargement", en: "Failed to load" },
  "workflow.err.heavyJobInProgress": {
    fr: "Un traitement volumineux est actuellement en cours sur la plateforme. Il peut provenir d'une autre organisation. Réessayez dans quelques minutes — un traitement en pause sur votre fichier reste reprenable.",
    en: "A heavy job is currently running on the platform. It may belong to another organisation. Retry in a few minutes — a paused job on your file remains resumable.",
  },
  "workflow.err.classifyAiFailed": {
    fr: "Échec de la classification IA.",
    en: "AI classification failed.",
  },
  "workflow.err.classifyWwfFailed": {
    fr: "Échec de la catégorisation WWF.",
    en: "WWF categorisation failed.",
  },
  "workflow.err.resumeFailed": {
    fr: "Impossible de reprendre la classification.",
    en: "Unable to resume classification.",
  },
  "workflow.err.resumeWwfFailed": {
    fr: "Impossible de reprendre la catégorisation WWF.",
    en: "Unable to resume WWF categorisation.",
  },
  "workflow.err.retryFailed": {
    fr: "Échec lors du redémarrage de la classification IA.",
    en: "Failed to restart AI classification.",
  },
  "workflow.err.retryWwfFailed": {
    fr: "Échec lors du redémarrage de la catégorisation WWF.",
    en: "Failed to restart WWF categorisation.",
  },

  // ----- Job poll transient messages -----
  "workflow.poll.interrupted": {
    fr: "Connexion temporairement interrompue. Nouvelle tentative…",
    en: "Connection temporarily interrupted. Retrying…",
  },
  "workflow.poll.deadEnd": {
    fr: "Connexion interrompue. Le traitement est sauvegardé et peut être repris.",
    en: "Connection lost. The job is saved and can be resumed.",
  },
  "workflow.poll.deadEnd.wwf": {
    fr: "Connexion interrompue. Le traitement WWF est sauvegardé et peut être repris.",
    en: "Connection lost. The WWF job is saved and can be resumed.",
  },

  // ----- Header / hero / footer -----
  "workflow.hero.badge.wwf": {
    fr: "WWF Planet-Based Diets",
    en: "WWF Planet-Based Diets",
  },
  "workflow.hero.badge": { fr: "Parcours guidé", en: "Guided workflow" },
  "workflow.hero.title.wwf": {
    fr: "Parcours WWF Planet-Based Diets",
    en: "WWF Planet-Based Diets workflow",
  },
  "workflow.hero.title": { fr: "Parcours guidé", en: "Guided workflow" },
  "workflow.hero.stepProgress": {
    fr: "Étape {current} sur {total} · Progression {pct} %",
    en: "Step {current} of {total} · Progress {pct} %",
  },
  "workflow.hero.technicalDetail": {
    fr: "Détail technique →",
    en: "Technical detail →",
  },
  "workflow.backToProject": { fr: "← Retour au projet", en: "← Back to project" },
  "workflow.prev": { fr: "← Précédent", en: "← Previous" },
  "workflow.next": { fr: "Suivant →", en: "Next →" },
  "workflow.footer.wwf": {
    fr: "Note : la catégorisation WWF utilise uniquement les descripteurs non commerciaux (nom, marque, catégorie retailer, ingrédients). Les volumes, ventes et prix ne sont jamais envoyés à l'IA.",
    en: "Note: WWF categorisation uses only non-commercial descriptors (name, brand, retailer category, ingredients). Volumes, sales and prices are never sent to the AI.",
  },
  "workflow.footer": {
    fr: "Note : l'IA peut aider à sélectionner certaines références, mais ne génère pas de valeurs nutritionnelles. Les protéines proviennent uniquement des données fournies par le retailer, de NEVO, de CIQUAL ou de la validation manuelle.",
    en: "Note: the AI can help select some references, but does not generate nutrition values. Protein comes only from data supplied by the retailer, from NEVO, from CIQUAL or from manual review.",
  },
};
