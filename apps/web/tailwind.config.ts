import type { Config } from "tailwindcss";

/**
 * Phase Design-A — premium climate-SaaS design tokens.
 *
 * The palette is an original "food transition / ESG" direction —
 * deep forest ink, a fresh (not neon) green accent, soft mint and
 * cream surfaces. It is intentionally NOT a copy of any specific
 * competitor's brand colours.
 *
 * The legacy ``brand`` ramp is preserved (every existing
 * ``bg-brand-600`` / ``text-brand-700`` class keeps working) but
 * retuned to the new fresh-green family so the whole app shifts
 * tone without touching call-sites. New semantic families
 * (``forest`` / ``mint`` / ``lime`` / ``cream`` / ``warn`` /
 * ``danger`` / ``ink`` / ``line``) are added for the redesign.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Fresh green accent ramp (retuned legacy "brand").
        brand: {
          50: "#eafaf1",
          100: "#d2f4e1",
          200: "#a9e9c6",
          300: "#76d8a5",
          400: "#4ec78a",
          500: "#39b980",
          600: "#2c9d6b",
          700: "#237d56",
          800: "#1d6347",
          900: "#173f30",
        },
        // Deep forest ink — primary text + dark surfaces.
        forest: {
          50: "#eef3f1",
          100: "#d6e2dc",
          500: "#274a3d",
          700: "#183228",
          900: "#10251f",
        },
        // Soft mint surface family.
        mint: {
          50: "#f2fbf6",
          100: "#e6f7ee",
          200: "#d2efe0",
        },
        // Pale lime / spring accent.
        lime: {
          100: "#eef9dd",
          200: "#ddf7c7",
          300: "#c7ed9f",
        },
        // Warm cream / off-white.
        cream: {
          50: "#faf8f1",
          100: "#f5f1e6",
        },
        // Soft amber — non-blocking warnings ("à vérifier").
        warn: {
          50: "#fef6e7",
          100: "#fdeccb",
          400: "#f6b95b",
          500: "#e9a23f",
          700: "#a9721f",
        },
        // Soft red — true blockers only.
        danger: {
          50: "#fdeded",
          100: "#fbdada",
          400: "#ef6b6b",
          500: "#e34d4d",
          700: "#a52f2f",
        },
        // Neutral slate/stone for borders + muted text.
        ink: {
          DEFAULT: "#10251f",
          muted: "#5c6b64",
          soft: "#8a958f",
        },
        line: {
          DEFAULT: "#e4ebe7",
          soft: "#eef3f0",
        },
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
        "3xl": "1.5rem",
      },
      boxShadow: {
        // Soft, layered elevation scale for premium cards.
        card: "0 1px 2px rgba(16, 37, 31, 0.04), 0 4px 16px rgba(16, 37, 31, 0.05)",
        "card-hover":
          "0 2px 4px rgba(16, 37, 31, 0.06), 0 12px 32px rgba(16, 37, 31, 0.09)",
        soft: "0 1px 2px rgba(16, 37, 31, 0.05)",
        ring: "0 0 0 3px rgba(57, 185, 128, 0.18)",
      },
      backgroundImage: {
        // Subtle page + hero gradients.
        "mint-fade":
          "radial-gradient(1200px 600px at 15% -10%, #e6f7ee 0%, rgba(242, 251, 246, 0) 55%), radial-gradient(1000px 500px at 100% 0%, #f5f1e6 0%, rgba(250, 248, 241, 0) 50%), linear-gradient(180deg, #f7fbf9 0%, #f4f9f6 100%)",
        "forest-hero":
          "linear-gradient(135deg, #10251f 0%, #1d6347 60%, #2c9d6b 100%)",
        "lime-soft": "linear-gradient(135deg, #f2fbf6 0%, #eef9dd 100%)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.97)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
        "scale-in": "scale-in 0.16s ease-out",
        shimmer: "shimmer 1.4s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
