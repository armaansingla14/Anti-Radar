"use client";

import { RouteError } from "@/components/entity/RouteError";

export default function ErrorBoundary({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError error={error} retry={retry} what="this diligence workspace" back={{ href: "/diligence", label: "← All workspaces" }} />;
}
