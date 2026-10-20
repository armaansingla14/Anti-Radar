import clsx from "clsx";
import { fmtDate } from "@/lib/format";
import type { FormationCard } from "@/lib/types";
import { familyWords } from "./logic";

/** "4 independent signals · GitHub · Research · Hiring · first detected OCT 2 · latest activity OCT 18". */
export function SignalStrip({ card, className }: { card: FormationCard; className?: string }) {
  const words = familyWords(card.families);
  const n = card.independent_signals;
  return (
    <p
      className={clsx(
        "flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3",
        className,
      )}
    >
      <span className="text-ink">
        <span className="tnum">{n}</span> independent {n === 1 ? "signal" : "signals"}
      </span>
      {words.length > 0 && (
        <>
          <span aria-hidden>·</span>
          <span className="text-ink-2">{words.join(" · ")}</span>
        </>
      )}
      {card.first_detected_at && (
        <>
          <span aria-hidden>·</span>
          <span className="whitespace-nowrap">
            First detected <span className="tnum text-ink-2">{fmtDate(card.first_detected_at)}</span>
          </span>
        </>
      )}
      {card.latest_activity_at && (
        <>
          <span aria-hidden>·</span>
          <span className="whitespace-nowrap">
            Latest activity <span className="tnum text-ink-2">{fmtDate(card.latest_activity_at)}</span>
          </span>
        </>
      )}
    </p>
  );
}
