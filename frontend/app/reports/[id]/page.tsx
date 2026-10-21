import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { DemoBadge } from "@/components/Badges";
import { loadReport } from "@/components/entity/data";
import { GeneratorLabel } from "@/components/entity/primitives";
import { ReportActions } from "@/components/entity/ReportActions";
import { isStructuredBody, ReportBody, sectionsFor } from "@/components/entity/ReportBody";
import { Sources } from "@/components/entity/Sources";
import { citedSourceIds, isMeetingBrief } from "@/components/meet/logic";
import { meetingSections, MeetingBriefView } from "@/components/meet/MeetingBriefView";
import { fmtDate, fmtDateTime, humanize } from "@/lib/format";

export async function generateMetadata({ params }: PageProps<"/reports/[id]">): Promise<Metadata> {
  const { id } = await params;
  const r = await loadReport(id).catch(() => null);
  return { title: r ? r.title : "Report" };
}

const KIND: Record<string, string> = { brief: "Anti Brief", memo: "Anti Memo", morning_brief: "Morning Brief", meeting_brief: "Meeting Brief" };

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

export default async function ReportPage({ params }: PageProps<"/reports/[id]">) {
  const { id } = await params;
  const report = await loadReport(id);
  if (!report) notFound();

  const body = report.body ?? {};
  const kind = KIND[report.kind] ?? humanize(report.kind);
  const entityHref =
    report.entity_slug && report.entity_type === "company"
      ? `/companies/${report.entity_slug}`
      : report.entity_slug && report.entity_type === "person"
        ? `/founders/${report.entity_slug}`
        : null;
  const meeting = isMeetingBrief(report.kind, body);
  const subject =
    str(body.company) ?? str(body.subject) ?? str(body.person) ?? str(body.name) ?? report.title.replace(/^(Anti (Brief|Memo)|Meeting Brief)\s+—\s+/, "");
  const oneLiner = str(body.one_liner);
  const structured = meeting || isStructuredBody(body);
  const contents = meeting ? meetingSections(body) : structured ? sectionsFor(report.kind, body) : [];
  const briefDate = meeting ? str(body.date) : null;
  const cited = meeting ? citedSourceIds(body).length : null;
  const filename = `${report.kind}-${report.entity_slug ?? report.id}.md`;

  return (
    <article data-demo="report" data-demo-kind={report.kind} className="mx-auto max-w-[980px] pb-12 print:max-w-none">
      {/* Print: hide site chrome, keep the memo. */}
      <style>{`@media print { body > header, body > footer, #main ~ *, nav[aria-label] { display: none !important; } html, body { background: #fff; } a { text-decoration: none; } }`}</style>

      <header className="border-b-[3px] border-double border-ink pb-7">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span className="label text-accent">{kind}</span>
            <span aria-hidden className="h-px w-5 bg-ink-3" />
            <span className="label">Anti Radar · Research brief</span>
            {report.is_demo && <DemoBadge note="Report about a fictional demo entity" />}
          </div>
          <ReportActions id={report.id} filename={filename} />
        </div>

        <h1 className="mt-7 font-serif text-[54px] leading-[1.0] tracking-[-0.02em] text-ink max-md:text-[40px]">
          {entityHref ? (
            <Link href={entityHref} className="decoration-accent decoration-1 underline-offset-[6px] hover:underline">
              {subject}
            </Link>
          ) : (
            subject
          )}
        </h1>
        {meeting && (
          <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            Meeting brief{briefDate ? ` · ${fmtDate(briefDate)}` : ""} · for a first conversation — not diligence
          </p>
        )}
        {oneLiner && <p className="mt-4 max-w-[60ch] font-serif text-[21px] italic leading-snug text-ink-2">{oneLiner}</p>}

        <dl className="mt-7 grid grid-cols-2 gap-x-8 gap-y-3 border-t border-rule pt-4 md:grid-cols-5">
          <div>
            <dt className="label text-ink-3">Document</dt>
            <dd className="mt-1 text-[13px] text-ink">{kind}</dd>
          </div>
          <div>
            <dt className="label text-ink-3">Subject</dt>
            <dd className="mt-1 text-[13px] text-ink">
              {entityHref ? (
                <Link href={entityHref} className="link">
                  {humanize(report.entity_type ?? "entity")} profile →
                </Link>
              ) : (
                "—"
              )}
            </dd>
          </div>
          <div>
            <dt className="label text-ink-3">Generated</dt>
            <dd className="tnum mt-1 font-mono text-[11.5px] text-ink">{fmtDateTime(report.created_at)}</dd>
          </div>
          <div>
            <dt className="label text-ink-3">Author</dt>
            <dd className="mt-1">
              <GeneratorLabel generator={report.generator} model={report.model} />
            </dd>
          </div>
          <div>
            <dt className="label text-ink-3">Cost · status</dt>
            <dd className="tnum mt-1 font-mono text-[11.5px] text-ink">
              ${report.cost_usd.toFixed(report.cost_usd > 0 && report.cost_usd < 0.01 ? 4 : 2)} · {report.status}
            </dd>
          </div>
        </dl>

        <p className="mt-5 max-w-[78ch] text-[12.5px] leading-relaxed text-ink-2">
          {report.generator === "template"
            ? "Produced by ANTI RADAR as a deterministic template from database facts — no language model was used (LLM disabled). "
            : "Drafted by a language model from collected sources; verify before relying on it. "}
          Every claim carries its source citations and a marker: <span className="font-mono text-[10.5px]">● FACT</span> observed in a cited
          source, <span className="font-mono text-[10.5px]">◆ INFERENCE</span> derived from facts,{" "}
          <span className="font-mono text-[10.5px]">○ MODEL OPINION</span> generated judgement. Empty sections are left empty, not guessed.
        </p>
      </header>

      <div className="mt-10 grid gap-x-12 gap-y-12 lg:grid-cols-[minmax(0,1fr)_260px] print:block">
        <div className="min-w-0">
          {meeting ? <MeetingBriefView body={body} /> : <ReportBody kind={report.kind} body={body} markdown={report.markdown} />}
        </div>
        {structured && (
          <aside className="hidden lg:block print:hidden">
            <nav aria-label="Report contents" className="sticky top-6 border-t border-ink pt-3">
              <p className="label mb-3 text-ink">Contents</p>
              <ol className="space-y-1.5">
                {contents.map((def, i) => (
                  <li key={def.key}>
                    <a href={`#r-sec-${def.key}`} className="grid grid-cols-[22px_1fr] text-[12.5px] text-ink-2 transition-colors hover:text-accent">
                      <span className="tnum font-mono text-[10px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
                      {def.title}
                    </a>
                  </li>
                ))}
                <li>
                  <a href="#sources" className="grid grid-cols-[22px_1fr] text-[12.5px] text-ink-2 hover:text-accent">
                    <span className="font-mono text-[10px] text-ink-3">—</span>Sources
                  </a>
                </li>
              </ol>
            </nav>
          </aside>
        )}
      </div>

      <section id="sources" className="mt-6 scroll-mt-24 border-t-[3px] border-double border-ink pt-4">
        <h2 className="mb-4 flex items-baseline justify-between">
          <span className="font-serif text-[24px] text-ink">Sources</span>
          <span className="label tnum">
            {cited !== null && cited < report.sources.length ? `${cited} cited · ${report.sources.length} registered` : `${report.sources.length} cited`}
          </span>
        </h2>
        <Sources sources={report.sources} demo={report.is_demo} />
      </section>

      <footer className="mt-10 flex flex-wrap justify-between gap-3 border-t border-rule pt-3 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-3">
        <span>Anti Radar · Conviction before consensus</span>
        <span className="tnum">Report {report.id.slice(0, 8)}</span>
      </footer>
    </article>
  );
}
