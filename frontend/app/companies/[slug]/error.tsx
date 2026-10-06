"use client";

import { RouteError } from "@/components/entity/RouteError";

export default function ErrorBoundary({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError error={error} retry={retry} what="this company" back={{ href: "/companies", label: "← All companies" }} />;
}
