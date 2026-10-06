import clsx from "clsx";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { SourceLink } from "@/components/SourceLink";
import { domainOf, fmtDate, isSafeUrl, pad2 } from "@/lib/format";
import type { Epistemic, PaperCard as Paper } from "@/lib/types";
import { EntityLinks } from "./EntityLinks";

export interface PaperCardProps {
  paper: Paper;
  index?: number;
  /** Epistemic status of why_it_matters (template text = INFERENCE, LLM text = MODEL_OPINION). */
  whyEpistemic: Epistemic;
  className?: string;
}

function authorLine(authors: string[]): string {
  if (authors.length === 0) return "Authors not recorded";
  if (authors.length <= 3) return authors.join(", ");
  return `${authors.slice(0, 3).join(", ")} + ${authors.length - 3} more`;
}

/** Research paper as a publication entry: id, title, authors, categories, code, why it matters. */
export function PaperCard({ paper, index, whyEpistemic, className }: PaperCardProps) {
  const code = paper.code_urls.filter(isSafeUrl);
  const pathOf = (u: string) => u.replace(/^https?:\/\/[^/]+\//, "").replace(/\/$/, "").toLowerCase();
  const uniqueCode = code.filter((u, i) => code.findIndex((v) => pathOf(v) === pathOf(u)) === i);
  return (
    <article className={clsx("flex h-full flex-col border-t border-ink pt-3", className)}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {index !== undefined && <span className="tnum font-mono text-[12px] text-ink">{pad2(index)}</span>}
        <span className="label">arXiv {paper.arxiv_id}</span>
        {paper.published_at && <span className="label text-ink-3">· {fmtDate(paper.published_at)}</span>}
        {paper.is_demo && <DemoBadge className="ml-auto" />}
      </div>
      <h3 className="mt-2 font-serif text-[19px] leading-[1.25] text-ink">
        {isSafeUrl(paper.url) ? (
          <a href={paper.url} target="_blank" rel="noopener noreferrer nofollow" className="decoration-accent decoration-1 underline-offset-4 hover:underline">
            {paper.title}
          </a>
        ) : (
          paper.title
        )}
      </h3>
      <p className="mt-1.5 text-[12.5px] leading-snug text-ink-2">{authorLine(paper.authors)}</p>
      {paper.categories.length > 0 && (
        <p className="mt-2 flex flex-wrap gap-x-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
          {paper.categories.map((c) => (
            <span key={c} className="border-b border-rule">
              {c}
            </span>
          ))}
        </p>
      )}
      {paper.why_it_matters && (
        <div className="mt-4">
          <p className="label mb-1.5 flex items-center gap-2 text-ink">
            Why it matters <EpistemicBadge value={whyEpistemic} />
          </p>
          <p className="text-[13px] leading-relaxed text-ink">{paper.why_it_matters}</p>
        </div>
      )}
      <div className="mt-auto space-y-2 pt-4">
        {uniqueCode.length > 0 ? (
          <p className="flex flex-wrap items-baseline gap-x-3">
            <span className="label">Code</span>
            {uniqueCode.slice(0, 2).map((u) => (
              <SourceLink key={u} url={u}>
                {domainOf(u)?.startsWith("github") ? pathOf(u) : domainOf(u)}
              </SourceLink>
            ))}
          </p>
        ) : (
          <p className="label text-ink-3">No code released</p>
        )}
        {paper.linked_entities.length > 0 && <EntityLinks entities={paper.linked_entities} limit={4} />}
        {paper.score !== null && (
          <p className="label text-ink-3" title="Paper relevance rank score (0–100)">
            Relevance {Math.round(paper.score)}
          </p>
        )}
      </div>
    </article>
  );
}
