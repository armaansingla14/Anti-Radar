/**
 * /changes sections: new to Radar, signals by family, score moves, momentum, collisions, formation,
 * Who to meet, caveats. Server components; numbers render exactly as the report returns them.
 */
import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { ChangeBadge } from "@/components/Badges";
import { MaybeExternal } from "@/components/HandleLinks";
import { familyLabels } from "@/lib/labels";
import type { MeetMove } from "@/lib/types-changes";
import type {
  ChangeSection,
  ChangesReport,
  CollisionChange,
  FamilySignals,
  FormationChange,
  ReportChangeItem,
  ScoreChange,
  ScoreChangeList,
} from "@/lib/types-changes";
import {
  dayLabel,
  formationChangeHref,
  humanizeDays,
  itemHref,
  meetRankText,
  num,
  refHref,
  sectionState,
  seenVsCreated,
  signed,
  topComponents,
} from "./logic";

const INLINE = 6; // rows shown before the rest folds into <details>

function Quiet({ children }: { children: ReactNode }) {
  return <p className="py-3 text-[13px] leading-snug text-ink-3">{children}</p>;
}

/** Name link: in-app page, external source (↗), or plain text. */
function ItemName({ item, className }: { item: Pick<ReportChangeItem, "entity_type" | "slug" | "id" | "url" | "name">; className?: string }) {
  const link = itemHref(item);
  const cls = clsx("decoration-accent decoration-1 underline-offset-4 hover:underline", className);
  if (!link) return <span className={className}>{item.name}</span>;
  if (!link.external)
    return (
      <Link href={link.href} className={cls}>
        {item.name}
      </Link>
    );
  return (
    <MaybeExternal href={link.href} plain className={cls}>
      {item.name}
      <span aria-hidden className="ml-1 font-mono text-[10px] text-ink-3">↗</span>
    </MaybeExternal>
  );
}

/** "first seen Nov 5 · created Mar 2022" + optional parent link. */
function ItemMeta({ item }: { item: ReportChangeItem }) {
  const { seen, created } = seenVsCreated(item);
  const parent = item.parent && item.entity_type !== "signal" ? item.parent : null;
  const parentHref = refHref(parent);
  return (
    <p className="mt-0.5 flex flex-wrap gap-x-2.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
      {seen && <span>{seen}</span>}
      <span className={created ? "text-ink-2" : undefined}>{created ?? "creation date not recorded"}</span>
      {parent &&
        (parentHref ? (
          <Link href={parentHref} className="link normal-case tracking-normal text-ink-2">
            {parent.name}
          </Link>
        ) : (
          <span className="normal-case tracking-normal">{parent.name}</span>
        ))}
    </p>
  );
}

function ItemRow({ item }: { item: ReportChangeItem }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 py-2">
      <div className="min-w-0">
        <ItemName item={item} className="font-serif text-[15.5px] leading-tight text-ink" />
        {item.detail && <p className="mt-0.5 line-clamp-1 text-[12.5px] leading-snug text-ink-2">{item.detail}</p>}
        <ItemMeta item={item} />
      </div>
      {item.score !== null && (
        <span className="tnum pt-[2px] text-right font-mono text-[13px] text-ink" title="Score on the later day">
          {num(item.score)}
        </span>
      )}
    </div>
  );
}

/** First INLINE rows, then the rest of the listed top items behind a native disclosure. */
function Folded<T>({ items, render, keyOf, noun }: { items: T[]; render: (x: T) => ReactNode; keyOf: (x: T) => string; noun: string }) {
  const head = items.slice(0, INLINE);
  const rest = items.slice(INLINE);
  return (
    <>
      <ol className="divide-y divide-rule">
        {head.map((x) => (
          <li key={keyOf(x)}>
            {render(x)}
          </li>
        ))}
      </ol>
      {rest.length > 0 && (
        <details className="group border-t border-rule">
          <summary className="label cursor-pointer list-none py-2 text-ink-2 hover:text-ink">
            <span className="group-open:hidden">Show {rest.length} more {noun} →</span>
            <span className="hidden group-open:inline">Show fewer ↑</span>
          </summary>
          <ol className="divide-y divide-rule border-t border-rule">
            {rest.map((x) => (
              <li key={keyOf(x)}>
                {render(x)}
              </li>
            ))}
          </ol>
        </details>
      )}
    </>
  );
}

