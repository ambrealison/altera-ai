import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth-context";
import { AuthGate } from "@/components/AuthGate";
import { LanguageProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "Altera.ai",
  description:
    "Retailer protein-ratio + planet-based-diet analysis (Protein Tracker and WWF methodologies).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // Phase Design-A/B — let globals.css paint the climate gradient
    // (the previous bg-gray-50 override hid it). Default lang FR.
    <html lang="fr">
      <body className="min-h-screen text-forest-900 antialiased">
        <LanguageProvider>
          <AuthProvider>
            <AuthGate>{children}</AuthGate>
          </AuthProvider>
        </LanguageProvider>
      </body>
    </html>
  );
}
