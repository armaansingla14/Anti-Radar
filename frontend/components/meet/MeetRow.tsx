"use client";

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { DemoBadge } from "@/components/Badges";
import { WhyNow } from "@/components/formation/WhyNow";
import { useToast } from "@/components/Toast";
import { WatchButton } from "@/components/WatchButton";
import { postFeedback } from "@/lib/api";
import { pad2 } from "@/lib/format";
import type { MeetCard } from "@/lib/types";
import { affiliationParts, confidenceTone, formationHref, isChip, priorityTone, sortEvidence } from "./logic";
import { MeetingBriefButton } from "./MeetingBriefButton";

const chip = "inline-flex items-center gap-1 px-1.5 py-[1px] font-mono text-[9.5px] uppercase leading-[14px] tracking-[0.08em] whitespace-nowrap";

export function PriorityBadge({ value }: { value: string }) {
  const tone = priorityTone(value);
  return (
    <span
      className={clsx(chip, tone === "accent" ? "bg-accent text-paper" : tone === "ink" ? "border border-ink text-ink" : "border border-rule-strong text-ink-2")}
      title="Meeting priority this week"
    >
      {value} priority
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: string }) {
  const tone = confidenceTone(value);
  return (
    <span
      className={clsx(chip, tone === "ink" ? "border border-ink/70 text-ink" : tone === "muted" ? "border border-rule-strong text-ink-2" : "border border-dotted border-ink-3 text-ink-3")}
      title="How much independent, dated evidence supports this recommendation"
    >
      {value}
    </span>
  );
}

