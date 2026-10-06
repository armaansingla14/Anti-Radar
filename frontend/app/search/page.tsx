import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import clsx from "clsx";
import { Suspense } from "react";
import { Skeleton } from "@/components/Skeleton";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { EmptyState } from "@/components/EmptyState";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { SectionLabel } from "@/components/SectionLabel";
import { describeApiError } from "@/components/insight/apiError";
import { search } from "@/lib/api";
import { hrefFor, humanize, pad2 } from "@/lib/format";
import type { SearchFilters, SearchResponse, SearchResult } from "@/lib/types";

export async function generateMetadata({ searchParams }: PageProps<"/search">): Promise<Metadata> {
  const { q } = await searchParams;
  const query = typeof q === "string" ? q.trim() : "";
  return { title: query ? `“${query.slice(0, 60)}” · Search` : "Search" };
}

const EXAMPLES = [
  "Robotics founders who left major AI labs in the last six months",
  "New semiconductor startups with accelerating GitHub activity",
  "Companies working on inference outside NVIDIA CUDA",
  "Interesting founders in autonomous defense",
  "Research papers that could become companies",
  "Energy companies hiring machine learning engineers",
  "Companies with high technical momentum but low social attention",
];

const PARSER_COPY: Record<SearchResponse["parser"], { label: string; note: string }> = {
  deterministic: { label: "Deterministic parser", note: "Rules and the thesis taxonomy — no language model involved." },
  llm: { label: "LLM parser", note: "A language model translated the query into filters; check them below." },
  hybrid: { label: "Hybrid parser", note: "Rules first, then a language model filled the gaps." },
};

const KIND_LABEL: Record<string, string> = { company: "Company", person: "Person", repository: "Repository", paper: "Paper" };
const PAGE = 25;
const MAX = 100;

function one(v: string | string[] | undefined): string {
  return (Array.isArray(v) ? v[0] : v)?.trim() ?? "";
}

function SearchInput({ q }: { q: string }) {
  return (
    <Form action="/search" className="border-b border-ink" role="search">
      <label htmlFor="search-q" className="label mb-2 block">
        Ask the radar
      </label>
      <div className="flex items-end gap-4">
        <input
          key={q}
          id="search-q"
          name="q"
          type="search"
          defaultValue={q}
          autoFocus
          required
          maxLength={500}
          autoComplete="off"
          spellCheck={false}
          placeholder="e.g. tactile robotics founders who left a lab this year"
          className="min-w-0 flex-1 bg-transparent pb-3 font-serif text-[36px] leading-tight tracking-[-0.01em] text-ink placeholder:text-ink-3/70 focus:outline-none md:text-[40px] [&::-webkit-search-cancel-button]:hidden"
        />
        <button
          type="submit"
          className="mb-4 shrink-0 border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper"
        >
          Search ↵
        </button>
      </div>
    </Form>
  );
}

