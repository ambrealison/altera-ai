/**
 * Phase Product-UX-A — CSV import template definitions.
 *
 * Header names are chosen to match Altera's auto-mapping synonyms
 * exactly (verified against ``ingestion/mapping.py``), so a downloaded
 * template imports with zero "missing required field" warnings:
 *   product_id            → external_product_id
 *   product_name          → product_name
 *   raw_product_category  → retailer_category
 *   ingredient_declaration_simulated → ingredients_text
 *   pack_weight_g         → weight_per_item_g
 *   unit_purchased        → items_purchased   (Protein Tracker volume)
 *   units_sold            → items_sold        (WWF volume)
 *   retail_channel        → retail_channel    (WWF)
 *   brand_type            → is_own_brand      (WWF)
 *   protein_total_g_per_100g  → protein_pct
 *   protein_plant_g_per_100g  → plant_protein_pct
 *   protein_animal_g_per_100g → animal_protein_pct
 *   label_claims_notes    → labels
 *
 * IMPORTANT: these are display/template helpers only — they never feed
 * back into the mapping logic, which uses canonical identifiers.
 */

export type TemplateKind = "protein_tracker" | "wwf" | "combined";

export interface TemplateDef {
  kind: TemplateKind;
  /** CSV column headers in order. */
  headers: string[];
  /** Two generic example rows (safe, non-commercial sample values). */
  rows: string[][];
  /** Field names to display as "required" (label only). */
  requiredFields: string[];
  /** Field names to display as "optional" (label only). */
  optionalFields: string[];
}

const PT_HEADERS = [
  "product_id",
  "product_name",
  "raw_product_category",
  "ingredient_declaration_simulated",
  "pack_weight_g",
  "unit_purchased",
  "protein_total_g_per_100g",
  "protein_plant_g_per_100g",
  "protein_animal_g_per_100g",
  "protein_split_known",
  "brand_type",
  "label_claims_notes",
];

const WWF_HEADERS = [
  "product_id",
  "product_name",
  "raw_product_category",
  "ingredient_declaration_simulated",
  "pack_weight_g",
  "units_sold",
  "retail_channel",
  "brand_type",
  "sales_weight_kg",
  "label_claims_notes",
];

const COMBINED_HEADERS = [
  "product_id",
  "product_name",
  "raw_product_category",
  "ingredient_declaration_simulated",
  "pack_weight_g",
  "unit_purchased",
  "units_sold",
  "retail_channel",
  "brand_type",
  "protein_total_g_per_100g",
  "protein_plant_g_per_100g",
  "protein_animal_g_per_100g",
  "protein_split_known",
  "sales_weight_kg",
  "label_claims_notes",
];

export const TEMPLATES: Record<TemplateKind, TemplateDef> = {
  protein_tracker: {
    kind: "protein_tracker",
    headers: PT_HEADERS,
    rows: [
      [
        "SKU-0001",
        "Lentilles vertes 500g",
        "Épicerie / Légumes secs",
        "Lentilles vertes",
        "500",
        "1200",
        "9.0",
        "9.0",
        "0.0",
        "true",
        "Own brand",
        "Bio",
      ],
      [
        "SKU-0002",
        "Filet de poulet 400g",
        "Boucherie / Volaille",
        "Filet de poulet",
        "400",
        "850",
        "23.0",
        "0.0",
        "23.0",
        "true",
        "National brand",
        "",
      ],
    ],
    requiredFields: [
      "product_name",
      "pack_weight_g",
      "unit_purchased",
      "protein_total_g_per_100g",
    ],
    optionalFields: [
      "product_id",
      "raw_product_category",
      "ingredient_declaration_simulated",
      "protein_plant_g_per_100g",
      "protein_animal_g_per_100g",
      "protein_split_known",
      "brand_type",
      "label_claims_notes",
    ],
  },
  wwf: {
    kind: "wwf",
    headers: WWF_HEADERS,
    rows: [
      [
        "SKU-0001",
        "Lentilles vertes 500g",
        "Épicerie / Légumes secs",
        "Lentilles vertes",
        "500",
        "1200",
        "grocery_ambient",
        "Own brand",
        "600",
        "Bio",
      ],
      [
        "SKU-0002",
        "Filet de saumon 200g",
        "Marée / Poisson frais",
        "Saumon atlantique",
        "200",
        "640",
        "fresh",
        "National brand",
        "128",
        "",
      ],
    ],
    requiredFields: [
      "product_name",
      "pack_weight_g",
      "units_sold",
      "retail_channel",
      "brand_type",
    ],
    optionalFields: [
      "product_id",
      "raw_product_category",
      "ingredient_declaration_simulated",
      "sales_weight_kg",
      "label_claims_notes",
    ],
  },
  combined: {
    kind: "combined",
    headers: COMBINED_HEADERS,
    rows: [
      [
        "SKU-0001",
        "Lentilles vertes 500g",
        "Épicerie / Légumes secs",
        "Lentilles vertes",
        "500",
        "1200",
        "1200",
        "grocery_ambient",
        "Own brand",
        "9.0",
        "9.0",
        "0.0",
        "true",
        "600",
        "Bio",
      ],
      [
        "SKU-0002",
        "Yaourt nature 4x125g",
        "Crèmerie / Yaourts",
        "Lait, ferments lactiques",
        "500",
        "980",
        "980",
        "fresh",
        "National brand",
        "4.0",
        "0.0",
        "4.0",
        "true",
        "490",
        "",
      ],
    ],
    requiredFields: [
      "product_name",
      "pack_weight_g",
      "unit_purchased",
      "units_sold",
      "retail_channel",
      "brand_type",
      "protein_total_g_per_100g",
    ],
    optionalFields: [
      "product_id",
      "raw_product_category",
      "ingredient_declaration_simulated",
      "protein_plant_g_per_100g",
      "protein_animal_g_per_100g",
      "protein_split_known",
      "sales_weight_kg",
      "label_claims_notes",
    ],
  },
};

/** Serialise a template to CSV text (RFC-4180-ish: quote cells with
 *  commas/quotes/newlines). */
export function templateToCsv(def: TemplateDef): string {
  const esc = (cell: string) =>
    /[",\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell;
  const lines = [def.headers, ...def.rows].map((r) => r.map(esc).join(","));
  return lines.join("\n") + "\n";
}
