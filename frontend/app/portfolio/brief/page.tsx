import type { Metadata } from "next";
import { PortfolioBriefPage } from "@/components/portfolio/BriefPage";
import "../../brief/print.css";

export const metadata: Metadata = { title: "Portfolio brief" };

export default async function Page() {
  return <PortfolioBriefPage />;
}