function Examples({ active }: { active?: string }) {
  return (
    <div className="mt-5">
      <p className="label mb-2.5 text-ink-3">Try</p>
      <ul className="flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <li key={ex}>
            <Link
              href={`/search?q=${encodeURIComponent(ex)}`}
              className={clsx(
                "inline-block border px-2.5 py-1 text-[12.5px] leading-snug transition-colors",
                ex === active ? "border-ink bg-ink text-paper" : "border-rule-strong text-ink-2 hover:border-ink hover:text-ink",
              )}
            >
              {ex}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function filterRows(f: SearchFilters): [string, string][] {
  const rows: [string, string][] = [];
  const list = (k: string, v: string[]) => v.length && rows.push([k, v.join(", ")]);
  list("entity_types", f.entity_types);
  list("themes", f.themes);
  list("topics", f.topics);
  list("keywords", f.keywords);
  list("prior_affiliations", f.prior_affiliations);
  list("hiring_role_classes", f.hiring_role_classes);
  list("signal_types", f.signal_types);
  list("stage", f.stage);
  if (f.min_score !== null) rows.push(["min_score", String(f.min_score)]);
  if (f.min_attention_gap !== null) rows.push(["min_attention_gap", String(f.min_attention_gap)]);
  if (f.max_attention !== null) rows.push(["max_attention", String(f.max_attention)]);
  if (f.accelerating) rows.push(["accelerating", "true"]);
  if (f.first_seen_within_days !== null) rows.push(["first_seen_within_days", String(f.first_seen_within_days)]);
  if (f.event_within_days !== null) rows.push(["event_within_days", String(f.event_within_days)]);
  rows.push(["sort", f.sort]);
  return rows;
}

function Interpretation({ res }: { res: SearchResponse }) {
  const parser = PARSER_COPY[res.parser] ?? { label: res.parser, note: "" };
  const rows = filterRows(res.filters);
  return (
    <section aria-labelledby="interp" className="mb-12 grid grid-cols-12 gap-x-10 gap-y-5 border-y border-rule py-5">
      <div className="col-span-12 lg:col-span-8">
        <h2 id="interp" className="label mb-3 text-ink">
          Read as
        </h2>
        {res.interpretation.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {res.interpretation.map((chip) => {
              const [k, ...rest] = chip.split(": ");
              return (
                <li
                  key={chip}
                  className="inline-flex items-baseline gap-1.5 border border-ink px-2 py-[3px] font-mono text-[11px] tracking-[0.02em] text-ink"
                >
                  {rest.length ? (
                    <>
                      <span className="uppercase text-ink-3">{k}</span>
                      <span>{rest.join(": ")}</span>
                    </>
                  ) : (
                    chip
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-[13px] text-ink-2">No structured filters were recognised — matched on keywords only.</p>
        )}
        <details className="group mt-4">
          <summary className="label cursor-pointer list-none text-ink-3 transition-colors hover:text-ink">
            <span className="inline-block transition-transform group-open:rotate-90">›</span> Structured filters sent to the
            ranker
          </summary>
          <dl className="mt-3 grid grid-cols-[180px_1fr] gap-x-4 border-t border-rule font-mono text-[11px]">
            {rows.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="border-b border-rule py-1.5 text-ink-3">{k}</dt>
                <dd className="border-b border-rule py-1.5 leading-relaxed text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        </details>
      </div>
      <aside className="col-span-12 lg:col-span-4 lg:border-l lg:border-rule lg:pl-8">
        <p className="label mb-2 text-ink">Parser</p>
        <p className="flex items-center gap-2 text-[15px] text-ink">
          <span
            className={clsx(
              "font-mono text-[11px] uppercase tracking-[0.08em]",
              res.parser === "deterministic" ? "text-ink" : "text-accent",
            )}
          >
            ● {parser.label}
          </span>
        </p>
        <p className="mt-1.5 text-[12.5px] leading-snug text-ink-2">{parser.note}</p>
        <p className="mt-3 flex items-center gap-2 text-[12px] text-ink-3">
          <EpistemicBadge value={res.parser === "deterministic" ? "INFERENCE" : "MODEL_OPINION"} /> match reasons are derived from
          stored signals
        </p>
      </aside>
    </section>
  );
}

function ResultRow({ r, n }: { r: SearchResult; n: number }) {
  const href = hrefFor(r.entity_type, r.slug || r.id);
  return (
    <li
      data-card
      data-href={href}
      tabIndex={0}
      className="grid grid-cols-[40px_1fr_auto] gap-x-5 border-b border-rule py-5 transition-colors hover:bg-panel/40"
    >
      <span className="tnum pt-1.5 font-mono text-[11px] text-ink-3">{pad2(n)}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="label">{KIND_LABEL[r.entity_type] ?? humanize(r.entity_type)}</span>
          {r.is_demo && <DemoBadge />}
        </div>
        <h3 className="mt-1 font-serif text-[23px] leading-tight tracking-[-0.005em] text-ink">
          <Link href={href} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
            {r.name}
          </Link>
        </h3>
        {r.subtitle && <p className="mt-0.5 max-w-[72ch] text-[13.5px] leading-snug text-ink-2">{r.subtitle}</p>}
        {r.match_reasons.length > 0 && (
          <ul className="mt-2.5 space-y-1">
            {r.match_reasons.map((m, i) => (
              <li key={i} className="grid grid-cols-[16px_1fr] text-[12.5px] leading-snug text-ink">
                <span aria-hidden className="text-ink-3">
                  →
                </span>
                <span className="min-w-0 break-words">{m}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="pt-1 text-right">
        <ScoreNumeral value={r.score} size="sm" label={r.entity_type === "person" ? "Founder" : "Anti"} className="items-end" />
      </div>
    </li>
  );
}

async function Results({ q, limit }: { q: string; limit: number }) {
  let res: SearchResponse;
  try {
    res = await search(q, { limit });
  } catch (err) {
    return (
      <div className="border-y border-accent py-8" role="alert">
        <p className="label mb-2 text-accent">Search failed</p>
        <p className="text-[14px] text-ink">{describeApiError(err)}</p>
      </div>
    );
  }
  const shown = res.results.length;
  return (
    <>
      <Interpretation res={res} />
      <SectionLabel
        index={1}
        right={
          res.total > shown ? (
            <>
              Showing {shown} of {res.total.toLocaleString("en-US")}
            </>
          ) : (
            <>
              {res.total} {res.total === 1 ? "match" : "matches"}
            </>
          )
        }
      >
        Results · sorted by {humanize(res.filters.sort).toLowerCase()}
      </SectionLabel>
      {shown === 0 ? (
        <EmptyState
          className="mt-6"
          title="Nothing on the radar matches every filter."
          action={
            <Link href="/thesis" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
              Browse the thesis map →
            </Link>
          }
        >
          All {res.interpretation.length || "keyword"} conditions above must hold at once. Try dropping a qualifier — for example
          a time window or a theme — or rephrase. This is an honest zero, not an index failure.
        </EmptyState>
      ) : (
        <ol className="border-t border-ink">
          {res.results.map((r, i) => (
            <ResultRow key={`${r.entity_type}-${r.id}`} r={r} n={i + 1} />
          ))}
        </ol>
      )}
      {res.total > shown && limit < MAX && (
        <div className="mt-6 text-center">
          <Link
            href={`/search?q=${encodeURIComponent(q)}&limit=${MAX}`}
            scroll={false}
            className="inline-block border border-ink px-4 py-2 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper"
          >
            Show up to {MAX} results
          </Link>
        </div>
      )}
      {res.total > shown && limit >= MAX && (
        <p className="mt-6 text-center text-[12.5px] text-ink-3">
          Top {MAX} of {res.total.toLocaleString("en-US")} shown. Narrow the query to see the rest.
        </p>
      )}
    </>
  );
}

function ResultsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Searching">
      <div className="mb-12 space-y-3 border-y border-rule py-5">
        <Skeleton className="h-3 w-20" />
        <div className="flex gap-2">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-6 w-48" />
        </div>
      </div>
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="grid grid-cols-[40px_1fr_60px] gap-5 border-b border-rule py-5">
          <Skeleton className="h-3 w-5" />
          <div className="space-y-2">
            <Skeleton className="h-6 w-1/3" />
            <Skeleton lines={2} />
          </div>
          <Skeleton className="h-8 w-10" />
        </div>
      ))}
    </div>
  );
}

function HowItWorks() {
  const rows: [string, string][] = [
    [
      "Plain language in",
      "Type the question as you would ask a colleague. Lab names, themes, time windows, hiring and attention phrases are recognised.",
    ],
    ["Filters out", "The query becomes explicit, inspectable filters — shown as chips — before any ranking happens."],
    ["Reasons attached", "Every result lists why it matched: thesis fit, affiliations, signals with dates."],
  ];
  return (
    <section className="mt-16" aria-labelledby="how">
      <SectionLabel id="how" index={1}>
        How search works
      </SectionLabel>
      <ol className="mt-6 grid gap-8 md:grid-cols-3">
        {rows.map(([k, v], i) => (
          <li key={k} className="border-t border-ink pt-3">
            <p className="tnum font-mono text-[11px] text-ink-3">{pad2(i + 1)}</p>
            <p className="mt-1 font-serif text-[20px] leading-tight text-ink">{k}</p>
            <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{v}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default async function SearchPage({ searchParams }: PageProps<"/search">) {
  const sp = await searchParams;
  const q = one(sp.q).slice(0, 500);
  const limitRaw = Number(one(sp.limit));
  const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.min(MAX, Math.floor(limitRaw)) : PAGE;

  return (
    <div>
      <p className="label mb-6">Search · natural language over companies, founders and papers</p>
      <SearchInput q={q} />
      <Examples active={q} />
      <div className="mt-12">
        {q ? (
          <Suspense key={`${q}::${limit}`} fallback={<ResultsSkeleton />}>
            <Results q={q} limit={limit} />
          </Suspense>
        ) : (
          <HowItWorks />
        )}
      </div>
    </div>
  );
}
