import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { Footer } from "@/components/Footer";
import { KeyboardProvider } from "@/components/KeyboardProvider";
import { Masthead } from "@/components/Masthead";
import { StaticSnapshotProvider } from "@/components/StaticSnapshot";
import { dataSource } from "@/lib/dataSource";
import { ToastProvider } from "@/components/Toast";
import "./globals.css";

// Self-hosted (OFL) variable fonts: builds never depend on fetching Google Fonts.
const newsreader = localFont({
  variable: "--font-newsreader",
  display: "swap",
  src: [
    { path: "../node_modules/@fontsource-variable/newsreader/files/newsreader-latin-opsz-normal.woff2", style: "normal", weight: "200 800" },
    { path: "../node_modules/@fontsource-variable/newsreader/files/newsreader-latin-opsz-italic.woff2", style: "italic", weight: "200 800" },
  ],
});

const inter = localFont({
  variable: "--font-inter",
  display: "swap",
  src: [{ path: "../node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2", style: "normal", weight: "100 900" }],
});

const jetbrains = localFont({
  variable: "--font-jetbrains",
  display: "swap",
  src: [{ path: "../node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2", style: "normal", weight: "100 800" }],
});

const SITE_TITLE = "Anti Radar — Conviction Before Consensus";
const SITE_DESCRIPTION =
  "An intelligence engine for discovering technical founders and emerging companies through GitHub, research, hiring, web, and social signals.";
const siteUrl =
  process.env.SITE_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}` : "https://anti-radar-one.vercel.app");

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: { default: SITE_TITLE, template: "%s · Anti Radar" },
  description: SITE_DESCRIPTION,
  openGraph: { type: "website", siteName: "Anti Radar", title: SITE_TITLE, description: SITE_DESCRIPTION, url: "/" },
  twitter: { card: "summary_large_image", title: SITE_TITLE, description: SITE_DESCRIPTION },
};

export const viewport: Viewport = { themeColor: "#f3efe6" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${newsreader.variable} ${inter.variable} ${jetbrains.variable}`}>
      <body className="flex min-h-screen flex-col bg-paper text-ink">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[80] focus:bg-paper focus:px-3 focus:py-2 focus:font-mono focus:text-[11px]"
        >
          Skip to content
        </a>
        <StaticSnapshotProvider value={dataSource() === "snapshot"}>
        <ToastProvider>
          <KeyboardProvider>
            <Masthead />
            <main id="main" className="mx-auto w-full max-w-[1240px] flex-1 px-6 pt-10 md:px-10">
              {children}
            </main>
            <Footer />
          </KeyboardProvider>
        </ToastProvider>
        </StaticSnapshotProvider>
      </body>
    </html>
  );
}
