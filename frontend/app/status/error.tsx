"use client";

import { RouteError } from "@/components/insight/RouteError";

export default function Error({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <RouteError error={error} retry={retry} what="system status" endpoint="GET /api/status" />;
}
