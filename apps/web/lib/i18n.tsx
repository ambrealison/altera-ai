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
  "nav.settings": { fr: "Paramètres", en: "Settings" },
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
    fr: "Téléchargez le modèle CSV adapté à votre méthodologie. Les colonnes correspondent au mapping automatique d'Altera — aucun renommage nécessaire.",
    en: "Download the CSV template that matches your methodology. The columns match Altera's auto-mapping — no renaming required.",
  },
  "templates.pt.title": { fr: "Protein Tracker", en: "Protein Tracker" },
  "templates.pt.when": {
    fr: "Pour analyser le ratio protéines végétales / totales.",
    en: "To analyse the plant / total protein ratio.",
  },
  "templates.wwf.title": { fr: "WWF Planet-Based Diets", en: "WWF Planet-Based Diets" },
  "templates.wwf.when": {
    fr: "Pour répartir les volumes par groupe alimentaire (FG1–FG7).",
    en: "To split volumes across food groups (FG1–FG7).",
  },
  "templates.combined.title": {
    fr: "Protein Tracker + WWF",
    en: "Protein Tracker + WWF",
  },
  "templates.combined.when": {
    fr: "Pour mener les deux méthodologies sur un même import.",
    en: "To run both methodologies from a single upload.",
  },
  "templates.required": { fr: "Champs requis", en: "Required fields" },
  "templates.optional": { fr: "Champs optionnels", en: "Optional fields" },
  "templates.download": { fr: "Télécharger le CSV", en: "Download CSV" },
  "templates.copyHeaders": { fr: "Copier les en-têtes", en: "Copy headers" },
  "templates.copied": { fr: "Copié ✓", en: "Copied ✓" },
  "templates.privacy.title": { fr: "Confidentialité", en: "Privacy" },
  "templates.privacy.body": {
    fr: "Les volumes de vente, poids et valeurs nutritionnelles servent au calcul, mais les champs commerciaux ne sont jamais envoyés à l'IA.",
    en: "Sales volumes, weights and nutrition values are used for calculation, but commercial fields are never sent to the AI.",
  },
  "templates.tip.title": { fr: "Éviter les erreurs d'import", en: "Avoid upload errors" },
  "templates.tip.body": {
    fr: "Gardez la première ligne d'en-têtes, un encodage UTF-8, et une valeur par cellule. Les identifiants manquants sont générés automatiquement.",
    en: "Keep the header row, UTF-8 encoding, and one value per cell. Missing IDs are generated automatically.",
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
