"use client";

import { RouteError } from "@/components/entity/RouteError";

export default function ErrorBoundary({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError error={error} retry={retry} what="this formation report" back={{ href: "/formation", label: "← Formation Radar" }} />;
}
