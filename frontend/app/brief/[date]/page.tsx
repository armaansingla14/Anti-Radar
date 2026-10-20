import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { connection } from "next/server";
import { briefDateLabel, parseBriefDateParam } from "@/lib/api-pages";
import { StoredBriefPage } from "../StoredBriefPage";
import "../print.css";

export async function generateMetadata({ params }: PageProps<"/brief/[date]">): Promise<Metadata> {
  const { date } = await params;
  const d = parseBriefDateParam(date);
  return { title: d && d !== "today" ? `Brief · ${briefDateLabel(d)}` : "Brief" };
}

export default async function BriefDatePage({ params }: PageProps<"/brief/[date]">) {
  await connection();
  const { date } = await params;
  const d = parseBriefDateParam(date);
  if (!d) notFound();
  if (d === "today") redirect("/brief/today");
  return <StoredBriefPage day={d} />;
}
