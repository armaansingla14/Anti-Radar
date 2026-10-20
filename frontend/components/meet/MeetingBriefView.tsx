import clsx from "clsx";
import { VerificationBadge } from "@/components/Badges";
import { ClaimLine, toClaims } from "@/components/entity/ReportBody";
import type { JsonObject } from "@/lib/types";
import { MEETING_BRIEF_SECTIONS, toStrings, type MeetingSectionDef } from "./logic";

/** Sections present in a meeting-brief body, in reading order (unknowns only when non-empty). */
export function meetingSections(body: JsonObject): MeetingSectionDef[] {
  return MEETING_BRIEF_SECTIONS.filter((d) => d.key !== "unknowns" || toStrings(body.unknowns).length > 0);
}

function Empty({ text }: { text: string }) {
  return (
    <p className="mt-2 flex items-center gap-2 text-[13.5px] text-ink-3">
      <VerificationBadge value="UNKNOWN" />
      {text} Not a negative finding.
    </p>
  );
}

/**
 * MEETING BRIEF body: 30-second context → why on radar → what they built →
 * technical background → what changed → why Anti Fund → 5 questions → things
 * to verify. Claims carry [S#] citations and FACT / INFERENCE / MODEL OPINION
 * markers; questions are numbered; nothing is invented to fill a gap.
 */
export function MeetingBriefView({ body, citePrefix }: { body: JsonObject; citePrefix?: string }) {
  const defs = meetingSections(body);
  return (
    <div className="min-w-0">
      {defs.map((def, i) => {
        const id = `${citePrefix ?? "r"}-sec-${def.key}`;
        const lead = def.key === "context_30s";
        const head = (count: number) => (
          <h3 className="flex items-baseline gap-3">
            <span className="tnum font-mono text-[10.5px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
            <span className="label text-[11px] text-ink">{def.title}</span>
            {count > 1 && <span className="label ml-auto tnum text-ink-3">{count}</span>}
          </h3>
        );
        if (def.type === "claims") {
          const claims = toClaims(body[def.key]) ?? [];
          return (
            <section key={def.key} id={id} className={clsx("scroll-mt-24 border-t pb-7 pt-3", lead ? "border-accent" : "border-ink")}>
              {head(claims.length)}
              {claims.length === 0 ? (
                <Empty text={def.empty} />
              ) : (
                <ul className={clsx("mt-1 max-w-[76ch] divide-y divide-rule/70", lead && "[&>li]:font-serif [&>li]:text-[18px] [&>li]:leading-[1.5]")}>
                  {claims.map((c, j) => (
                    <ClaimLine key={j} claim={c} citePrefix={citePrefix} />
                  ))}
                </ul>
              )}
            </section>
          );
        }
        const items = toStrings(body[def.key]);
        const questions = def.key === "questions";
        return (
          <section
            key={def.key}
            id={id}
            className={clsx("scroll-mt-24 border-t pb-7 pt-3", questions ? "border-t-[3px] border-double border-ink" : "border-ink")}
          >
            {head(items.length)}
            {items.length === 0 ? (
              <Empty text={def.empty} />
            ) : (
              <ol className="mt-1 max-w-[76ch] divide-y divide-rule/70">
                {items.map((t, j) => (
                  <li
                    key={j}
                    className={clsx(
                      "grid gap-x-1 py-2.5",
                      questions ? "grid-cols-[34px_1fr] font-serif text-[18px] leading-[1.45] text-ink" : "grid-cols-[22px_1fr] text-[14.5px] leading-[1.6] text-ink",
                    )}
                  >
                    <span aria-hidden className={clsx("tnum font-mono", questions ? "pt-[5px] text-[12px] text-accent" : "pt-[3px] text-[10.5px] text-ink-3")}>
                      {questions ? `${j + 1}.` : def.key === "things_to_verify" ? "□" : "—"}
                    </span>
                    <span className="min-w-0">{t}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        );
      })}
    </div>
  );
}
