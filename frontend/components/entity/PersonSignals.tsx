import Link from "next/link";
import { VerificationBadge } from "@/components/Badges";
import { SourceLink } from "@/components/SourceLink";
import { formationHref } from "@/components/formation/FormationCardView";
import { FormationLabel } from "@/components/formation/Labels";
import { WhyNow } from "@/components/formation/WhyNow";
import { fmtCompact, fmtDate } from "@/lib/format";
import type { FormationCard, PaperCard, RepoCard } from "@/lib/types";
import { type FormationEvidence, type LabContribution, labContributionText } from "./entityLogic";
import { Quiet } from "./primitives";

/** FORMATION SIGNALS — formation candidates this person belongs to + new_project / joined_young_org evidence. */
export function FormationSignals({
  formations,
  evidence,
  demo = false,
}: {
  formations: FormationCard[];
  evidence: FormationEvidence;
  demo?: boolean;
}) {
  const has = formations.length > 0 || evidence.newProjects.length > 0 || evidence.youngOrgs.length > 0 || evidence.evidence.length > 0;
  if (!has) {
    return (
      <Quiet>
        No formation signals — no new own repository, no young organization joined, and not part of a formation candidate. Absence of a
        signal is not evidence of no activity.
      </Quiet>
    );
  }
  return (
    <div className="space-y-5">
      {formations.length > 0 && (
        <ul className="divide-y divide-rule border-y border-ink">
          {formations.map((f) => (
            <li key={f.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-3">
              <div className="min-w-0">
                <FormationLabel label={f.label} tier={f.tier} />
                <Link href={formationHref(f)} className="link mt-1 block font-serif text-[19px] leading-tight text-ink">
                  {f.title}
                </Link>
                <p className="mt-1 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
                  {f.members.length} {f.members.length === 1 ? "person" : "people"} · {f.independent_signals} independent{" "}
                  {f.independent_signals === 1 ? "signal" : "signals"}
                  {f.first_detected_at && <> · first detected {fmtDate(f.first_detected_at, { year: false })}</>}
                </p>
              </div>
              <Link href={formationHref(f)} className="label whitespace-nowrap text-ink hover:text-accent">
                Formation report →
              </Link>
            </li>
          ))}
        </ul>
      )}
      {(evidence.newProjects.length > 0 || evidence.youngOrgs.length > 0) && (
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {evidence.newProjects.length > 0 && (
            <div>
              <dt className="label text-ink-3">New own projects</dt>
              <dd className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {evidence.newProjects.map((r) => (
                  <SourceLink key={r} url={/^[\w.-]+\/[\w.-]+$/.test(r) ? `https://github.com/${r}` : null} demo={demo} className="text-[11.5px] text-ink">
                    {r}
                  </SourceLink>
                ))}
              </dd>
            </div>
          )}
          {evidence.youngOrgs.length > 0 && (
            <div>
              <dt className="label text-ink-3">{evidence.joinedYoungOrg ? "Joined young organization" : "Young organizations"}</dt>
              <dd className="mt-1 font-mono text-[11.5px] text-ink">{evidence.youngOrgs.join(" · ")}</dd>
            </div>
          )}
        </dl>
      )}
      {evidence.evidence.length > 0 && <WhyNow items={evidence.evidence} limit={5} demo={demo} />}
      <p className="text-[11.5px] leading-snug text-ink-3">
        A new repository or a young organization is a possible formation signal — never proof that a company was founded.
      </p>
    </div>
  );
}

/** TECHNICAL TRAJECTORY — verified lab contributions, strongest repositories, papers. */
export function TechnicalTrajectory({
  labs,
  repos,
  papers,
  demo = false,
}: {
  labs: LabContribution[];
  repos: RepoCard[];
  papers: PaperCard[];
  demo?: boolean;
}) {
  const topRepos = [...repos].sort((a, b) => b.stars - a.stars).slice(0, 4);
  const recentPapers = [...papers].sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? "")).slice(0, 3);
  if (labs.length === 0 && topRepos.length === 0 && recentPapers.length === 0) {
    return <Quiet>No verified lab contributions, repositories or papers linked yet.</Quiet>;
  }
  return (
    <div className="grid gap-x-10 gap-y-6 lg:grid-cols-3">
      <div className="min-w-0">
        <p className="label mb-2 text-ink-3">Frontier-lab contributions</p>
        {labs.length ? (
          <ul className="divide-y divide-rule border-y border-ink">
            {labs.map((l) => (
              <li key={l.fullName} className="py-2.5">
                <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <SourceLink url={demo ? null : l.url} className="text-[12.5px] text-ink">
                    {labContributionText(l)}
                  </SourceLink>
                  <VerificationBadge value="VERIFIED" />
                </p>
                <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
                  {l.label}
                  {l.tier !== null && ` · tier ${l.tier}`}
                  {l.firstCommitAt && ` · ${fmtDate(l.firstCommitAt, { year: true })}`}
                  {l.lastCommitAt && ` – ${fmtDate(l.lastCommitAt, { year: true })}`}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border-y border-rule py-3 text-[12.5px] text-ink-3">No verified frontier-lab contributions on record.</p>
        )}
      </div>
      <div className="min-w-0">
        <p className="label mb-2 text-ink-3">Repositories</p>
        {topRepos.length ? (
          <ul className="divide-y divide-rule border-y border-ink">
            {topRepos.map((r) => (
              <li key={r.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-2 py-2.5">
                <span className="min-w-0">
                  <SourceLink url={r.url} demo={r.is_demo} className="truncate text-[12px] text-ink">
                    {r.full_name}
                  </SourceLink>
                  {r.gh_created_at && <span className="block font-mono text-[9.5px] uppercase text-ink-3">created {fmtDate(r.gh_created_at)}</span>}
                </span>
                <span className="tnum text-right font-mono text-[11px] text-ink">
                  {fmtCompact(r.stars)}★{r.stars_30d ? <span className="block text-[9.5px] text-ink-2">+{fmtCompact(r.stars_30d)} 30d</span> : null}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border-y border-rule py-3 text-[12.5px] text-ink-3">No public repositories linked.</p>
        )}
      </div>
      <div className="min-w-0">
        <p className="label mb-2 text-ink-3">Papers</p>
        {recentPapers.length ? (
          <ul className="divide-y divide-rule border-y border-ink">
            {recentPapers.map((p) => (
              <li key={p.id} className="py-2.5">
                <SourceLink url={p.url} demo={p.is_demo} className="font-sans text-[12.5px] leading-snug text-ink">
                  {p.title}
                </SourceLink>
                <span className="block font-mono text-[9.5px] uppercase text-ink-3">
                  {fmtDate(p.published_at)} · arXiv {p.arxiv_id}
                  {p.code_urls.length > 0 && " · code"}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border-y border-rule py-3 text-[12.5px] text-ink-3">No linked papers.</p>
        )}
      </div>
    </div>
  );
}

/** Public X handle (link only when known) + the honest availability line. */
export function XPresence({ handle, demo = false, xLine }: { handle: string | null; demo?: boolean; xLine: string }) {
  const href = handle && !demo ? `https://x.com/${encodeURIComponent(handle)}` : null;
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-[10.5px] uppercase tracking-[0.06em]">
      {handle ? (
        href ? (
          <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="link text-ink-2">
            x/@{handle} ↗
          </a>
        ) : (
          <span className="text-ink-3">x/@{handle} (fictional)</span>
        )
      ) : (
        <span className="text-ink-3">No public X handle on record</span>
      )}
      <span className="text-ink-3">· {xLine}</span>
    </p>
  );
}
