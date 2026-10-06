import type { Metadata } from "next";
import clsx from "clsx";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { SectionLabel } from "@/components/SectionLabel";
import { StatTile } from "@/components/StatTile";
import { PageHeader } from "@/components/insight/PageHeader";
import { relativeTime } from "@/components/insight/relativeTime";
import { AddToRadar } from "@/components/insight/status/AddToRadar";
import { ProviderTable, type ProviderRow } from "@/components/insight/status/ProviderTable";
import { getStatus } from "@/lib/api";
import { fmtCompact, fmtDate, fmtDateTime, fmtNum, humanize } from "@/lib/format";
import type { JsonObject } from "@/lib/types";

export const metadata: Metadata = { title: "Status" };

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const usd = (v: number | null) => (v === null ? "—" : v === 0 ? "$0.00" : v < 1 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);

function KV({ k, v, muted, title }: { k: string; v: ReactNode; muted?: boolean; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-rule py-2" title={title}>
      <dt className="label">{k}</dt>
      <dd className={clsx("tnum text-right font-mono text-[12.5px]", muted ? "text-ink-3" : "text-ink")}>{v}</dd>
    </div>
  );
}

function LlmPanel({ llm }: { llm: JsonObject }) {
  const enabled = llm.enabled === true;
  const provider = str(llm.active_provider) ?? str(llm.provider) ?? "none";
  const models = (llm.models && typeof llm.models === "object" ? llm.models : {}) as Record<string, unknown>;
  const hit = num(llm.cache_hit_rate);
  const known = new Set([
    "provider",
    "active_provider",
    "enabled",
    "models",
    "calls_24h",
    "cost_24h",
    "cost_total",
    "cache_hit_rate",
    "cached_responses",
    "max_usd_per_investigation",
  ]);
  const extra = Object.entries(llm).filter(([k, v]) => !known.has(k) && (typeof v !== "object" || v === null));
  return (
    <div>
      <div className={clsx("mb-4 border-l-2 pl-3", enabled ? "border-ink" : "border-ink-3")}>
        <p className={clsx("font-mono text-[11px] uppercase tracking-[0.08em]", enabled ? "text-ink" : "text-ink-2")}>
          {enabled ? `● Enabled · ${provider}` : "○ LLM disabled — template output"}
        </p>
        <p className="mt-1 text-[12.5px] leading-snug text-ink-2">
          {enabled
            ? "Narratives, memos and search parsing may use the models below; every generated claim is labelled Model opinion."
            : "Narratives, memos and briefs are rendered from deterministic templates and labelled “template”. Scores never depend on an LLM."}
        </p>
      </div>
      <dl>
        {Object.entries(models).map(([tier, model]) => (
          <KV key={tier} k={`Model · ${tier}`} v={String(model)} muted={!enabled} />
        ))}
        <KV k="Calls · 24h" v={fmtCompact(num(llm.calls_24h))} />
        <KV k="Cost · 24h" v={usd(num(llm.cost_24h))} />
        <KV k="Cost · total" v={usd(num(llm.cost_total))} />
        <KV
          k="Cache hit rate"
          v={hit === null ? "—" : `${fmtNum(hit * 100, 1)}%`}
          title="Share of LLM requests served from the response cache"
        />
        <KV k="Cached responses" v={fmtCompact(num(llm.cached_responses))} />
        {num(llm.max_usd_per_investigation) !== null && (
          <KV
            k="Budget / investigation"
            v={usd(num(llm.max_usd_per_investigation))}
            title="Hard spend cap per investigation run"
          />
        )}
        {extra.map(([k, v]) => (
          <KV key={k} k={humanize(k)} v={String(v)} />
        ))}
      </dl>
      {!enabled && num(llm.calls_24h) ? (
        <p className="mt-2 text-[11.5px] leading-snug text-ink-3">
          Calls and cost above were recorded while a provider was active, or are served from cache.
        </p>
      ) : null}
    </div>
  );
}

function DatabasePanel({
  db,
  scoreVersion,
  lastScored,
  now,
}: {
  db: JsonObject;
  scoreVersion: string;
  lastScored: string | null;
  now: Date;
}) {
  const counts: [string, string][] = [
    ["companies", "Companies"],
    ["people", "People"],
    ["repositories", "Repositories"],
    ["papers", "Papers"],
    ["signals", "Signals"],
    ["jobs", "Jobs"],
    ["metric_snapshots", "Metric snapshots"],
    ["score_snapshots", "Score snapshots"],
  ];
  const demo = num(db.demo_companies);
  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
        {counts.map(([k, label]) => (
          <div key={k} className="border-t border-rule pt-2">
            <dt className="label">{label}</dt>
            <dd className={clsx("tnum mt-1 font-serif text-[26px] leading-none", num(db[k]) ? "text-ink" : "text-ink-3")}>
              {fmtCompact(num(db[k]))}
            </dd>
          </div>
        ))}
      </dl>
      <dl className="mt-6 grid gap-x-10 sm:grid-cols-2">
        <div>
          <KV k="Snapshot days" v={fmtCompact(num(db.snapshot_days))} />
          <KV k="First signal" v={fmtDate(str(db.first_signal_at))} />
          <KV k="Last signal" v={str(db.last_signal_at) ? `${fmtDateTime(str(db.last_signal_at))}` : "—"} />
        </div>
        <div>
          <KV k="Score version" v={scoreVersion} />
          <KV
            k="Last scored"
            v={lastScored ? relativeTime(lastScored, now) : "never"}
            muted={!lastScored}
            title={lastScored ? fmtDateTime(lastScored) : undefined}
          />
          <KV
            k="Demo companies"
            v={demo === null ? "—" : fmtCompact(demo)}
            muted={!demo}
            title="Fictional entities, always badged DEMO DATA"
          />
        </div>
      </dl>
    </div>
  );
}

