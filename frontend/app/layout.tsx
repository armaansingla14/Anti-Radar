import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono, Newsreader } from "next/font/google";
import { Footer } from "@/components/Footer";
import { KeyboardProvider } from "@/components/KeyboardProvider";
import { Masthead } from "@/components/Masthead";
import { ToastProvider } from "@/components/Toast";
import "./globals.css";

const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  style: ["normal", "italic"],
  axes: ["opsz"],
  display: "swap",
});

const inter = Inter({ variable: "--font-inter", subsets: ["latin"], display: "swap" });

const jetbrains = JetBrains_Mono({ variable: "--font-jetbrains", subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: { default: "Anti Radar", template: "%s · Anti Radar" },
  description: "Conviction Before Consensus. Anti Fund's private intelligence terminal.",
  robots: { index: false, follow: false },
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
        <ToastProvider>
          <KeyboardProvider>
            <Masthead />
            <main id="main" className="mx-auto w-full max-w-[1240px] flex-1 px-6 pt-10 md:px-10">
              {children}
            </main>
            <Footer />
          </KeyboardProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