function CountHead({ id, title, count, note }: { id: string; title: string; count: number; note?: ReactNode }) {
  return (
    <div id={id} className="scroll-mt-24 border-b border-ink pb-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="label text-ink">{title}</h3>
        <span className={clsx("tnum font-serif text-[30px] leading-none tracking-[-0.02em]", count > 0 ? "text-ink" : "text-ink-3")}>
          {count.toLocaleString("en-US")}
        </span>
      </div>
      {note && <p className="mt-1 text-[12px] leading-snug text-ink-3">{note}</p>}
    </div>
  );
}

function listedNote(section: ChangeSection, by: string): string | null {
  const st = sectionState(section.count, section.items);
  if (st === "truncated") return `Top ${section.items.length} listed, ${by}.`;
  return null;
}

/** One "new to Radar" block. */
export function NewSection({ id, title, section, by, empty, note }: { id: string; title: string; section: ChangeSection; by: string; empty: string; note?: string }) {
  const listed = listedNote(section, by);
  return (
    <div className="min-w-0">
      <CountHead id={id} title={title} count={section.count} note={[note, listed].filter(Boolean).join(" ") || undefined} />
      {section.count === 0 ? (
        <Quiet>{empty}</Quiet>
      ) : (
        <Folded items={section.items} keyOf={(x) => x.id} noun="listed" render={(x) => <ItemRow item={x} />} />
      )}
    </div>
  );
}

// ── Signals by family ────────────────────────────────────────────────────────