export default async function StatusPage() {
  await connection();
  const status = await getStatus();
  const now = new Date();

  const rows: ProviderRow[] = status.providers.map((p) => ({
    ...p,
    last_success_rel: relativeTime(p.last_success_at, now),
    last_run_rel: relativeTime(p.last_run_at, now),
  }));
  const healthy = status.providers.filter((p) => p.state === "healthy").length;
  const errored = status.providers.filter((p) => p.state === "error");
  const degraded = status.providers.filter((p) => p.state === "degraded");
  const live = status.providers.filter((p) => p.state !== "disabled");
  const records = status.providers.reduce((s, p) => s + p.records_collected, 0);
  const signals = status.providers.reduce((s, p) => s + p.signals_created, 0);
  const cost24 = num(status.llm.cost_24h);

  const headline =
    errored.length > 0
      ? `${errored.map((p) => p.label).join(", ")} ${errored.length === 1 ? "is" : "are"} failing.`
      : degraded.length > 0
        ? `${healthy} of ${live.length} live providers healthy.`
        : `All ${live.length} live providers healthy.`;

  return (
    <div>
      <PageHeader
        kicker={
          <>
            System status · score {status.score_version}
            {status.demo_mode && " · demo mode on"}
          </>
        }
        title={headline}
        dek={
          <>
            {degraded.length > 0 && <span className="text-ink">Degraded: {degraded.map((p) => p.label).join(", ")}. </span>}
            Where the radar’s data comes from, how fresh it is, and what it cost. Last scored{" "}
            {status.last_scored_at ? (
              <span className="text-ink">
                {relativeTime(status.last_scored_at, now)} ({fmtDateTime(status.last_scored_at)})
              </span>
            ) : (
              "never"
            )}
            .{status.demo_mode && " Demo mode mixes a clearly-badged fictional dataset into every view."}
          </>
        }
      />

      <div className="mb-14 grid grid-cols-2 gap-6 md:grid-cols-4">
        <StatTile
          value={`${healthy}/${status.providers.length}`}
          label="Providers healthy"
          accent={errored.length > 0}
          sub={errored.length ? `${errored.length} in error` : `${degraded.length} degraded`}
        />
        <StatTile value={fmtCompact(records)} label="Records collected" sub="latest run per provider" />
        <StatTile value={fmtCompact(signals)} label="Signals created" sub="latest run per provider" />
        <StatTile value={usd(cost24)} label="LLM cost · 24h" sub={status.llm.enabled ? "provider active" : "LLM disabled"} />
      </div>

      <section className="mb-16" aria-labelledby="st-providers">
        <SectionLabel index={1} id="st-providers" right={`${status.providers.length} providers`}>
          Providers
        </SectionLabel>
        <div className="mt-5">
          <ProviderTable providers={rows} />
        </div>
      </section>

      <div className="mb-16 grid grid-cols-12 gap-x-10 gap-y-16">
        <section className="col-span-12 lg:col-span-5" aria-labelledby="st-llm">
          <SectionLabel index={2} id="st-llm">
            Language models
          </SectionLabel>
          <div className="mt-5">
            <LlmPanel llm={status.llm} />
          </div>
        </section>
        <section className="col-span-12 lg:col-span-7" aria-labelledby="st-db">
          <SectionLabel index={3} id="st-db">
            Database
          </SectionLabel>
          <div className="mt-5">
            <DatabasePanel
              db={status.database}
              scoreVersion={status.score_version}
              lastScored={status.last_scored_at}
              now={now}
            />
          </div>
        </section>
      </div>

      <section aria-labelledby="st-add">
        <SectionLabel index={4} id="st-add" right="manual ingest">
          Add to radar
        </SectionLabel>
        <p className="mt-4 mb-6 max-w-[72ch] text-[13.5px] leading-relaxed text-ink-2">
          Point the radar at something it has not found on its own. Each run is logged like a scheduled provider run; the created
          or matched entity is scored immediately.
        </p>
        <AddToRadar />
      </section>
    </div>
  );
}
