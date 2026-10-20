import type { Metadata } from "next";
import { connection } from "next/server";
import { StoredBriefPage } from "../StoredBriefPage";
import "../print.css";

export const metadata: Metadata = { title: "Today's brief" };

export default async function BriefTodayPage() {
  await connection();
  return <StoredBriefPage day="today" />;
}
