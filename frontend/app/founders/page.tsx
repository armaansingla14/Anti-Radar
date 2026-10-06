import type { Metadata } from "next";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { PreCompanyCard } from "@/components/entity/FounderList";
import { FounderResults } from "@/components/entity/FounderResults";
import { IndexControls, type FilterDef } from "@/components/entity/IndexControls";
import { themeOptions } from "@/components/entity/themes";
import { getFounders, type QueryParams } from "@/lib/api";

export const metadata: Metadata = { title: "Founders to Know" };

const PAGE = 40;
const SORTS = ["score", "delta", "recent", "followers"] as const;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? undefined;

export default async function FoundersPage({ searchParams }: PageProps<"/founders">) {
  const sp = await searchParams;
  const sort = SORTS.find((s) => s === one(sp.sort)) ?? "score";
  const q = one(sp.q)?.slice(0, 200);
  const theme = one(sp.theme)?.slice(0, 120);
  const pre = one(sp.pre) === "1";
  const demo = one(sp.demo);
  const query: QueryParams = { q, theme, sort, no_company: pre || undefined, include_demo: demo === "0" ? false : undefined };
  const filtered = Boolean(q || theme || pre || demo === "0");
  const showSpotlight = !pre;

  const [people, spotlight, themes] = await Promise.all([
    getFounders({ ...query, limit: PAGE, offset: 0 }),
    showSpotlight ? getFounders({ ...query, no_company: true, limit: 6, offset: 0 }) : Promise.resolve([]),
    themeOptions(),
  ]);

  const sortDef: FilterDef = {
    key: "sort",
    label: "Sort by",
    allValue: "score",
    options: [
      { value: "score", label: "Founder Score" },
      { value: "delta", label: "Δ 7 days" },
      { value: "recent", label: "Recently seen" },
      { value: "followers", label: "Followers" },
    ],
  };
  const filters: FilterDef[] = [{ key: "theme", label: "Domain / thesis", allValue: "", options: [{ value: "", label: "All domains" }, ...themes] }];

  return (
    <div className="pb-10">
      <header className="grid gap-x-12 gap-y-6 border-b border-ink pb-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div>
          <p className="label mb-3">Index · Technical founders surfacing now</p>
          <h1 className="font-serif text-[56px] leading-[0.98] tracking-[-0.02em] text-ink">Founders to Know</h1>
          <p className="tnum mt-3 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            {people.length}
            {people.length === PAGE ? "+" : ""} {pre ? "with no company yet" : filtered ? "matching" : "people tracked"} · sorted by{" "}
            {sortDef.options.find((o) => o.value === sort)?.label}
          </p>
        </div>
        <div className="max-w-[64ch] lg:pt-8">
          <p className="text-[14px] leading-relaxed text-ink-2">
            <span className="text-ink">Founder Score</span> (0–100) weighs pedigree, research output, formation signals (leaving a lab,
            new org, “starting something” posts), thesis fit, technical output and attention gap. People marked{" "}
            <span className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-accent">◆ No company yet</span> are the
            formation-stage targets: technical, moving, and not yet consensus.
          </p>
        </div>
      </header>

      <div className="mb-10 mt-6">
        <IndexControls
          placeholder="Name, bio, handle, company…"
          sort={sortDef}
          filters={filters}
          toggles={[
            { key: "pre", label: "Pre-company only", onValue: "1", defaultOn: false, accent: true, hint: "Only people with no current company on record" },
            { key: "demo", label: "Include demo data", onValue: "1", offValue: "0", defaultOn: true, hint: "Fictional people are labelled DEMO DATA" },
          ]}
          right={<span className="label text-ink-3">J / K to move · Enter to open</span>}
        />
      </div>

      {showSpotlight && spotlight.length > 0 && (
        <section aria-labelledby="pre-company" className="mb-14">
          <SectionLabel index={1} id="pre-company" right={<span>formation-stage targets</span>}>
            Pre-company · no company yet
          </SectionLabel>
          <div className="mt-5 grid gap-x-8 gap-y-4 md:grid-cols-2 xl:grid-cols-3">
            {spotlight.map((p, i) => (
              <PreCompanyCard key={p.id} person={p} rank={i + 1} />
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="all-founders">
        <SectionLabel index={showSpotlight && spotlight.length > 0 ? 2 : 1} id="all-founders" right={<span className="tnum">ranked</span>}>
          {pre ? "Pre-company founders" : "All founders"}
        </SectionLabel>
        <div className="mt-5">
          {people.length === 0 ? (
            <EmptyState title={filtered ? "Nobody matches these filters." : "No founders surfaced yet."}>
              {filtered ? "Try a broader domain or clear the search." : "People appear once papers, repositories or profiles are collected."}
            </EmptyState>
          ) : (
            <FounderResults key={JSON.stringify(query)} initial={people} query={query} pageSize={PAGE} />
          )}
        </div>
      </section>
    </div>
  );
}
