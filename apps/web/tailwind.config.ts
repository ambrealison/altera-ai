import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f3faf5",
          100: "#dff2e3",
          500: "#3aa663",
          600: "#2e8a51",
          700: "#246d40",
        },
      },
    },
  },
  plugins: [],
};

export default config;