function Bullets({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-[13px] text-ink-3">{empty}</p>;
  return (
    <ul className="space-y-1.5">
      {items.map((t, i) => (
        <li key={i} className="grid grid-cols-[18px_1fr] text-[14px] leading-snug text-ink">
          <span aria-hidden className="text-ink-3">—</span>
          {t}
        </li>
      ))}
    </ul>
  );
}

const act = "inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.08em] transition-colors";

/**
 * One weekly recommendation, editorial: rank → who → WHY (person, now, change)
 * → thesis → actions. The founder score and evidence count sit to the side and
 * support the story rather than lead it. data-card for J/K/O/W.
 */
export function MeetRow({ card, readOnly, watched }: { card: MeetCard; readOnly: boolean; watched: boolean }) {
  const p = card.person;
  const toast = useToast();
  const href = `/founders/${encodeURIComponent(p.slug || p.id)}`;
  const aff = affiliationParts(card.affiliation_line);
  const chips = card.thesis_alignment.filter(isChip);
  const lines = card.thesis_alignment.filter((t) => !isChip(t));
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const dismiss = async () => {
    setBusy(true);
    try {
      await postFeedback({ entity_type: "person", entity_id: p.id, label: "dismiss", note: "meet" });
      setDismissed(true);
      toast(`${p.name} dismissed — won't be recommended again`);
    } catch {
      toast(readOnly ? "Read-only — unlock to dismiss" : "Could not dismiss", { tone: "error" });
      setBusy(false);
      setConfirm(false);
    }
  };

  if (dismissed) {
    return (
      <p className="border-t border-ink py-4 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-3" role="status">
        <span className="tnum mr-3 text-ink-2">{pad2(card.rank)}</span>
        {p.name} — dismissed. Hidden from future weeks and radar feeds.
      </p>
    );
  }

  return (
    <article
      data-card
      data-href={href}
      tabIndex={0}
      aria-labelledby={`meet-${p.id}`}
      className="grid animate-rise grid-cols-12 gap-x-8 gap-y-5 border-t border-ink pb-9 pt-5 focus:outline-none focus-visible:bg-accent-wash"
    >
      <div className="col-span-12 flex items-baseline gap-4 md:col-span-1 md:block">
        <span className={clsx("tnum font-serif text-[56px] leading-[0.85] tracking-[-0.03em]", card.rank === 1 ? "text-accent" : "text-ink")}>{pad2(card.rank)}</span>
      </div>

      <div className="col-span-12 min-w-0 md:col-span-8">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <PriorityBadge value={card.priority} />
          <ConfidenceBadge value={card.confidence} />
          {card.area && (
            <>
              <span aria-hidden className="mx-1 h-px w-4 bg-ink-3" />
              <span className="label text-ink-2">{card.area}</span>
            </>
          )}
          {p.is_demo && <DemoBadge />}
        </div>

        <h2 id={`meet-${p.id}`} className="mt-3 font-serif text-[36px] leading-[1.04] tracking-[-0.015em] text-ink max-md:text-[30px]">
          <Link href={href} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-[5px] hover:underline">
            {p.name}
          </Link>
        </h2>
        {aff && (
          <p className={clsx("mt-1.5 text-[14px] leading-snug", /unknown/i.test(aff.text) ? "text-ink-3" : "text-ink-2")}>
            {aff.text}
            {aff.note && (
              <span className="ml-2 whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3" title="From the person's own public profile — not verified">
                ({aff.note})
              </span>
            )}
          </p>
        )}

        <div className="mt-6 grid gap-x-10 gap-y-6 lg:grid-cols-2">
          <section aria-label="Why this person">
            <h3 className="label mb-2.5 text-ink">Why this person</h3>
            <Bullets items={card.why_person} empty="No person-level evidence summarised." />
          </section>
          <section aria-label="What changed">
            <h3 className="label mb-2.5 text-ink">What changed</h3>
            <Bullets items={card.what_changed} empty="No dated change recorded." />
          </section>
        </div>

        <section aria-label="Why now" className="mt-6">
          <h3 className="label mb-2.5 text-ink">Why now</h3>
          {card.why_now.length > 0 ? (
            <>
              <WhyNow items={sortEvidence(card.why_now).slice(0, 4)} demo={p.is_demo} />
              {card.why_now.length > 4 && (
                <Link href={href} className="label mt-2 inline-block pl-[88px] text-ink-3 hover:text-ink">
                  + {card.why_now.length - 4} more on the profile
                </Link>
              )}
            </>
          ) : (
            <p className="text-[13px] text-ink-3">No dated evidence this week.</p>
          )}
        </section>

        {card.thesis_alignment.length > 0 && (
          <section aria-label="Thesis alignment" className="mt-6 border-t border-rule pt-3">
            <h3 className="label mb-2 text-ink">Thesis alignment</h3>
            {chips.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {chips.map((t) => (
                  <span key={t} className="border border-rule-strong px-2 py-[2px] font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
                    {t}
                  </span>
                ))}
              </div>
            )}
            {lines.length > 0 && (
              <ul className="space-y-1">
                {lines.map((t) => (
                  <li key={t} className="grid grid-cols-[16px_1fr] text-[13px] leading-snug text-ink-2">
                    <span aria-hidden className="text-accent">◇</span>
                    {t}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>

      <aside className="col-span-12 flex flex-wrap gap-x-8 gap-y-4 border-rule md:col-span-3 md:flex-col md:border-l md:pl-6" aria-label="Supporting numbers">
        <div>
          <p className="label">Evidence</p>
          <p className="tnum mt-1 font-serif text-[30px] leading-none text-ink">{card.evidence_count}</p>
          <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">dated items</p>
        </div>
        <div>
          <p className="label">Founder score</p>
          <p className="tnum mt-1 font-serif text-[22px] leading-none text-ink-2">{p.founder_score === null ? "—" : Math.round(p.founder_score)}</p>
        </div>
        {card.formation && (
          <div className="min-w-0">
            <p className="label">Formation</p>
            <Link href={formationHref(card.formation)} className="mt-1 block text-[13px] leading-snug text-ink hover:text-accent">
              <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-accent">{card.formation.label}</span>
              <span className="tnum ml-2 font-mono text-[11px] text-ink-2">{Math.round(card.formation.score)}</span>
              <span className="mt-0.5 block underline decoration-rule-strong underline-offset-4">View formation report →</span>
            </Link>
          </div>
        )}
      </aside>

      <div className="col-span-12 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-rule pt-3 md:col-start-2 md:col-span-11">
        <Link href={href} data-action="open" className={clsx(act, "text-ink hover:text-accent")}>
          Open profile →
        </Link>
        <MeetingBriefButton personId={p.id} briefId={card.brief_report_id} readOnly={readOnly} tone={card.rank === 1 ? "solid" : "outline"} />
        <WatchButton entityType="person" entityId={p.id} watched={watched} />
        {confirm ? (
          <span className={clsx(act, "gap-3")}>
            <span className="text-ink-2">Hide {p.name} from future weeks?</span>
            <button type="button" onClick={() => void dismiss()} disabled={busy} className="text-accent hover:underline">
              {busy ? "Dismissing…" : "Confirm"}
            </button>
            <button type="button" onClick={() => setConfirm(false)} className="text-ink-2 hover:text-ink">
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirm(true)} className={clsx(act, "text-ink-3 hover:text-ink")}>
            Dismiss
          </button>
        )}
      </div>
    </article>
  );
}
