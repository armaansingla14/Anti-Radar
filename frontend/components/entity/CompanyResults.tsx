"use client";

import { CompanyTable } from "@/components/CompanyRow";
import { getCompanies, type QueryParams } from "@/lib/api";
import type { CompanySummary } from "@/lib/types";
import { LoadMore } from "./LoadMore";

/** Company table with offset pagination over GET /api/companies. */
export function CompanyResults({ initial, query, pageSize }: { initial: CompanySummary[]; query: QueryParams; pageSize: number }) {
  return (
    <LoadMore
      initial={initial}
      query={query}
      pageSize={pageSize}
      fetchPage={(p) => getCompanies(p)}
      render={(items) => <CompanyTable companies={items} caption="Companies on the radar" />}
      noun="companies"
    />
  );
}
