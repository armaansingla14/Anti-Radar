import type { Metadata } from "next";
import { CompanyResults } from "@/components/entity/CompanyResults";
import { IndexControls, type FilterDef } from "@/components/entity/IndexControls";
import { ScoreAnatomy } from "@/components/entity/ScoreAnatomy";
import { themeOptions } from "@/components/entity/themes";
import { EmptyState } from "@/components/EmptyState";
import { getCompanies, type QueryParams } from "@/lib/api";

export const metadata: Metadata = { title: "Companies" };

const PAGE = 50;
const SORTS = ["score", "delta", "attention_gap", "recent", "stars"] as const;
const STAGES = ["formation", "pre_seed", "seed", "series_a", "series_b", "unknown"];
const KINDS = ["company", "stealth", "project", "lab"];

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? undefined;

export default async function CompaniesPage({ searchParams }: PageProps<"/companies">) {
  const sp = await searchParams;
  const sort = SORTS.find((s) => s === one(sp.sort)) ?? "score";
  const stage = STAGES.find((s) => s === one(sp.stage));
  const kind = KINDS.find((s) => s === one(sp.kind));
  const q = one(sp.q)?.slice(0, 200);
  const theme = one(sp.theme)?.slice(0, 120);
  const demo = one(sp.demo);
  const query: QueryParams = { q, sort, stage, kind, theme, include_demo: demo === "0" ? false : undefined };

  const [companies, themes] = await Promise.all([getCompanies({ ...query, limit: PAGE, offset: 0 }), themeOptions()]);
  const filtered = Boolean(q || stage || kind || theme || demo === "0");

  const sortDef: FilterDef = {
    key: "sort",
    label: "Sort by",
    allValue: "score",
    options: [
      { value: "score", label: "Anti Score" },
      { value: "delta", label: "Δ 7 days" },
      { value: "attention_gap", label: "Attention gap" },
      { value: "recent", label: "Recently seen" },
      { value: "stars", label: "Stars · 30d" },
    ],
  };
  const filters: FilterDef[] = [
    { key: "theme", label: "Thesis", allValue: "", options: [{ value: "", label: "All themes" }, ...themes] },
    {
      key: "stage",
      label: "Stage",
      allValue: "",
      options: [
        { value: "", label: "Any stage" },
        { value: "formation", label: "Formation" },
        { value: "pre_seed", label: "Pre-seed" },
        { value: "seed", label: "Seed" },
        { value: "series_a", label: "Series A" },
        { value: "series_b", label: "Series B" },
        { value: "unknown", label: "Unknown" },
      ],
    },
    {
      key: "kind",
      label: "Kind",
      allValue: "",
      options: [
        { value: "", label: "Any kind" },
        { value: "stealth", label: "Stealth" },
        { value: "company", label: "Company" },
        { value: "project", label: "Project" },
        { value: "lab", label: "Lab" },
      ],
    },
  ];

  return (
    <div className="pb-10">
      <header className="grid gap-x-12 gap-y-6 border-b border-ink pb-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div>
          <p className="label mb-3">Index · Emerging companies & projects</p>
          <h1 className="font-serif text-[56px] leading-[0.98] tracking-[-0.02em] text-ink">Companies</h1>
          <p className="tnum mt-3 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            {companies.length}
            {companies.length === PAGE ? "+" : ""} {filtered ? "matching" : "on the radar"} · sorted by{" "}
            {sortDef.options.find((o) => o.value === sort)?.label}
          </p>
        </div>
        <div className="lg:pt-8">
          <ScoreAnatomy />
        </div>
      </header>

      <div className="mt-6 mb-8">
        <IndexControls
          placeholder="Name, description, org…"
          sort={sortDef}
          filters={filters}
          toggles={[
            {
              key: "demo",
              label: "Include demo data",
              onValue: "1",
              offValue: "0",
              defaultOn: true,
              hint: "Fictional demo entities are always labelled DEMO DATA",
            },
          ]}
          right={<span className="label text-ink-3">J / K to move · Enter to open</span>}
        />
      </div>

      {companies.length === 0 ? (
        <EmptyState title={filtered ? "No companies match these filters." : "No companies on the radar yet."}>
          {filtered
            ? "Try a broader thesis area or clear the search. Stage is inferred, so most early projects are “Unknown”."
            : "Collectors have not produced any companies yet. Check provider status."}
        </EmptyState>
      ) : (
        <CompanyResults key={JSON.stringify(query)} initial={companies} query={query} pageSize={PAGE} />
      )}
    </div>
  );
}
