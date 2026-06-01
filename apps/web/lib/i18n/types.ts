/**
 * Phase Product-UX-E — shared types for the modular i18n dictionary.
 *
 * The dictionary is split into per-surface modules (common, workflow,
 * upload, validation, report, …) so translation work on different
 * surfaces never collides on a single file. Each module exports an
 * ``I18nDict``; ``lib/i18n.tsx`` merges them.
 *
 * SAFETY CONTRACT (unchanged): these are UI labels only. Never put
 * API payload values, canonical mapping field names, CSV headers,
 * methodology enum values, route paths, or stored DB values here.
 */
export interface I18nEntry {
  fr: string;
  en: string;
}

export type I18nDict = Record<string, I18nEntry>;
