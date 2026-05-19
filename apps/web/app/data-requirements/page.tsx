"use client";

import { useAuth } from "@/lib/auth-context";
import { getApiBaseUrl } from "@/lib/api";
import { Card, CardHeader } from "@/components/ui";

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

interface TemplateInfo {
  label: string;
  description: string;
  path: string;
  filename: string;
}

const TEMPLATES: TemplateInfo[] = [
  {
    label: "Protein Tracker template",
    description: "All required and recommended columns for Protein Tracker methodology.",
    path: "/api/v1/templates/protein-tracker.csv",
    filename: "protein_tracker_template.csv",
  },
  {
    label: "WWF template",
    description: "All required and recommended columns for the WWF methodology.",
    path: "/api/v1/templates/wwf.csv",
    filename: "wwf_template.csv",
  },
  {
    label: "WWF Step 2 ingredients template",
    description: "Data collection template for own-brand composite ingredient attribution.",
    path: "/api/v1/templates/wwf-step2-ingredients.csv",
    filename: "wwf_step2_ingredients_template.csv",
  },
  {
    label: "Business assumptions template (optional)",
    description: "Opportunity modelling inputs: market share, margins, growth assumptions.",
    path: "/api/v1/templates/business-assumptions.csv",
    filename: "business_assumptions_template.csv",
  },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DataRequirementsPage() {
  const { accessToken } = useAuth();

  async function downloadTemplate(template: TemplateInfo) {
    try {
      const headers: Record<string, string> = {};
      if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
      const res = await fetch(`${getApiBaseUrl()}${template.path}`, { headers });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = template.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Download failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Data Requirements</h1>
        <p className="mt-1 text-sm text-gray-600">
          What data you need to provide, and what Altera does with it.
        </p>
      </div>

      {/* Download templates */}
      <Card>
        <CardHeader title="Download templates" />
        <p className="mt-2 text-sm text-gray-500">
          Use these templates to prepare your upload files. Column names and formats match exactly
          what the ingestion pipeline expects.
        </p>
        <ul className="mt-4 divide-y divide-gray-100">
          {TEMPLATES.map((t) => (
            <li key={t.path} className="flex items-start justify-between py-3">
              <div>
                <p className="text-sm font-medium text-gray-800">{t.label}</p>
                <p className="mt-0.5 text-xs text-gray-500">{t.description}</p>
              </div>
              <button
                onClick={() => downloadTemplate(t)}
                className="ml-4 shrink-0 rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
              >
                Download CSV
              </button>
            </li>
          ))}
        </ul>
      </Card>

      {/* Protein Tracker */}
      <Card>
        <CardHeader title="Protein Tracker — what you need to provide" />
        <div className="mt-3 space-y-4 text-sm text-gray-700">
          <FieldTable
            required={[
              { field: "external_product_id", desc: "Your internal SKU or product code." },
              { field: "product_name", desc: "Product name as it appears on shelf." },
              { field: "weight_per_item_kg", desc: "Item weight in kilograms (e.g. 0.400 for 400 g)." },
              { field: "items_purchased", desc: "Number of units purchased in the reporting period." },
            ]}
            recommended={[
              { field: "protein_pct", desc: "Protein as % of product weight from the nutrition label. If missing, Altera can enrich from the ANSES CIQUAL reference table — see note below." },
              { field: "brand", desc: "Brand name." },
              { field: "retailer_category / retailer_subcategory", desc: "Your internal category hierarchy. Used for AI classification." },
              { field: "ingredients_text", desc: "Full ingredient list from the label. Sent to AI for classification." },
              { field: "is_own_brand", desc: "true or false. Own-brand products get closer scrutiny." },
              { field: "ean", desc: "EAN / GTIN barcode for future Open Food Facts look-up." },
            ]}
            optional={[
              { field: "labels", desc: "Pipe-separated product claims: organic|vegan|gluten-free" },
              { field: "country", desc: "ISO 3166-1 alpha-2 country code (FR, GB, NL, …)" },
              { field: "language", desc: "ISO 639-1 language code (fr, en, nl, …)" },
              { field: "reporting_period", desc: "e.g. 2024-Q4. For your own reference." },
            ]}
          />

          <NutritionNote />
          <AIPrivacyNote />
        </div>
      </Card>

      {/* WWF */}
      <Card>
        <CardHeader title="WWF — what you need to provide" />
        <div className="mt-3 space-y-4 text-sm text-gray-700">
          <FieldTable
            required={[
              { field: "external_product_id", desc: "Your internal SKU or product code." },
              { field: "product_name", desc: "Product name as it appears on shelf." },
              { field: "weight_per_item_kg", desc: "Item weight in kilograms." },
              { field: "items_sold", desc: "Number of units sold in the reporting period." },
              { field: "is_own_brand", desc: "true or false." },
              { field: "retail_channel", desc: "Product type: fresh | grocery_ambient | frozen" },
            ]}
            recommended={[
              { field: "ingredients_text", desc: "Required for Step 2 composite attribution." },
              { field: "brand", desc: "Brand name." },
              { field: "retailer_category / retailer_subcategory", desc: "Used for AI food-group classification." },
              { field: "ean", desc: "EAN / GTIN barcode." },
            ]}
            optional={[
              { field: "labels", desc: "Pipe-separated product claims." },
              { field: "country / language", desc: "For AI context." },
              { field: "reporting_period", desc: "For your own reference." },
            ]}
          />

          <section>
            <h3 className="font-medium">WWF Step 2 — own-brand composite ingredients</h3>
            <p className="mt-1 text-gray-600">
              Step 2 applies only to <strong>own-brand composite products</strong> (products
              classified as composite by the AI). For each such product you can optionally provide
              ingredient-level food-group attribution. This improves the accuracy of the WWF
              whole-diet score.
            </p>
            <p className="mt-1 text-gray-600">
              Use the WWF Step 2 template to organise your data. The actual upload format is JSON —
              see your Altera methodology contact for details.
            </p>
            <p className="mt-1 text-gray-600">
              Branded composites are handled at Step 1 (whole-product weight) only.
            </p>
          </section>
        </div>
      </Card>

      {/* Business assumptions */}
      <Card>
        <CardHeader title="Business assumptions (optional)" />
        <p className="mt-2 text-sm text-gray-600">
          Optional inputs for opportunity modelling and financial impact scenarios. None of these
          fields affect the methodology calculation — they are used only in the scenario modelling
          section. All fields are optional; leave blank if not applicable.
        </p>
        <ul className="mt-3 list-inside list-disc space-y-1 text-sm text-gray-600">
          <li>Total food sales, protein basket sales</li>
          <li>Current and target plant/animal protein share</li>
          <li>Private-label share, gross margin assumptions</li>
          <li>Plant-based growth and meat price inflation assumptions</li>
        </ul>
      </Card>

      {/* What Altera reviews */}
      <Card>
        <CardHeader title="What Altera reviews manually" />
        <ul className="mt-3 list-inside list-disc space-y-1 text-sm text-gray-600">
          <li>Products where the AI cannot confidently classify the PT group or WWF food group.</li>
          <li>
            Products where <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">protein_pct</code>{" "}
            is missing and no CIQUAL reference match is found.
          </li>
          <li>Products with conflicting classification signals (rule collision).</li>
          <li>Composite products where the ingredient split cannot be determined automatically.</li>
        </ul>
        <p className="mt-2 text-sm text-gray-500">
          Altera methodology reviewers will contact you if manual input is needed before the report
          can be finalised.
        </p>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface FieldRow {
  field: string;
  desc: string;
}

function FieldTable({
  required,
  recommended,
  optional,
}: {
  required: FieldRow[];
  recommended: FieldRow[];
  optional?: FieldRow[];
}) {
  return (
    <div className="space-y-3">
      <FieldSection label="Required" accent="rose" rows={required} />
      <FieldSection label="Recommended" accent="amber" rows={recommended} />
      {optional && optional.length > 0 && (
        <FieldSection label="Optional" accent="gray" rows={optional} />
      )}
    </div>
  );
}

function FieldSection({
  label,
  accent,
  rows,
}: {
  label: string;
  accent: "rose" | "amber" | "gray";
  rows: FieldRow[];
}) {
  const colors = {
    rose: "border-rose-200 bg-rose-50 text-rose-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    gray: "border-gray-200 bg-gray-50 text-gray-600",
  }[accent];

  return (
    <div>
      <div
        className={`inline-block rounded border px-2 py-0.5 text-xs font-medium uppercase tracking-wider ${colors}`}
      >
        {label}
      </div>
      <table className="mt-2 w-full text-xs">
        <tbody className="divide-y divide-gray-100">
          {rows.map((r) => (
            <tr key={r.field}>
              <td className="py-1.5 pr-4 font-mono font-medium text-gray-800 align-top w-52">
                {r.field}
              </td>
              <td className="py-1.5 text-gray-600 align-top">{r.desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NutritionNote() {
  return (
    <section className="rounded-md border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-800">
      <p className="font-medium">About missing protein % (protein_pct)</p>
      <p className="mt-1">
        Providing <code className="rounded bg-blue-100 px-1">protein_pct</code> from the product
        nutrition label is strongly recommended and gives the most accurate Protein Tracker result.
      </p>
      <p className="mt-1">
        If <code className="rounded bg-blue-100 px-1">protein_pct</code> is missing, Altera can
        enrich it from the{" "}
        <strong>ANSES CIQUAL 2025 reference table</strong> (a national French food composition
        database). CIQUAL values are analytical averages for food categories — not label data
        for your specific SKUs. When CIQUAL values are used, they will be disclosed in the report
        with source and confidence level.
      </p>
      <p className="mt-1">
        Products where neither retailer label data nor a CIQUAL match is available will be sent to
        Altera for manual review.
      </p>
      <p className="mt-2 text-blue-600">
        Attribution: Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/
      </p>
    </section>
  );
}

function AIPrivacyNote() {
  return (
    <section className="rounded-md border border-gray-100 bg-gray-50 px-4 py-3 text-xs text-gray-600">
      <p className="font-medium text-gray-700">What is sent to AI — and what is not</p>
      <p className="mt-1">
        The AI classifier receives only product descriptors needed for classification:
        product name, brand, retailer category/subcategory, and ingredients text.
      </p>
      <p className="mt-1 text-rose-700 font-medium">
        The following fields are NEVER sent to AI:
      </p>
      <ul className="mt-1 list-inside list-disc space-y-0.5">
        <li>items_purchased / items_sold (sales volumes)</li>
        <li>weight_per_item_kg (unit weight)</li>
        <li>protein_pct (nutrition label data)</li>
        <li>Any revenue, margin, supplier, or pricing data</li>
      </ul>
    </section>
  );
}
