/** Report reader bodies for PORTFOLIO ANALYST reports: `portfolio_review` (per-company memo) and `portfolio_brief`. */
import Link from "next/link";
import type { JsonObject } from "@/lib/types";
import type { PortfolioBriefBody, PortfolioReviewBody, ReportClaim } from "@/lib/types-portfolio";
import { ActionChip, InferenceTag } from "./Badges";
import { BriefHeading, ClaimList, Cites, PortfolioBriefSections } from "./BriefView";
import { portfolioHref, stripCites } from "./logic";

/** Shape guards: a malformed body falls back to the markdown reader. */
export function isPortfolioReview(kind: string, body: JsonObject | null | undefined): boolean {
  return kind === "portfolio_review" && !!body && typeof body.company === "object" && Array.isArray(body.what_changed);
}

export function isPortfolioBrief(kind: string, body: JsonObject | null | undefined): boolean {
  return kind === "portfolio_brief" && !!body && Array.isArray(body.five_things) && Array.isArray(body.needs_attention);
}

const TYPE_ORDER = ["FINANCING", "PRODUCT", "TECHNICAL", "TALENT", "COMMERCIAL", "REGULATORY", "RISK", "STRATEGIC", "OTHER"];

/** The S-id prefix matches components/entity/Sources (id="source-S1"). */
const PREFIX = "source";

export function PortfolioReviewView({ body }: { body: PortfolioReviewBody }) {
  const groups = TYPE_ORDER.filter((t) => (body.material_events?.[t] ?? []).length > 0);
  const extra = Object.keys(body.material_events ?? {}).filter((t) => !TYPE_ORDER.includes(t) && body.material_events[t].length > 0);
  const nf = body.noise_filtered ?? {};
  const s = body.suggested_review;
  return (
    <div className="space-y-12">
      <section id="r-sec-summary" className="scroll-mt-24">
        <BriefHeading n={1}>Summary</BriefHeading>
        <p className="font-serif text-[19px] leading-snug text-ink">
          {stripCites(body.summary.text)}
          <Cites ids={body.summary.source_ids} prefix={PREFIX} />
          {body.summary.epistemic === "INFERENCE" && <InferenceTag className="ml-2 align-[3px]" />}
        </p>
        <p className="mt-2 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">
          Window {body.period.start} → {body.period.end} ({body.period.days} days) ·{" "}
          <Link href={portfolioHref(body.company.slug)} className="text-ink hover:text-accent">
            Open portfolio view →
          </Link>
        </p>
      </section>

      <section id="r-sec-suggested_review" className="scroll-mt-24">
        <BriefHeading n={2}>Suggested review</BriefHeading>
        <ActionChip value={s.action} />
        <p className="mt-2 text-[13.5px] leading-snug text-ink">
          {stripCites(s.reason.text)}
          <Cites ids={s.reason.source_ids} prefix={PREFIX} />
          {s.reason.epistemic === "INFERENCE" && <InferenceTag className="ml-2 align-[1px]" />}
        </p>
      </section>

      <section id="r-sec-what_changed" className="scroll-mt-24">
        <BriefHeading n={3} right={body.what_changed.length}>
          What changed
        </BriefHeading>
        <ClaimList claims={body.what_changed} numbered empty="Nothing material or worth watching in the window." prefix={PREFIX} />
      </section>

      <section id="r-sec-material_events" className="scroll-mt-24">
        <BriefHeading n={4}>Events by type</BriefHeading>
        {groups.length + extra.length === 0 ? (
          <p className="text-[13px] text-ink-3">No typed event in the window.</p>
        ) : (
          <div className="space-y-6">
            {[...groups, ...extra].map((t) => (
              <div key={t}>
                <p className="label mb-1 text-ink">{t}</p>
                <ClaimList claims={body.material_events[t] as ReportClaim[]} empty="" prefix={PREFIX} />
              </div>
            ))}
          </div>
        )}
      </section>

      <section id="r-sec-open_questions" className="scroll-mt-24">
        <BriefHeading n={5} right={body.open_questions.length}>
          Open questions
        </BriefHeading>
        {body.open_questions.length === 0 ? (
          <p className="text-[13px] text-ink-3">No question raised.</p>
        ) : (
          <ol className="divide-y divide-rule">
            {body.open_questions.map((q, i) => (
              <li key={q.question_id ?? i} className="grid grid-cols-[26px_minmax(0,1fr)] gap-x-3 py-2.5">
                <span className="tnum pt-[3px] font-mono text-[11px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
                <div className="min-w-0">
                  <p className="font-serif text-[16px] leading-snug text-ink">
                    {q.question}
                    <Cites ids={q.source_ids} prefix={PREFIX} />
                  </p>
                  {q.reason && <p className="mt-0.5 text-[12px] text-ink-2">{q.reason}</p>}
                  {q.event_id && (
                    <Link href={`/portfolio/events/${q.event_id}`} className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3 hover:text-accent">
                      From event →
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section id="r-sec-noise_filtered" className="scroll-mt-24">
        <BriefHeading n={6}>Noise filtered</BriefHeading>
        <p className="tnum text-[13.5px] text-ink">
          {typeof nf.raw_changes === "number" ? `${nf.raw_changes.toLocaleString("en-US")} raw changes observed` : "Raw changes not stored"}
          {typeof nf.noise_events === "number" && ` → ${nf.noise_events} events filtered as noise by rule`}.
        </p>
      </section>

      {body.limitations && (
        <p className="border-t border-rule pt-3 text-[12px] leading-relaxed text-ink-3">
          <span className="label mr-2 text-ink-2">Limitations</span>
          {body.limitations}
        </p>
      )}
    </div>
  );
}

export function PortfolioBriefReportView({ body }: { body: PortfolioBriefBody }) {
  return <PortfolioBriefSections body={body} prefix={PREFIX} />;
}
