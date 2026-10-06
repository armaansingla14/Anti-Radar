import clsx from "clsx";
import Link from "next/link";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { SourceLink } from "@/components/SourceLink";
import { fmtCompact, fmtDate, fmtNum, hrefFor, humanize, isSafeUrl } from "@/lib/format";
import type { EntityLink, JsonObject, Narrative, PaperCard, ReportRef, RepoCard } from "@/lib/types";
import { GeneratorLabel, Quiet } from "./primitives";

const th = "label whitespace-nowrap px-2 py-2 text-left font-normal";
const td = "px-2 py-2.5 align-top";

/** Dense repository table: stars, Δ30d, commits 4w, contributors, created. */
export function RepoTable({ repos }: { repos: RepoCard[] }) {
  if (repos.length === 0) return <Quiet>No public repositories linked yet.</Quiet>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse border-t border-ink text-[13px]">
        <thead>
          <tr className="border-b border-ink">
            <th className={th} scope="col">Repository</th>
            <th className={th} scope="col">Lang</th>
            <th className={clsx(th, "text-right")} scope="col">Stars</th>
            <th className={clsx(th, "text-right")} scope="col">Δ 30d</th>
            <th className={clsx(th, "text-right")} scope="col">Commits 4w</th>
            <th className={clsx(th, "text-right")} scope="col">Contrib.</th>
            <th className={clsx(th, "text-right")} scope="col">Forks</th>
            <th className={clsx(th, "text-right")} scope="col">Created</th>
          </tr>
        </thead>
        <tbody>
          {repos.map((r) => (
            <tr key={r.id} className="border-b border-rule">
              <td className={clsx(td, "min-w-[260px]")}>
                <div className="flex flex-wrap items-center gap-2">
                  {isSafeUrl(r.url) ? (
                    <a href={r.url} target="_blank" rel="noopener noreferrer nofollow" className="link font-mono text-[12.5px] text-ink">
                      {r.full_name}
                    </a>
                  ) : (
                    <span className="font-mono text-[12.5px] text-ink">{r.full_name}</span>
                  )}
                  {r.is_demo && <DemoBadge />}
                </div>
                {r.description && <p className="mt-0.5 line-clamp-2 max-w-[62ch] text-[12.5px] leading-snug text-ink-2">{r.description}</p>}
              </td>
              <td className={clsx(td, "whitespace-nowrap font-mono text-[10.5px] uppercase text-ink-2")}>{r.language ?? "—"}</td>
              <td className={clsx(td, "tnum text-right font-serif text-[18px] leading-none text-ink")}>{fmtCompact(r.stars)}</td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px]", r.stars_30d && r.stars_30d > 0 ? "text-ink" : "text-ink-3")}>
                {r.stars_30d === null ? "—" : `+${fmtCompact(r.stars_30d)}`}
              </td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px] text-ink-2")}>{r.commits_4w === null ? "—" : fmtNum(r.commits_4w, 0)}</td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px] text-ink-2")}>{r.contributors ?? "—"}</td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px] text-ink-2")}>{fmtCompact(r.forks)}</td>
              <td className={clsx(td, "tnum whitespace-nowrap text-right font-mono text-[10.5px] uppercase text-ink-3")}>{fmtDate(r.gh_created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function linkFor(e: EntityLink): string | null {
  if (!e.slug) return null;
  if (e.type === "company" || e.type === "person") return hrefFor(e.type, e.slug);
  return null;
}

/** Linked arXiv papers with authors, categories, code links and linked entities. */
export function PaperList({ papers, focusName }: { papers: PaperCard[]; focusName?: string }) {
  if (papers.length === 0) return <Quiet>No linked papers. Authorship is matched against arXiv by name; none found for this entity.</Quiet>;
  return (
    <ol className="divide-y divide-rule border-y border-ink">
      {papers.map((p) => (
        <li key={p.id} className="py-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="tnum font-mono text-[10.5px] uppercase text-ink-3">{fmtDate(p.published_at)}</span>
            <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">{p.categories.slice(0, 3).join(" · ")}</span>
            {p.is_demo && <DemoBadge />}
          </div>
          <h4 className="mt-1 font-serif text-[18px] leading-snug text-ink">
            {isSafeUrl(p.url) ? (
              <a href={p.url} target="_blank" rel="noopener noreferrer nofollow" className="decoration-accent decoration-1 underline-offset-4 hover:underline">
                {p.title}
              </a>
            ) : (
              p.title
            )}
          </h4>
          <p className="mt-1 text-[12.5px] text-ink-2">
            {p.authors.slice(0, 8).map((a, i) => (
              <span key={`${a}-${i}`} className={a === focusName ? "text-ink underline decoration-rule-strong underline-offset-2" : undefined}>
                {i > 0 && ", "}
                {a}
              </span>
            ))}
            {p.authors.length > 8 && <span className="text-ink-3"> + {p.authors.length - 8} more</span>}
          </p>
          {p.abstract && <p className="mt-2 line-clamp-3 max-w-[72ch] text-[13px] leading-relaxed text-ink-2">{p.abstract}</p>}
          {p.why_it_matters && <p className="mt-2 max-w-[72ch] border-l-2 border-accent pl-3 text-[13px] leading-snug text-ink">{p.why_it_matters}</p>}
          <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="font-mono text-[10.5px] text-ink-3">arXiv {p.arxiv_id}</span>
            {p.code_urls.filter(isSafeUrl).slice(0, 2).map((u) => (
              <SourceLink key={u} url={u}>
                code
              </SourceLink>
            ))}
            {p.linked_entities
              .filter((e) => e.type === "company" || e.type === "person")
              .slice(0, 6)
              .map((e) => {
                const href = linkFor(e);
                return href ? (
                  <Link key={`${e.type}-${e.slug}`} href={href} className="link font-mono text-[10.5px] uppercase tracking-[0.04em] text-ink-2">
                    {e.name}
                  </Link>
                ) : null;
              })}
          </div>
        </li>
      ))}
    </ol>
  );
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** Hiring summary (roles by class, new in 30d) plus the job list. */
export function HiringPanel({ hiring, jobs }: { hiring: JsonObject; jobs: JsonObject[] }) {
  const open = num(hiring.open_roles) ?? jobs.length;
  const technical = num(hiring.technical_roles);
  const new30 = num(hiring.new_30d);
  const prev30 = num(hiring.new_prev_30d);
  const byClass = Object.entries((hiring.by_class ?? {}) as Record<string, number>).sort((a, b) => b[1] - a[1]);
  const maxClass = Math.max(1, ...byClass.map(([, v]) => v));
  if (open === 0 && jobs.length === 0) {
    return <Quiet>No open roles observed. Job boards are checked when a careers page or ATS is known for this entity.</Quiet>;
  }
  return (
    <div>
      <div className="grid grid-cols-3 gap-4 border-t border-ink pt-3">
        <div>
          <p className="label">Open roles</p>
          <p className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{open}</p>
        </div>
        <div>
          <p className="label">Technical</p>
          <p className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{technical ?? "—"}</p>
        </div>
        <div>
          <p className="label">New · 30d</p>
          <p className={clsx("tnum mt-1 font-serif text-[34px] leading-none", new30 && new30 > (prev30 ?? 0) ? "text-accent" : "text-ink")}>
            {new30 ?? "—"}
          </p>
          {prev30 !== null && <p className="tnum mt-1 font-mono text-[10px] text-ink-3">prior 30d: {prev30}</p>}
        </div>
      </div>
      {byClass.length > 0 && (
        <ul className="mt-5 space-y-1.5" aria-label="Open roles by class">
          {byClass.map(([k, v]) => (
            <li key={k} className="grid grid-cols-[150px_1fr_24px] items-center gap-3">
              <span className="truncate font-mono text-[10.5px] uppercase tracking-[0.04em] text-ink-2">{humanize(k)}</span>
              <span className="h-[6px] bg-rule">
                <span className="block h-full bg-ink" style={{ width: `${(v / maxClass) * 100}%` }} />
              </span>
              <span className="tnum text-right font-mono text-[11px] text-ink">{v}</span>
            </li>
          ))}
        </ul>
      )}
      {jobs.length > 0 && (
        <ul className="mt-5 divide-y divide-rule border-y border-rule">
          {jobs.map((j, i) => {
            const url = str(j.url);
            const title = str(j.title) ?? "Untitled role";
            return (
              <li key={str(j.id) ?? i} className="grid grid-cols-[1fr_auto] items-baseline gap-3 py-2">
                <span className="min-w-0">
                  {isSafeUrl(url) ? (
                    <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="link text-[13.5px] text-ink">
                      {title}
                    </a>
                  ) : (
                    <span className="text-[13.5px] text-ink">{title}</span>
                  )}
                  <span className="mt-0.5 block font-mono text-[10px] uppercase tracking-[0.04em] text-ink-3">
                    {[str(j.role_class) && humanize(str(j.role_class)!), str(j.location), str(j.department)].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <span className="tnum whitespace-nowrap font-mono text-[10.5px] uppercase text-ink-3">
                  {fmtDate(str(j.posted_at) ?? str(j.first_seen_at), { year: false })}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Skeptical list: risks and open questions, each with its provenance. */
export function RiskList({ risks }: { risks: Narrative[] }) {
  if (risks.length === 0) return <Quiet>No risks recorded — that is a gap in our analysis, not a clean bill of health.</Quiet>;
  return (
    <ol className="divide-y divide-rule border-y border-ink">
      {risks.map((r, i) => (
        <li key={i} className="grid grid-cols-[28px_1fr] gap-2 py-3">
          <span className="tnum pt-[2px] font-mono text-[11px] text-accent">Q{i + 1}</span>
          <div className="min-w-0">
            <p className="text-[14px] leading-snug text-ink">{r.text}</p>
            <div className="mt-1.5 flex flex-wrap gap-2">
              <EpistemicBadge value={r.epistemic} />
              <GeneratorLabel generator={r.generator} model={r.model} />
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Existing briefs and memos for this entity. */
export function ReportsList({ reports }: { reports: (ReportRef & { generator?: string; status?: string })[] }) {
  if (reports.length === 0) {
    return <Quiet>No briefs or memos yet. Run Investigate for an Anti Brief, or Generate Anti Memo.</Quiet>;
  }
  return (
    <ul className="divide-y divide-rule border-y border-ink">
      {reports.map((r) => (
        <li key={r.id}>
          <Link href={`/reports/${r.id}`} className="group grid grid-cols-[64px_1fr_auto] items-baseline gap-3 py-2.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-accent">{r.kind === "memo" ? "Memo" : r.kind === "brief" ? "Brief" : humanize(r.kind)}</span>
            <span className="min-w-0 truncate text-[13.5px] text-ink group-hover:underline group-hover:decoration-accent group-hover:underline-offset-4">
              {r.title}
            </span>
            <span className="tnum whitespace-nowrap font-mono text-[10.5px] uppercase text-ink-3">
              {r.generator && <span className="mr-2">{r.generator}</span>}
              {fmtDate(r.created_at)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** Possible duplicate entities — never merged automatically. */
export function MatchCandidates({ candidates, basePath }: { candidates: JsonObject[]; basePath: "/companies" | "/founders" }) {
  if (candidates.length === 0) {
    return <Quiet>No uncertain matches pending. Entity resolution found no other record that might be the same {basePath === "/companies" ? "company" : "person"}.</Quiet>;
  }
  return (
    <ul className="divide-y divide-rule border-y border-ink">
      {candidates.map((m, i) => {
        const name = str(m.other_name) ?? "Unnamed record";
        const slug = str(m.other_slug);
        const conf = num(m.confidence);
        const evidence = m.evidence && typeof m.evidence === "object" ? Object.entries(m.evidence as JsonObject).slice(0, 4) : [];
        return (
          <li key={str(m.id) ?? i} className="grid grid-cols-[1fr_auto] gap-3 py-3">
            <div className="min-w-0">
              <p className="label mb-1 text-accent">Uncertain match — not merged</p>
              {slug ? (
                <Link href={`${basePath}/${slug}`} className="link font-serif text-[17px] text-ink">
                  {name}
                </Link>
              ) : (
                <span className="font-serif text-[17px] text-ink">{name}</span>
              )}
              <p className="mt-1 font-mono text-[10.5px] text-ink-3">
                {str(m.method) && <>method {str(m.method)}</>}
                {evidence.map(([k, v]) => (
                  <span key={k} className="ml-3">
                    {humanize(k)}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </span>
                ))}
              </p>
            </div>
            <div className="text-right">
              <p className="label">Confidence</p>
              <p className="tnum font-serif text-[26px] leading-none text-ink">{conf === null ? "—" : `${Math.round(conf * 100)}%`}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