export function SignalsByFamily({ total, families }: { total: number; families: FamilySignals[] }) {
  if (total === 0 || families.length === 0) return <Quiet>No new signals were recorded between the two captures.</Quiet>;
  const max = Math.max(...families.map((f) => f.count), 1);
  return (
    <div className="grid gap-x-10 gap-y-8 md:grid-cols-2">
      {families.map((f) => (
        <div key={f.family} className="min-w-0">
          <div className="flex items-baseline justify-between gap-3 border-b border-ink pb-2">
            <h3 className="label text-ink">{f.label}</h3>
            <span className="tnum font-serif text-[26px] leading-none text-ink">{f.count.toLocaleString("en-US")}</span>
          </div>
          <div aria-hidden className="mt-1.5 h-[3px] bg-rule">
            <div className="h-full bg-ink" style={{ width: `${(f.count / max) * 100}%` }} />
          </div>
          {f.items.length > 0 && (
            <ol className="mt-1 divide-y divide-rule">
              {f.items.slice(0, 4).map((s) => {
                const parentHref = refHref(s.parent);
                return (
                  <li key={s.id} className="py-1.5 text-[12.5px] leading-snug">
                    <ItemName item={s} className="text-ink" />
                    <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{dayLabel(s.created_at) ?? "undated"}</span>
                    {s.parent && parentHref && (
                      <Link href={parentHref} className="link ml-2 text-[11.5px] text-ink-2">
                        {s.parent.name}
                      </Link>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
          {f.count > Math.min(4, f.items.length) && (
            <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">Newest {Math.min(4, f.items.length)} of {f.count.toLocaleString("en-US")} shown</p>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Score changes ────────────────────────────────────────────────────────────

function ScoreRow({ c, focus, diligence }: { c: ScoreChange; focus?: string; diligence?: ReadonlySet<string> }) {
  const href = refHref({ type: c.entity_type, slug: c.slug, id: c.id });
  const comps = topComponents(c);
  const up = c.delta > 0;
  const workspace = c.entity_type === "company" && c.slug && diligence?.has(c.slug) ? `/diligence/${encodeURIComponent(c.slug)}` : null;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-2.5" data-demo="score-row" data-demo-slug={c.slug ?? undefined}>
      <div className="min-w-0">
        {href ? (
          <Link href={href} className="font-serif text-[16.5px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 hover:underline">
            {c.name}
          </Link>
        ) : (
          <span className="font-serif text-[16.5px] leading-tight text-ink">{c.name}</span>
        )}
        {workspace && (
          <Link
            href={workspace}
            data-demo="open-diligence"
            className="ml-2.5 font-mono text-[9.5px] uppercase tracking-[0.08em] text-accent hover:underline"
            title="A technical diligence workspace exists for this company"
          >
            Open diligence →
          </Link>
        )}
        {comps.length > 0 && (
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10.5px] text-ink-2">
            {comps.map((k) => (
              <span key={k.key} className={clsx("tnum whitespace-nowrap", k.key === focus && "text-ink")}>
                {k.label} <span className="text-ink">{k.text}</span>
              </span>
            ))}
          </p>
        )}
      </div>
      <div className="text-right">
        <span className={clsx("tnum block font-mono text-[15px] leading-none", up ? "text-accent" : "text-ink")}>
          <span aria-hidden className="mr-1 text-[12px]">{up ? "↑" : c.delta < 0 ? "↓" : "→"}</span>
          {signed(c.delta)}
        </span>
        <span className="tnum mt-1 block font-mono text-[10.5px] text-ink-3">
          {num(c.base_score)} → {num(c.current_score)}
        </span>
      </div>
    </div>
  );
}

function ScoreColumn({
  id,
  title,
  list,
  tone,
  empty,
  focus,
  diligence,
}: {
  id: string;
  title: string;
  list: ScoreChangeList;
  tone: "up" | "down";
  empty: string;
  focus?: string;
  diligence?: ReadonlySet<string>;
}) {
  return (
    <div className="min-w-0">
      <div id={id} className={clsx("flex items-baseline justify-between gap-3 border-b-2 pb-2", tone === "up" ? "border-accent" : "border-ink")}>
        <h3 className={clsx("label", tone === "up" ? "text-accent" : "text-ink")}>
          <span aria-hidden className="mr-1.5">{tone === "up" ? "↑" : "↓"}</span>
          {title}
        </h3>
        <span className={clsx("tnum font-serif text-[30px] leading-none", tone === "up" ? "text-accent" : "text-ink")}>{list.count}</span>
      </div>
      {list.count === 0 ? (
        <Quiet>{empty}</Quiet>
      ) : (
        <>
          <Folded items={list.items} keyOf={(x) => x.id} noun="listed" render={(x) => <ScoreRow c={x} focus={focus} diligence={diligence} />} />
          {list.items.length < list.count && <p className="label mt-1 text-ink-3">Top {list.items.length} of {list.count} listed</p>}
        </>
      )}
    </div>
  );
}

/** `diligence`: company slugs with a diligence workspace (their rows get an "Open diligence" link). */
export function ScoreChangesBlock({ report, diligence }: { report: ChangesReport; diligence?: ReadonlySet<string> }) {
  const s = report.score_changes;
  const base = dayLabel(report.base_day) ?? "the base day";
  const facts: [string, ReactNode][] = [
    ["Compared", s.compared],
    [`Moved under ±${num(s.threshold)}`, s.unchanged],
    ["Not re-observed", s.not_reobserved],
    ["Not comparable", s.not_comparable],
    [`No score on ${base}`, s.no_baseline],
  ];
  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-8 gap-y-3 border-b border-rule pb-4 sm:grid-cols-3 lg:grid-cols-5">
        {facts.map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="label">{k}</dt>
            <dd className="tnum mt-1 font-serif text-[24px] leading-none text-ink">{typeof v === "number" ? v.toLocaleString("en-US") : v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 max-w-[96ch] text-[12.5px] leading-relaxed text-ink-2">
        {s.comparable ? (
          <>
            Live Anti Scores of version <span className="font-mono text-[11.5px] text-ink">{s.current_version ?? "—"}</span> on both days, for companies
            re-observed in between. A move of at least {num(s.threshold)} points counts. Scores are cohort-relative percentiles, so part of any move
            comes from who else is in the cohort.
          </>
        ) : (
          <span className="text-ink">Scores are not compared: {s.reason ? humanizeDays(s.reason) : "no comparable live scores on both days."}</span>
        )}
      </p>
      {s.comparable && (
        <div className="mt-6 grid gap-x-12 gap-y-10 lg:grid-cols-2">
          <ScoreColumn id="accelerated" title="Accelerated" list={s.accelerated} tone="up" empty="No compared company rose by the threshold or more." diligence={diligence} />
          <ScoreColumn id="weakened" title="Weakened" list={s.weakened} tone="down" empty="No compared company fell by the threshold or more." diligence={diligence} />
        </div>
      )}
    </div>
  );
}

export function MomentumBlock({ list }: { list: ScoreChangeList }) {
  if (list.count === 0) return <Quiet>No compared company shows rising technical momentum or acceleration.</Quiet>;
  return (
    <div>
      <p className="mb-2 max-w-[96ch] text-[12.5px] leading-relaxed text-ink-2">
        Compared companies whose technical-momentum component or acceleration percentile rose materially — whatever their total score did.
        Ordered by the momentum component.
      </p>
      <div className="grid gap-x-12 lg:grid-cols-2">
        {[list.items.slice(0, Math.ceil(list.items.length / 2)), list.items.slice(Math.ceil(list.items.length / 2))].map((col, i) => (
          <ol key={i} className="divide-y divide-rule border-t border-ink">
            {col.map((c) => (
              <li key={c.id}>
                <ScoreRow c={c} focus="technical_momentum" />
              </li>
            ))}
          </ol>
        ))}
      </div>
      {list.items.length < list.count && <p className="label mt-1 text-ink-3">Top {list.items.length} of {list.count} listed</p>}
    </div>
  );
}

// ── Collisions ───────────────────────────────────────────────────────────────

function CollisionRow({ c }: { c: CollisionChange }) {
  const href = refHref({ type: c.entity_type, slug: c.slug, id: c.id });
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-2.5">
      <div className="min-w-0">
        {href ? (
          <Link href={href} className="font-serif text-[16.5px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 hover:underline">
            {c.name}
          </Link>
        ) : (
          <span className="font-serif text-[16.5px] text-ink">{c.name}</span>
        )}
        <p className="mt-0.5 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
          {familyLabels(c.families).join(" · ") || c.families.join(" · ") || "No families"}
          {c.new_entity && <span className="ml-2 text-ink-3">· new to Radar</span>}
        </p>
      </div>
      <span className="tnum pt-[3px] text-right font-mono text-[11px] uppercase tracking-[0.06em] text-ink">
        {c.base_strength ?? "none"} → {c.current_strength ?? "none"}
      </span>
    </li>
  );
}

export function CollisionsBlock({ report }: { report: ChangesReport }) {
  const cols = [
    { key: "new", title: "New collisions", data: report.new_collisions, empty: "No company newly reached a multi-family collision." },
    { key: "ended", title: "Collisions ended", data: report.collisions_ended, empty: "No collision present on the earlier day has ended." },
  ];
  return (
    <div className="grid gap-x-12 gap-y-8 lg:grid-cols-2">
      {cols.map((col) => (
        <div key={col.key} className="min-w-0">
          <div className="flex items-baseline justify-between gap-3 border-b border-ink pb-2">
            <h3 className="label text-ink">{col.title}</h3>
            <span className={clsx("tnum font-serif text-[26px] leading-none", col.data.count > 0 ? "text-ink" : "text-ink-3")}>{col.data.count}</span>
          </div>
          {col.data.count === 0 ? (
            <Quiet>{col.empty}</Quiet>
          ) : (
            <ol className="divide-y divide-rule">
              {col.data.items.map((c) => (
                <CollisionRow key={c.id} c={c} />
              ))}
            </ol>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Formation ────────────────────────────────────────────────────────────────

const STATUS_ORDER = ["NEW", "STRENGTHENED", "WEAKENED", "DROPPED", "UNCHANGED"] as const;

function FormationRow({ fc }: { fc: FormationChange }) {
  const href = formationChangeHref(fc);
  const scores =
    fc.status === "NEW" ? `${num(fc.current_score)}` : fc.status === "DROPPED" ? `was ${num(fc.base_score)}` : `${num(fc.base_score)} → ${num(fc.current_score)}`;
  const tiers =
    fc.base_tier && fc.current_tier && fc.base_tier !== fc.current_tier
      ? `${fc.base_tier} → ${fc.current_tier}`
      : (fc.current_tier ?? fc.base_tier ?? "");
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <ChangeBadge status={fc.status} className="translate-y-[-2px]" />
          {href ? (
            <Link href={href} className="font-serif text-[17px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 hover:underline">
              {fc.title}
            </Link>
          ) : (
            <span className="font-serif text-[17px] leading-tight text-ink">{fc.title}</span>
          )}
        </span>
        <span className="tnum font-mono text-[11.5px] text-ink">
          {scores}
          {tiers && <span className="ml-2 uppercase tracking-[0.06em] text-ink-3">{tiers}</span>}
        </span>
      </div>
      {fc.status === "DROPPED" && (
        <p className="mt-1.5 border-l-2 border-ink pl-3 text-[13px] leading-snug text-ink">
          <span className="label mr-2 text-ink">Why dropped</span>
          {humanizeDays(fc.reason ?? "No longer qualifies as a formation candidate.")}
        </p>
      )}
    </li>
  );
}

export function FormationBlock({ report }: { report: ChangesReport }) {
  const f = report.formation;
  if (!f.available) return <Quiet>{f.reason ? humanizeDays(f.reason) : "Formation history is not stored for both days."}</Quiet>;
  const lists: Record<string, FormationChange[]> = { NEW: f.new, STRENGTHENED: f.strengthened, WEAKENED: f.weakened, DROPPED: f.dropped };
  const rows = STATUS_ORDER.flatMap((st) => lists[st] ?? []);
  return (
    <div>
      <dl className="flex flex-wrap gap-x-8 gap-y-3 border-b border-rule pb-4">
        {STATUS_ORDER.map((st) => (
          <div key={st} className="min-w-0">
            <dt>
              <ChangeBadge status={st} />
            </dt>
            <dd className={clsx("tnum mt-1.5 font-serif text-[28px] leading-none", (f.counts[st] ?? 0) > 0 ? "text-ink" : "text-ink-3")}>{f.counts[st] ?? 0}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-[12.5px] leading-relaxed text-ink-2">
        Formation history of {dayLabel(f.base_day, { year: true }) ?? "—"} vs {dayLabel(f.current_day, { year: true }) ?? "—"}. A tier change decides; within a
        tier a move of at least {f.threshold ?? 5} points does. Dropped candidates no longer have a card — the reason they stopped qualifying is shown.
        {(f.counts.UNCHANGED ?? 0) > 0 && <> Unchanged candidates are not listed; they carry an Unchanged badge on the formation radar.</>}
      </p>
      {rows.length > 0 ? (
        <ol className="mt-2 divide-y divide-rule border-t border-ink">
          {rows.map((fc) => (
            <FormationRow key={`${fc.status}-${fc.anchor_id}`} fc={fc} />
          ))}
        </ol>
      ) : (
        <Quiet>No formation candidate appeared, moved or dropped.</Quiet>
      )}
      <p className="mt-3">
        <Link href="/formation" className="label text-ink hover:text-accent">
          Formation radar →
        </Link>
      </p>
    </div>
  );
}

// ── Who to meet ──────────────────────────────────────────────────────────────

function MeetList({ title, rows, empty, tone }: { title: string; rows: MeetMove[]; empty: string; tone?: "down" }) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-3 border-b border-ink pb-2">
        <h3 className="label text-ink">{title}</h3>
        <span className={clsx("tnum font-serif text-[24px] leading-none", rows.length > 0 ? "text-ink" : "text-ink-3")}>{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <Quiet>{empty}</Quiet>
      ) : (
        <ol className="divide-y divide-rule">
          {rows.map((m) => {
            const href = refHref(m.person);
            return (
              <li key={m.person.id} className="flex items-baseline justify-between gap-3 py-2">
                {href ? (
                  <Link href={href} className="truncate font-serif text-[15.5px] text-ink decoration-accent decoration-1 underline-offset-4 hover:underline">
                    {m.person.name}
                  </Link>
                ) : (
                  <span className="truncate font-serif text-[15.5px] text-ink">{m.person.name}</span>
                )}
                <span className={clsx("tnum shrink-0 font-mono text-[11px]", tone === "down" ? "text-ink" : "text-ink-2")}>
                  {meetRankText(m)}
                  {m.change !== null && m.change !== 0 && (
                    <span className={clsx("ml-2", m.change > 0 ? "text-accent" : "text-ink")}>{m.change > 0 ? `↑ ${m.change}` : `↓ ${Math.abs(m.change)}`}</span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

export function MeetBlock({ report }: { report: ChangesReport }) {
  const m = report.meet;
  if (!m.available) return <Quiet>{m.reason ? humanizeDays(m.reason) : "No Who to meet list stored for both weeks."}</Quiet>;
  return (
    <div>
      <p className="mb-4 text-[12.5px] leading-relaxed text-ink-2">
        The weekly list of <span className="font-mono text-[11.5px] text-ink">{m.base_week}</span> vs{" "}
        <span className="font-mono text-[11.5px] text-ink">{m.current_week}</span>.{" "}
        <Link href="/meet" className="link text-ink">
          This week’s list
        </Link>
      </p>
      <div className="grid gap-x-10 gap-y-8 md:grid-cols-3">
        <MeetList title="Entered" rows={m.entered} empty="Nobody new on the list." />
        <MeetList title="Left" rows={m.left} empty="Nobody left the list." tone="down" />
        <MeetList title="Moved" rows={m.moved} empty="No rank changes." />
      </div>
    </div>
  );
}

// ── Caveats ──────────────────────────────────────────────────────────────────

export function Caveats({ caveats, id }: { caveats: string[]; id: string }) {
  if (caveats.length === 0) return null;
  return (
    <aside id={id} aria-labelledby={`${id}-h`} className="scroll-mt-24 border-y-2 border-ink bg-panel/60 px-5 py-4">
      <h2 id={`${id}-h`} className="label text-ink">
        <span aria-hidden className="mr-2 inline-block size-[6px] translate-y-[-1px] bg-accent" />
        Read before the numbers
      </h2>
      <ol className="mt-2.5 grid gap-x-10 gap-y-2 md:grid-cols-2">
        {caveats.map((c, i) => (
          <li key={i} className="grid grid-cols-[22px_minmax(0,1fr)] text-[13px] leading-snug text-ink">
            <span className="tnum font-mono text-[10.5px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
            <span>{humanizeDays(c)}</span>
          </li>
        ))}
      </ol>
    </aside>
  );
}
