"use client";

/**
 * Phase Product-UX-A — lightweight, dependency-free i18n.
 *
 * SAFETY CONTRACT: this layer translates UI **labels only**. It never
 * touches API payload values, canonical mapping field names, CSV
 * header detection, methodology enum values, route paths, or stored
 * database values. Translation keys map to display strings; the rest
 * of the app continues to use canonical identifiers unchanged.
 *
 * No external i18n library — a typed dictionary + React context is
 * enough for the current surface area and avoids a heavy dependency.
 * Language preference persists in localStorage. Default: French.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Lang = "fr" | "en";

const STORAGE_KEY = "altera.lang";

// ---------------------------------------------------------------------------
// Dictionary. Keys are stable IDs; values are per-language display text.
// Only UI labels live here — never field values or canonicals.
// ---------------------------------------------------------------------------
const DICT: Record<string, { fr: string; en: string }> = {
  // Navigation
  "nav.projects": { fr: "Projets enseignes", en: "Retailer projects" },
  "nav.templates": { fr: "Templates", en: "Templates" },
  "nav.admin": { fr: "Admin", en: "Admin" },
  "nav.workspace": { fr: "Espace de travail", en: "Workspace" },
  "nav.helper.title": { fr: "Parcours guidé", en: "Guided workflow" },
  "nav.helper.body": {
    fr: "Importez, classez, validez et calculez vos ratios directement dans un projet.",
    en: "Import, classify, review and compute your ratios inside a project.",
  },
  // Account
  "account.signout": { fr: "Déconnexion", en: "Sign out" },
  // Projects page
  "projects.eyebrow": { fr: "Projets", en: "Projects" },
  "projects.title": { fr: "Vos projets", en: "Your projects" },
  "projects.subtitle": {
    fr: "Chaque projet fixe les méthodologies (Protein Tracker, WWF) et une période de reporting. Les imports et les calculs vivent dans un projet.",
    en: "Each project pins the methodologies (Protein Tracker, WWF) and a reporting period. Uploads and runs live inside a project.",
  },
  "projects.all": { fr: "Tous les projets", en: "All projects" },
  "projects.new": { fr: "Nouveau projet", en: "New project" },
  "projects.empty.title": {
    fr: "Aucun projet pour l'instant",
    en: "No projects yet",
  },
  "projects.empty.body": {
    fr: "Créez un projet pour analyser un pays, une enseigne ou un périmètre produit.",
    en: "Create a project to analyse a country, a retailer or a product scope.",
  },
  "projects.open": { fr: "Ouvrir le projet", en: "Open project" },
  "projects.meta.uploads": { fr: "imports", en: "uploads" },
  "projects.meta.review": { fr: "en revue", en: "in review" },
  "projects.meta.runs": { fr: "calculs", en: "runs" },
  // Templates page
  "templates.eyebrow": { fr: "Données", en: "Data" },
  "templates.title": { fr: "Templates d'import", en: "Import templates" },
  "templates.subtitle": {
    fr: "Choisissez le modèle adapté à votre méthodologie et téléchargez-le en CSV ou Excel. Les colonnes correspondent au mapping automatique d'Altera — aucun renommage nécessaire.",
    en: "Pick the template that matches your methodology and download it as CSV or Excel. The columns match Altera's auto-mapping — no renaming required.",
  },
  "templates.who": { fr: "Pour qui", en: "Who it's for" },
  "templates.downloadCsv": { fr: "Télécharger CSV", en: "Download CSV" },
  "templates.downloadExcel": { fr: "Télécharger Excel", en: "Download Excel" },
  "templates.badge.ratio": { fr: "Ratio protéines", en: "Protein ratio" },
  "templates.badge.groups": { fr: "Groupes WWF", en: "WWF groups" },
  "templates.badge.complete": { fr: "Complet", en: "Complete" },
  "templates.pt.title": { fr: "Protein Tracker", en: "Protein Tracker" },
  "templates.pt.enables": {
    fr: "Calcule le ratio protéines végétales / totales de votre assortiment.",
    en: "Computes the plant / total protein ratio of your assortment.",
  },
  "templates.pt.who": {
    fr: "Enseignes qui suivent la transition protéique.",
    en: "Retailers tracking the protein transition.",
  },
  "templates.wwf.title": { fr: "WWF Planet-Based Diets", en: "WWF Planet-Based Diets" },
  "templates.wwf.enables": {
    fr: "Répartit vos volumes de vente par groupe alimentaire (FG1–FG7) et bucket composite.",
    en: "Splits your sales volumes across food groups (FG1–FG7) and composite buckets.",
  },
  "templates.wwf.who": {
    fr: "Enseignes qui mesurent un régime planet-based.",
    en: "Retailers measuring a planet-based diet.",
  },
  "templates.combined.title": {
    fr: "Protein Tracker + WWF",
    en: "Protein Tracker + WWF",
  },
  "templates.combined.enables": {
    fr: "Mène les deux analyses sur un seul import — un seul fichier à préparer.",
    en: "Runs both analyses from a single upload — one file to prepare.",
  },
  "templates.combined.who": {
    fr: "Enseignes qui veulent une vue complète en une fois.",
    en: "Retailers wanting the full picture in one go.",
  },
  "templates.privacy.title": { fr: "Confidentialité", en: "Privacy" },
  "templates.privacy.body": {
    fr: "Les volumes, ventes, poids et données nutritionnelles sont utilisés pour les calculs, mais ne sont jamais envoyés à l'IA.",
    en: "Volumes, sales, weights and nutrition data are used for calculation, but are never sent to the AI.",
  },
  "templates.tip.title": { fr: "Éviter les erreurs d'import", en: "Avoid upload errors" },
  "templates.tip.body": {
    fr: "Utilisez ces templates pour éviter les erreurs de mapping. Gardez la première ligne d'en-têtes et un encodage UTF-8.",
    en: "Use these templates to avoid mapping errors. Keep the header row and UTF-8 encoding.",
  },
  // Phase Product-UX-D — clarify WWF scope and NEVO's role.
  "templates.wwfScope.title": {
    fr: "Méthodologie WWF — Step 1 (niveau produit)",
    en: "WWF methodology — Step 1 (product-level)",
  },
  "templates.wwfScope.body": {
    fr: "Les produits composés sont comptés à leur poids total et classés dans les buckets meat-based, seafood-based, vegetarian ou vegan, puis cartographiés sur les groupes FG1–FG7. Le Step 2 ingrédient-level (décomposition par recette pour les produits marque propre) n'est pas encore activé : il nécessite des données de recette détaillées.",
    en: "Composite products are counted using their whole product weight and assigned to meat-based, seafood-based, vegetarian or vegan buckets, then mapped onto food groups FG1–FG7. Step 2 ingredient-level reporting (recipe decomposition for own-brand composites) is not enabled yet; it requires detailed recipe data.",
  },
  "templates.nevoNote.title": {
    fr: "À propos de NEVO",
    en: "About NEVO",
  },
  "templates.nevoNote.body": {
    fr: "NEVO sert à l'enrichissement nutritionnel / protéines (surtout pour Protein Tracker). Il fournit une composition alimentaire de référence, pas les poids d'ingrédients de recette des enseignes — il ne peut donc pas produire à lui seul un Step 2 WWF ingrédient-level.",
    en: "NEVO is used for nutrition/protein enrichment (mainly for Protein Tracker). It provides reference food composition, not retailer recipe-level ingredient weights — so it cannot on its own produce WWF Step 2 ingredient-level breakdowns.",
  },
  // Language switch
  "lang.fr": { fr: "FR", en: "FR" },
  "lang.en": { fr: "EN", en: "EN" },
};

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: keyof typeof DICT | string) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("fr");

  // Hydrate from localStorage after mount (avoids SSR mismatch — the
  // server always renders the default FR, then the client adopts the
  // stored preference).
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "fr" || stored === "en") setLangState(stored);
    } catch {
      /* ignore */
    }
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      window.localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
  }, []);

  const t = useCallback(
    (key: string) => {
      const entry = DICT[key];
      if (!entry) return key;
      return entry[lang];
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (ctx) return ctx;
  // Defensive fallback so a component rendered outside the provider
  // (e.g. an isolated test) still works in French.
  return {
    lang: "fr",
    setLang: () => {},
    t: (key: string) => DICT[key]?.fr ?? key,
  };
}

/** Convenience hook returning just the translate function. */
export function useT() {
  return useI18n().t;
}
