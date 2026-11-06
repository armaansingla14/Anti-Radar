"use client";

import { RouteError } from "@/components/insight/RouteError";

export default function ErrorBoundary({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError error={error} retry={retry} what="portfolio events" endpoint="/api/portfolio/events" />;
}
