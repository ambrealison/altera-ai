/**
 * Phase Product-UX-E — upload surface labels.
 * Populated during the EN translation audit. UI labels only.
 *
 * SAFETY CONTRACT: these are display labels only. The canonical field
 * KEYS (e.g. "items_purchased") and option ``value=`` attributes
 * ("__none__", "ignore") are NOT translated — only the visible text.
 */
import type { I18nDict } from "./types";

export const upload: I18nDict = {
  // ---- Canonical-field display labels (KEYS stay canonical) ----
  "upload.field.external_product_id": {
    fr: "Identifiant produit / SKU",
    en: "Product ID / SKU",
  },
  "upload.field.product_name": { fr: "Nom du produit", en: "Product name" },
  "upload.field.brand": { fr: "Marque", en: "Brand" },
  "upload.field.retailer_category": {
    fr: "Catégorie retailer",
    en: "Retailer category",
  },
  "upload.field.retailer_subcategory": {
    fr: "Sous-catégorie retailer",
    en: "Retailer subcategory",
  },
  "upload.field.weight_per_item_kg": {
    fr: "Poids unitaire (kg)",
    en: "Unit weight (kg)",
  },
  "upload.field.weight_per_item_g": {
    fr: "Poids unitaire (g)",
    en: "Unit weight (g)",
  },
  "upload.field.items_purchased": {
    fr: "Volume / nombre d’unités (achats)",
    en: "Volume / number of units (purchases)",
  },
  "upload.field.protein_pct": {
    fr: "Protéines totales (%)",
    en: "Total protein (%)",
  },
  "upload.field.plant_protein_pct": {
    fr: "Protéines végétales (%)",
    en: "Plant protein (%)",
  },
  "upload.field.animal_protein_pct": {
    fr: "Protéines animales (%)",
    en: "Animal protein (%)",
  },
  "upload.field.ingredients_text": { fr: "Ingrédients", en: "Ingredients" },
  "upload.field.is_own_brand": { fr: "Marque propre ?", en: "Own brand?" },
  "upload.field.ean": { fr: "EAN / code-barres", en: "EAN / barcode" },
  "upload.field.labels": { fr: "Labels", en: "Labels" },
  "upload.field.country": { fr: "Pays", en: "Country" },
  "upload.field.language": { fr: "Langue", en: "Language" },
  "upload.field.reporting_period": {
    fr: "Période de reporting",
    en: "Reporting period",
  },
  "upload.field.items_sold": {
    fr: "Volume / nombre d’unités (ventes)",
    en: "Volume / number of units (sales)",
  },
  "upload.field.retail_channel": {
    fr: "Canal de distribution",
    en: "Retail channel",
  },

  // ---- Header parsing errors ----
  "upload.parse.headersUnreadable": {
    fr: "Lecture des en-têtes impossible",
    en: "Could not read the headers",
  },
  "upload.parse.headersUnreadableEn": {
    fr: "Could not read file headers",
    en: "Could not read file headers",
  },

  // ---- Confidence badges ----
  "upload.confidence.synonym": { fr: "synonyme", en: "synonym" },
  "upload.confidence.unmatched": { fr: "à mapper", en: "to map" },
  "upload.confidence.synonymEn": { fr: "synonym", en: "synonym" },
  "upload.confidence.unmatchedEn": { fr: "unmatched", en: "unmatched" },

  // ---- Mapping table (inline) ----
  "upload.table.csvColumn": { fr: "Colonne CSV", en: "CSV column" },
  "upload.table.mapTo": { fr: "Mapper vers", en: "Map to" },
  "upload.table.detection": { fr: "Détection", en: "Detection" },
  "upload.table.optionNone": {
    fr: "— Ignorer / tel quel —",
    en: "— Ignore / as is —",
  },
  "upload.table.optionIgnore": {
    fr: "Ignorer cette colonne",
    en: "Ignore this column",
  },

  // ---- Mapping table (standalone page) ----
  "upload.tableStd.csvHeader": { fr: "CSV header", en: "CSV header" },
  "upload.tableStd.mapToField": { fr: "Map to field", en: "Map to field" },
  "upload.tableStd.confidence": { fr: "Confidence", en: "Confidence" },
  "upload.tableStd.enrichable": { fr: "(enrichable)", en: "(enrichable)" },
  "upload.tableStd.optionNone": {
    fr: "— Ignorer / utiliser tel quel —",
    en: "— Ignore / use as is —",
  },
  "upload.tableStd.optionIgnore": {
    fr: "Ignorer cette colonne",
    en: "Ignore this column",
  },

  // ---- Already-imported summary ----
  "upload.summary.productsRows": {
    fr: "{p} produit(s) · {r} ligne(s)",
    en: "{p} product(s) · {r} row(s)",
  },
  "upload.summary.warnings": {
    fr: "{n} avertissement(s) à l’import.",
    en: "{n} warning(s) at import.",
  },
  "upload.summary.imported": { fr: "Importé", en: "Imported" },

  // ---- File picker ----
  "upload.picker.choose": {
    fr: "Choisir un fichier CSV",
    en: "Choose a CSV file",
  },
  // ---- Template CTA (Step 1 demo polish) ----
  "upload.template.button": { fr: "Modèle", en: "Template" },
  "upload.template.hint": {
    fr: "Téléchargez un modèle prêt à remplir avant d’importer votre catalogue.",
    en: "Download a ready-to-use template before importing your catalog.",
  },

  // ---- Analysis / preview ----
  "upload.analysing": { fr: "Analyse du fichier…", en: "Analysing file…" },
  "upload.previewError": {
    fr: "Impossible de lire le fichier ou de calculer le mapping.",
    en: "Could not read the file or compute the mapping.",
  },
  "upload.preview.columnsDetected": {
    fr: "{n} colonne(s) détectée(s)",
    en: "{n} column(s) detected",
  },
  "upload.preview.autoMapped": {
    fr: "{n} auto-mappée(s)",
    en: "{n} auto-mapped",
  },
  // ---- Missing-field warnings (inline) ----
  "upload.missing.ptTitle": {
    fr: "Champs Protein Tracker requis encore manquants : {fields}",
    en: "Required Protein Tracker fields still missing: {fields}",
  },
  "upload.missing.ptBody": {
    fr: "Sans ces champs, les lignes seront importées mais sans bloc Protein Tracker (avertissements par ligne).",
    en: "Without these fields, rows will be imported but without a Protein Tracker block (per-row warnings).",
  },
  "upload.missing.wwfTitle": {
    fr: "Champs WWF requis encore manquants : {fields}",
    en: "Required WWF fields still missing: {fields}",
  },
  "upload.missing.wwfBody": {
    fr: "Ces champs sont nécessaires pour calculer les volumes WWF par groupe alimentaire. Sans eux, les lignes seront importées mais sans bloc WWF.",
    en: "These fields are needed to compute WWF volumes per food group. Without them, rows will be imported but without a WWF block.",
  },

  // ---- Detailed-mapping toggle ----
  "upload.showMapping": {
    fr: "Voir / modifier le mapping détaillé →",
    en: "View / edit the detailed mapping →",
  },

  // ---- Submit / cancel buttons ----
  "upload.submit.importing": { fr: "Import en cours…", en: "Importing…" },
  "upload.submit.importingProgress": {
    fr: "Import en cours… ({done}/{total})",
    en: "Importing… ({done}/{total})",
  },
  "upload.submit.importFile": {
    fr: "Importer le fichier",
    en: "Import the file",
  },

  // ---- Submit error messages (inline) ----
  "upload.error.invalidCsv": {
    fr: "Fichier CSV invalide : {message}",
    en: "Invalid CSV file: {message}",
  },
  "upload.error.invalidCsvFallback": {
    fr: "vérifier l'encodage / le format",
    en: "check the encoding / format",
  },
  "upload.error.invalidMapping": {
    fr: "Mapping invalide : {message}",
    en: "Invalid mapping: {message}",
  },
  "upload.error.invalidMappingFallback": {
    fr: "vérifier les correspondances",
    en: "check the mappings",
  },
  "upload.error.createFailed": {
    fr: "Le serveur n'a pas pu créer la tâche d'import. Réessayez.",
    en: "The server could not create the import task. Please try again.",
  },
  "upload.error.advanceFailed": {
    fr: "Le serveur a rencontré une erreur pendant l'import. Réessayez.",
    en: "The server hit an error during the import. Please try again.",
  },
  "upload.error.jobNotFound": {
    fr: "Tâche d'import introuvable — le serveur a peut-être redémarré. Re-cliquez Importer.",
    en: "Import task not found — the server may have restarted. Click Import again.",
  },
  "upload.error.failedToFetch": {
    fr: "Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.",
    en: "Could not reach the server. Check your connection and try again.",
  },
  "upload.error.generic": { fr: "Échec de l’import.", en: "Import failed." },
  "upload.error.transient": {
    fr: "Connexion temporairement interrompue. Nouvelle tentative…",
    en: "Connection temporarily interrupted. Retrying…",
  },
  "upload.error.tooManyFailures": {
    fr: "Trop d'échecs réseau consécutifs. Cliquez sur Réessayer pour reprendre l'import.",
    en: "Too many consecutive network failures. Click Retry to resume the import.",
  },

  // ---- Ingestion job progress badges ----
  "upload.job.queued": { fr: "En file d'attente", en: "Queued" },
  "upload.job.running": { fr: "Import en cours…", en: "Importing…" },
  "upload.job.completed": { fr: "Import terminé", en: "Import complete" },
  "upload.job.completedWithErrors": {
    fr: "Terminé avec erreurs",
    en: "Completed with errors",
  },
  "upload.job.failed": { fr: "Échec", en: "Failed" },
  "upload.job.cancelled": { fr: "Annulé", en: "Cancelled" },
  "upload.job.insertedProducts": {
    fr: "{n} produit(s) insérés",
    en: "{n} product(s) inserted",
  },
  "upload.job.errorsSuffix": {
    fr: " · {n} erreur(s)",
    en: " · {n} error(s)",
  },
  "upload.job.keepOpen": {
    fr: "Vous pouvez laisser cette page ouverte — la progression est sauvegardée côté serveur.",
    en: "You can leave this page open — progress is saved on the server.",
  },
  "upload.job.errorLabel": { fr: "Erreur", en: "Error" },
  "upload.job.sampleErrors": {
    fr: "Voir un échantillon des erreurs ({n})",
    en: "View a sample of the errors ({n})",
  },

  // ---- Standalone upload page chrome ----
  "upload.page.title": { fr: "Upload data", en: "Upload data" },
  "upload.page.subtitle": {
    fr: "Upload a CSV. The pipeline drops commercial columns at the boundary, normalises units, and validates per methodology.",
    en: "Upload a CSV. The pipeline drops commercial columns at the boundary, normalises units, and validates per methodology.",
  },
  "upload.page.step1Title": { fr: "1. Pick a CSV file", en: "1. Pick a CSV file" },
  "upload.page.fileField": {
    fr: "CSV / TSV / TXT file",
    en: "CSV / TSV / TXT file",
  },
  "upload.page.parsingHeaders": {
    fr: "Parsing column headers…",
    en: "Parsing column headers…",
  },
  "upload.page.previewError": {
    fr: "Could not preview column mapping",
    en: "Could not preview column mapping",
  },
  "upload.page.step1bTitle": { fr: "1b. Column mapping", en: "1b. Column mapping" },
  "upload.page.step1bSubtitle": {
    fr: "Review suggested field mappings. Adjust any that look wrong before uploading.",
    en: "Review suggested field mappings. Adjust any that look wrong before uploading.",
  },
  "upload.page.duplicates": {
    fr: "Duplicate column headers detected: {headers}. Only the last value will be kept per row.",
    en: "Duplicate column headers detected: {headers}. Only the last value will be kept per row.",
  },
  "upload.page.missingPtTitle": {
    fr: "Champs Protein Tracker requis encore manquants : {fields}.",
    en: "Required Protein Tracker fields still missing: {fields}.",
  },
  "upload.page.missingPtBody": {
    fr: "Ces champs sont nécessaires pour calculer les volumes de protéines (totales, animales, végétales).",
    en: "These fields are needed to compute protein volumes (total, animal, plant).",
  },
  "upload.page.missingWwfTitle": {
    fr: "Champs WWF requis encore manquants : {fields}.",
    en: "Required WWF fields still missing: {fields}.",
  },
  "upload.page.missingWwfBody": {
    fr: "Ces champs sont nécessaires pour calculer les volumes WWF par groupe alimentaire.",
    en: "These fields are needed to compute WWF volumes per food group.",
  },
  "upload.page.noExternalId": {
    fr: "Aucune colonne d’identifiant produit détectée : Altera générera des identifiants internes pour cet upload (préfixés ",
    en: "No product ID column detected: Altera will generate internal IDs for this upload (prefixed ",
  },
  "upload.page.noExternalIdSuffix": {
    fr: ").",
    en: ").",
  },
  "upload.page.gramsHintPrefix": {
    fr: "Cette colonne semble contenir des grammes. Sélection recommandée :",
    en: "This column seems to contain grams. Recommended selection:",
  },
  "upload.page.gramsHintInsteadOf": {
    fr: " au lieu de",
    en: " instead of",
  },
  "upload.page.gramsHintSuffix": {
    fr: ". Aucune conversion automatique n’est appliquée tant que vous n’avez pas choisi la bonne unité.",
    en: ". No automatic conversion is applied until you pick the correct unit.",
  },
  "upload.page.uploadWithMapping": {
    fr: "Upload with this mapping",
    en: "Upload with this mapping",
  },

  // ---- Standalone busy labels ----
  "upload.page.busy.uploading": { fr: "Uploading…", en: "Uploading…" },
  "upload.page.busy.preparing": { fr: "Preparing…", en: "Preparing…" },
  "upload.page.busy.uploadingStorage": {
    fr: "Uploading to storage…",
    en: "Uploading to storage…",
  },
  "upload.page.busy.processing": { fr: "Processing…", en: "Processing…" },
  "upload.page.uploadFailed": { fr: "Upload failed", en: "Upload failed" },

  // ---- Ingestion report (standalone) ----
  "upload.page.step2Title": { fr: "2. Ingestion report", en: "2. Ingestion report" },
  "upload.page.duplicateOf": {
    fr: "This file appears to be a duplicate of a previous upload (same content). Processing continued, but you may want to verify this is intentional.",
    en: "This file appears to be a duplicate of a previous upload (same content). Processing continued, but you may want to verify this is intentional.",
  },
  "upload.page.rows": { fr: "Rows", en: "Rows" },
  "upload.page.products": { fr: "Products", en: "Products" },
  "upload.page.errors": { fr: "Errors", en: "Errors" },
  "upload.page.warnings": { fr: "Warnings", en: "Warnings" },
  "upload.page.fileSize": { fr: "File size", en: "File size" },
  "upload.page.droppedColumns": {
    fr: "Dropped commercial columns",
    en: "Dropped commercial columns",
  },
  "upload.page.errorsCount": { fr: "Errors ({n})", en: "Errors ({n})" },
  "upload.page.errorsHint": {
    fr: "Rows with errors were not ingested. Fix the CSV and re-upload.",
    en: "Rows with errors were not ingested. Fix the CSV and re-upload.",
  },
  "upload.page.errorRow": {
    fr: "row {row}: {code} — {message}",
    en: "row {row}: {code} — {message}",
  },

  // ---- Classify step (standalone) ----
  "upload.page.step3Title": { fr: "3. Classify", en: "3. Classify" },
  "upload.page.step3Subtitle": {
    fr: "Runs the deterministic rules engine. Unmatched products are queued for Altera review.",
    en: "Runs the deterministic rules engine. Unmatched products are queued for Altera review.",
  },
  "upload.page.classifying": { fr: "Classifying…", en: "Classifying…" },
  "upload.page.classifyPt": {
    fr: "Classify as Protein Tracker",
    en: "Classify as Protein Tracker",
  },
  "upload.page.classifyWwf": { fr: "Classify as WWF", en: "Classify as WWF" },
  "upload.page.classificationFailed": {
    fr: "Classification failed",
    en: "Classification failed",
  },
  "upload.page.jobLabel": { fr: "job {id}", en: "job {id}" },

  // ---- Classify result summary (standalone) ----
  "upload.page.rulesMatched": { fr: "Rules matched", en: "Rules matched" },
  "upload.page.aiAccepted": { fr: "AI accepted", en: "AI accepted" },
  "upload.page.passThrough": { fr: "Pass-through", en: "Pass-through" },
  "upload.page.collisions": { fr: "Collisions", en: "Collisions" },
  "upload.page.sentToReview": { fr: "Sent to review", en: "Sent to review" },
  "upload.page.aiClassifierSummary": {
    fr: "AI classifier: {attempted} attempted · {accepted} accepted · {review} sent to Altera review",
    en: "AI classifier: {attempted} attempted · {accepted} accepted · {review} sent to Altera review",
  },
  "upload.page.queuedReview": {
    fr: "{n} product{s} will be reviewed by the Altera team before the report is generated.",
    en: "{n} product{s} will be reviewed by the Altera team before the report is generated.",
  },

  // ---- WWF Step 2 (standalone) ----
  "upload.page.step4Title": {
    fr: "4. Step 2 ingredient attribution (WWF)",
    en: "4. Step 2 ingredient attribution (WWF)",
  },
  "upload.page.step4Subtitle": {
    fr: "Optional: upload a JSON file mapping own-brand composite products to their ingredients.",
    en: "Optional: upload a JSON file mapping own-brand composite products to their ingredients.",
  },
  "upload.page.step4Body": {
    fr: "Step 2 applies to own-brand composite products only. Branded composites are always reported at Step 1 (whole product weight) and are unaffected by this file. Uploading a new file replaces any previously stored Step 2 data for this project.",
    en: "Step 2 applies to own-brand composite products only. Branded composites are always reported at Step 1 (whole product weight) and are unaffected by this file. Uploading a new file replaces any previously stored Step 2 data for this project.",
  },
  "upload.page.step4Field": {
    fr: "Ingredient JSON file (.json, max 50 MB)",
    en: "Ingredient JSON file (.json, max 50 MB)",
  },
  "upload.page.uploadIngredients": {
    fr: "Upload ingredients",
    en: "Upload ingredients",
  },
  "upload.page.stored": { fr: "stored", en: "stored" },
  "upload.page.notStored": { fr: "not stored", en: "not stored" },
  "upload.page.ingredientsSavedReplaced": {
    fr: "Replaced previous data — ingredients saved for {n} product{s}",
    en: "Replaced previous data — ingredients saved for {n} product{s}",
  },
  "upload.page.ingredientsSaved": {
    fr: "Ingredients saved for {n} product{s}",
    en: "Ingredients saved for {n} product{s}",
  },
  "upload.page.rerunCalculation": {
    fr: "Re-run the calculation to apply these ingredients to the report.",
    en: "Re-run the calculation to apply these ingredients to the report.",
  },
  "upload.page.productsInFile": {
    fr: "Products in file",
    en: "Products in file",
  },
  "upload.page.ownBrandStored": {
    fr: "Own-brand stored",
    en: "Own-brand stored",
  },
  "upload.page.unknownProducts": {
    fr: "{n} product(s) not found in project — check external IDs.",
    en: "{n} product(s) not found in project — check external IDs.",
  },
  "upload.page.brandedComposites": {
    fr: "{n} branded composite(s): ingredients not stored. These products remain at Step 1 (whole product weight) only.",
    en: "{n} branded composite(s): ingredients not stored. These products remain at Step 1 (whole product weight) only.",
  },
  "upload.page.validationErrors": {
    fr: "Validation errors",
    en: "Validation errors",
  },

  // ---- Bottom navigation (standalone) ----
  "upload.page.backToProject": {
    fr: "← Back to project",
    en: "← Back to project",
  },
  "upload.page.reviewQueue": {
    fr: "Review queue ({n}) →",
    en: "Review queue ({n}) →",
  },
  "upload.page.calculate": { fr: "Calculate →", en: "Calculate →" },
};
