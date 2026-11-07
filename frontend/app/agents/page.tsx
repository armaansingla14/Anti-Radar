import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import type { ReactNode } from "react";
import { SectionLabel } from "@/components/SectionLabel";
import { PageHeader } from "@/components/insight/PageHeader";
import { Quiet } from "@/components/entity/primitives";
import { MetricCell, MetricNotes } from "@/components/agents/MetricCell";
import { METRIC_COLUMNS, metricFootnotes } from "@/components/agents/logic";
import { getAgentEvaluations, getAgentFailures, getAgents } from "@/lib/api";
import { fmtDateTime, humanize } from "@/lib/format";
import type { EvaluationOut, FailureOut, LLMTelemetryRow } from "@/lib/types-agents";

export const metadata: Metadata = { title: "Agents" };

const th = "label whitespace-nowrap px-2 py-2 text-left font-normal align-bottom";
const td = "px-2 py-2.5 align-top";

function Sec({ i, id, title, right, children }: { i: number; id: string; title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="scroll-mt-24">
      <SectionLabel index={i} id={`${id}-h`} right={right}>
        {title}
      </SectionLabel>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function TelemetryTable({ rows }: { rows: LLMTelemetryRow[] }) {
  const notes = new Map<string, number>();
  for (const r of rows)
    for (const m of [r.ok_rate, r.latency_p50_ms, r.latency_p95_ms, r.cache_hit_rate])
      if (m.value === null && m.reason && !notes.has(m.reason)) notes.set(m.reason, notes.size + 1);
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse border-t border-ink text-[12.5px]">
          <thead>
            <tr className="border-b border-ink">
              <th className={th} scope="col">Purpose</th>
              <th className={th} scope="col">Agent</th>
              <th className={clsx(th, "text-right")} scope="col">Calls</th>
              <th className={clsx(th, "text-right")} scope="col">Live</th>
              <th className={clsx(th, "text-right")} scope="col">OK rate</th>
              <th className={clsx(th, "text-right")} scope="col">p50</th>
              <th className={clsx(th, "text-right")} scope="col">p95</th>
              <th className={clsx(th, "text-right")} scope="col">Cache hits</th>
              <th className={clsx(th, "text-right")} scope="col">Cost</th>
              <th className={th} scope="col">Models</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.purpose} className="border-b border-rule">
                <td className={clsx(td, "font-mono text-[11.5px] text-ink")}>{r.purpose}</td>
                <td className={clsx(td, "font-mono text-[10.5px] uppercase text-ink-2")}>{r.agent}</td>
                <td className={clsx(td, "tnum text-right font-mono text-[11.5px]")}>{r.calls}</td>
                <td className={clsx(td, "tnum text-right font-mono text-[11.5px]")}>{r.live_calls}</td>
                <td className={clsx(td, "text-right")}>
                  <MetricCell m={r.ok_rate} kind="rate" notes={notes} />
                </td>
                <td className={clsx(td, "text-right")}>
                  <MetricCell m={r.latency_p50_ms} kind="ms" notes={notes} />
                </td>
                <td className={clsx(td, "text-right")}>
                  <MetricCell m={r.latency_p95_ms} kind="ms" notes={notes} />
                </td>
                <td className={clsx(td, "text-right")}>
                  <MetricCell m={r.cache_hit_rate} kind="rate" notes={notes} />
                </td>
                <td className={clsx(td, "tnum text-right font-mono text-[11.5px]")}>${r.cost_usd.toFixed(r.cost_usd > 0 && r.cost_usd < 0.01 ? 4 : 2)}</td>
                <td className={clsx(td, "font-mono text-[10.5px] text-ink-2")}>{r.models.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <MetricNotes notes={notes} />
    </>
  );
}

function EvaluationsTable({ rows }: { rows: EvaluationOut[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] border-collapse border-t border-ink text-[12.5px]">
        <thead>
          <tr className="border-b border-ink">
            <th className={th} scope="col">Output</th>
            <th className={th} scope="col">Agent · author</th>
            <th className={clsx(th, "text-right")} scope="col">Claims</th>
            <th className={clsx(th, "text-right")} scope="col" title="Delivered citations that point to a registered source">Citations valid</th>
            <th className={clsx(th, "text-right")} scope="col">Unsupported</th>
            <th className={clsx(th, "text-right")} scope="col">Sources used</th>
            <th className={clsx(th, "text-right")} scope="col">Latency</th>
            <th className={th} scope="col">Flags</th>
            <th className={th} scope="col">Review</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className="border-b border-rule">
              <td className={clsx(td, "min-w-[220px]")}>
                {e.report_url ? (
                  <Link href={e.report_url} className="text-ink hover:text-accent">
                    {e.report_title ?? "Report"}
                  </Link>
                ) : (
                  <span className="text-ink">{e.report_title ?? "—"}</span>
                )}
                <span className="block font-mono text-[10px] text-ink-3">{fmtDateTime(e.evaluated_at)}</span>
              </td>
              <td className={clsx(td, "font-mono text-[10.5px] uppercase text-ink-2")}>
                {e.agent}
                <span className="block text-ink-3">{e.model ?? e.generator ?? "—"}</span>
              </td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px]")}>{e.claims_total}</td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px]")}>
                {e.citations_total - e.invalid_citations}/{e.citations_total}
              </td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px]", e.unsupported_claims > 0 && "text-accent")}>{e.unsupported_claims}</td>
              <td className={clsx(td, "tnum text-right font-mono text-[11.5px]")}>
                {e.sources_used}/{e.sources_registered}
              </td>
              <td className={clsx(td, "tnum whitespace-nowrap text-right font-mono text-[11.5px]", e.latency_ms === null && "text-ink-3")} title={e.latency_ms === null ? "No timing entry in the trace" : undefined}>
                {e.latency_ms === null ? "—" : e.latency_ms >= 1000 ? `${(e.latency_ms / 1000).toFixed(1)} s` : `${Math.round(e.latency_ms)} ms`}
              </td>
              <td className={clsx(td, "font-mono text-[10px] uppercase text-ink-2")}>
                {e.flags.length === 0 ? <span className="text-ink-3">none</span> : [...new Set(e.flags.map((f) => f.category))].join(", ")}
              </td>
              <td className={clsx(td, "font-mono text-[10px] uppercase text-ink-2")}>{humanize(e.review_status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default async function AgentsPage() {
  const soft = <T,>(p: Promise<T>) => p.catch(() => null);
  const [ov, failures, evaluations] = await Promise.all([getAgents(), soft(getAgentFailures()), soft(getAgentEvaluations({ limit: 12 }))]);
  const notes = metricFootnotes(ov.agents);
  const reg = ov.regression;
  const fails: FailureOut[] = failures ?? [];
  const open = fails.filter((f) => f.status === "OPEN").length;
  const fixed = fails.filter((f) => f.status === "FIXED").length;
  const confirmed = fails.filter((f) => f.status === "CONFIRMED").length;
  const mc = ov.model_comparison;
  const recs = mc.recommendations.filter((r) => r.recommendation);

  return (
    <div className="pb-24">
      <PageHeader
        kicker="Agent evaluation · control plane"
        title="Agents — can we trust the research system?"
        dek={
          <>
            Agents accelerate research; every output is evaluated, sourced and auditable. Agents do not replace judgment. Each number below
            is computed by evaluator <span className="font-mono text-[12.5px] text-ink">{ov.evaluator_version}</span> from stored outputs;
            where it cannot be computed it is shown as “—” with the reason, never estimated.
          </>
        }
        aside={
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            <div className="border-t border-ink pt-2">
              <dt className="label">Regression cases</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">
                {reg.passing}
                <span className="text-[20px] text-ink-3">/{reg.cases_total}</span>
              </dd>
              <dd className="mt-1 text-[11.5px] text-ink-2">
                passing{reg.failing ? ` · ${reg.failing} failing` : ""}
                {reg.never_run ? ` · ${reg.never_run} never run` : ""}
              </dd>
            </div>
            <div className="border-t border-ink pt-2">
              <dt className="label">Failure ledger</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{failures ? fails.length : "—"}</dd>
              <dd className="mt-1 text-[11.5px] text-ink-2">{failures ? `${open} open · ${confirmed} confirmed · ${fixed} fixed` : "ledger unavailable"}</dd>
            </div>
          </dl>
        }
      />

      <nav aria-label="Agents pages" className="-mt-4 mb-12 flex flex-wrap gap-x-6 gap-y-2 border-y border-rule py-2.5 font-mono text-[11px] uppercase tracking-[0.08em]">
        <a href="#agents" className="text-ink hover:text-accent">Per-agent metrics</a>
        <Link href="/agents/failures" className="text-ink-2 hover:text-accent">Failure ledger →</Link>
        <Link href="/agents/cases" className="text-ink-2 hover:text-accent">Regression cases →</Link>
        <a href="#methodology" className="text-ink-2 hover:text-accent">Methodology</a>
        <a href="#taxonomy" className="text-ink-2 hover:text-accent">Failure taxonomy</a>
      </nav>

      <div className="space-y-16">
        <Sec i={1} id="agents" title="Per-agent metrics" right={<span className="tnum">{ov.agents.length} agents<span className="max-sm:hidden"> · hover a value for n</span></span>}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] border-collapse border-t border-ink text-[12.5px]">
              <thead>
                <tr className="border-b border-ink">
                  <th className={th} scope="col">Agent</th>
                  <th className={clsx(th, "text-right")} scope="col">Runs</th>
                  {METRIC_COLUMNS.map((c) => (
                    <th key={c.key} className={clsx(th, "px-1.5 text-right")} scope="col" title={ov.methodology[c.key] ?? c.label}>
                      {c.short}
                    </th>
                  ))}
                  <th className={clsx(th, "text-right")} scope="col" title={ov.methodology.human_review_pending}>
                    Review
                  </th>
                </tr>
              </thead>
              <tbody>
                {ov.agents.map((a) => (
                  <tr key={a.key} className="border-b border-rule" data-demo-agent={a.name}>
                    <td className={clsx(td, "min-w-[180px] max-w-[230px]")}>
                      <p className="font-mono text-[11.5px] uppercase tracking-[0.06em] text-ink">{a.name}</p>
                      <p className="mt-0.5 text-[11.5px] leading-snug text-ink-2">{a.description}</p>
                      {a.open_failures > 0 && (
                        <Link href={`/agents/failures?agent=${encodeURIComponent(a.name)}&status=OPEN`} className="mt-0.5 inline-block font-mono text-[10px] uppercase text-accent">
                          {a.open_failures} open failure{a.open_failures === 1 ? "" : "s"} →
                        </Link>
                      )}
                    </td>
                    <td className={clsx(td, "tnum text-right font-serif text-[18px] leading-none", a.runs === 0 ? "text-ink-3" : "text-ink")}>{a.runs}</td>
                    {METRIC_COLUMNS.map((c) => (
                      <td key={c.key} className={clsx(td, "px-1.5 text-right")}>
                        <MetricCell m={a.metrics[c.key]} kind={c.kind} notes={notes} />
                      </td>
                    ))}
                    <td className={clsx(td, "text-right")}>
                      <MetricCell m={a.metrics.human_review_pending} kind="count" notes={notes} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <MetricNotes notes={notes} />
          <p className="mt-3 max-w-[90ch] text-[11.5px] leading-snug text-ink-3">
            Rates are pooled over the runs (n in each tooltip). Citation validity measures that a citation points to a registered source —
            existence, not semantic support. Review = flags awaiting human review plus open failures.
          </p>
        </Sec>

        <div className="grid gap-x-12 gap-y-16 lg:grid-cols-2">
          <Sec i={2} id="regression" title="Regression suite" right={<Link href="/agents/cases" className="hover:text-accent">All cases →</Link>}>
            {reg.error ? (
              <Quiet>The case files could not be loaded: {reg.error}</Quiet>
            ) : (
              <>
                <dl className="grid grid-cols-4 gap-x-5">
                  {[
                    ["Cases", reg.cases_total],
                    ["Passing", reg.passing],
                    ["Failing", reg.failing],
                    ["Never run", reg.never_run],
                  ].map(([k, v]) => (
                    <div key={k} className={clsx("border-t pt-2", k === "Failing" && Number(v) > 0 ? "border-accent" : "border-ink")}>
                      <dt className="label">{k}</dt>
                      <dd className={clsx("tnum mt-1 font-serif text-[30px] leading-none", k === "Failing" && Number(v) > 0 ? "text-accent" : "text-ink")}>{v}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-4 text-[12.5px] leading-snug text-ink-2">
                  Last run {reg.last_run_at ? fmtDateTime(reg.last_run_at) : "— never"}. Every documented failure is pinned by a YAML case or a
                  pytest test so it cannot silently return.{" "}
                  <Link href="/agents/failures" className="link">
                    Failure ledger →
                  </Link>
                </p>
              </>
            )}
          </Sec>

          <Sec i={3} id="models" title="Model comparison">
            {mc.configured_models.length === 0 ? (
              <div className="border-y border-rule py-4">
                <p className="text-[13.5px] text-ink">No models configured for comparison.</p>
                <p className="mt-1.5 text-[12.5px] leading-snug text-ink-2">
                  Comparison is opt-in: set <code className="font-mono text-[11.5px] text-ink">EVAL_PROVIDERS</code> on the backend (comma-separated,
                  e.g. <code className="font-mono text-[11.5px] text-ink">EVAL_PROVIDERS=anthropic,openai</code>) with that provider&apos;s API key, then run{" "}
                  <code className="font-mono text-[11.5px] text-ink">radar eval --compare-models</code>. Until then only the deterministic templates are
                  measured.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-rule border-y border-rule">
                {mc.configured_models.map((m) => (
                  <li key={`${m.provider}/${m.model}`} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                    <span className="font-mono text-[12px] text-ink">
                      {m.provider} / {m.model}
                    </span>
                    <span className="font-mono text-[10px] uppercase text-ink-3">{m.tiers.join(" · ") || "—"}</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-5">
              <p className="label mb-2 text-ink">Recommendations by task</p>
              <ul className="divide-y divide-rule/70 border-t border-ink">
                {mc.recommendations.map((r) => (
                  <li key={r.task} className="grid gap-x-3 py-2 sm:grid-cols-[190px_minmax(0,1fr)]">
                    <span className="font-mono text-[11px] text-ink">{r.task}</span>
                    <span className="text-[12.5px] leading-snug text-ink-2">
                      {r.recommendation ? (
                        <span className="mr-2 font-mono text-[11px] text-ink">
                          {r.recommendation.provider} / {r.recommendation.model}
                        </span>
                      ) : (
                        <span className="mr-2 font-mono text-[10px] uppercase text-ink-3">No recommendation</span>
                      )}
                      {r.reason}
                    </span>
                  </li>
                ))}
              </ul>
              {recs.length === 0 && <p className="mt-2 text-[11.5px] text-ink-3">A recommendation needs at least two models with 10 or more evaluated cases each; none is made from less.</p>}
            </div>
          </Sec>
        </div>

        <Sec i={4} id="evaluations" title="Latest evaluated outputs" right={<span className="tnum">{evaluations?.length ?? 0}</span>}>
          {!evaluations ? <Quiet>Evaluations unavailable.</Quiet> : evaluations.length === 0 ? <Quiet>No evaluated outputs yet (run `radar eval`).</Quiet> : <EvaluationsTable rows={evaluations} />}
        </Sec>

        <Sec i={5} id="telemetry" title="LLM telemetry" right={<span className="tnum">{ov.llm_telemetry.length} purposes</span>}>
          {ov.llm_telemetry.length === 0 ? (
            <Quiet>No LLM calls recorded — this deployment runs deterministic templates and rules. Nothing here is a model output.</Quiet>
          ) : (
            <TelemetryTable rows={ov.llm_telemetry} />
          )}
        </Sec>

        <div className="grid gap-x-12 gap-y-16 lg:grid-cols-2">
          <Sec i={6} id="methodology" title="Methodology">
            <dl className="border-t border-ink">
              {Object.entries(ov.methodology).map(([k, v]) => (
                <div key={k} className="grid gap-x-4 border-b border-rule/70 py-2 sm:grid-cols-[170px_minmax(0,1fr)]">
                  <dt className="font-mono text-[10.5px] uppercase tracking-[0.04em] text-ink">{k.replace(/_/g, " ")}</dt>
                  <dd className="text-[12.5px] leading-snug text-ink-2">{v}</dd>
                </div>
              ))}
            </dl>
          </Sec>
          <Sec i={7} id="taxonomy" title="Failure taxonomy" right={<span className="tnum">{ov.taxonomy.length} categories</span>}>
            <dl className="border-t border-ink">
              {ov.taxonomy.map((t) => (
                <div key={t.category} id={`cat-${t.category}`} className="grid scroll-mt-24 gap-x-4 border-b border-rule/70 py-2 sm:grid-cols-[200px_minmax(0,1fr)]">
                  <dt>
                    <Link href={`/agents/failures?category=${t.category}`} className="font-mono text-[10.5px] uppercase tracking-[0.04em] text-ink hover:text-accent">
                      {t.category.replace(/_/g, " ")}
                    </Link>
                  </dt>
                  <dd className="text-[12.5px] leading-snug text-ink-2">{t.definition}</dd>
                </div>
              ))}
            </dl>
          </Sec>
        </div>
      </div>
    </div>
  );
}
