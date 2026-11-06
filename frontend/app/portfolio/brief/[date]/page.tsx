import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PortfolioBriefPage } from "@/components/portfolio/BriefPage";
import "../../../brief/print.css";

export async function generateMetadata({ params }: PageProps<"/portfolio/brief/[date]">): Promise<Metadata> {
  const { date } = await params;
  return { title: `Portfolio brief · ${date}` };
}

export default async function Page({ params }: PageProps<"/portfolio/brief/[date]">) {
  const { date } = await params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) notFound();
  return <PortfolioBriefPage day={date} />;
}
