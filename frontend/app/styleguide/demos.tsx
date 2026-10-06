"use client";

import { useState } from "react";
import { FeedbackMenu } from "@/components/FeedbackMenu";
import { IntelCard } from "@/components/IntelCard";
import { useCardActions, useKeyboard } from "@/components/KeyboardProvider";
import { StreamLog } from "@/components/StreamLog";
import { useToast } from "@/components/Toast";
import { WatchButton } from "@/components/WatchButton";
import { WhyDrawer } from "@/components/WhyDrawer";
import type { InvestigateEvent } from "@/lib/types";
import { CARD, CARD_PERSON, SCORE, STREAM } from "./samples";

const btn =
  "border border-ink px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper";

/** Interactive gallery pieces; all callbacks are local so nothing hits the backend. */
export function CardDemos() {
  const toast = useToast();
  useCardActions({});
  return (
    <div>
      <IntelCard
        card={CARD}
        score={SCORE}
        onWatch={(c, next) => toast(`${next ? "Watching" : "Unwatched"} ${c.name} (sample — not saved)`)}
        onFeedback={(c, label) => toast(`Sample feedback “${label}” for ${c.name} (not saved)`)}
        onInvestigate={(c) => toast(`Would investigate ${c.name}`)}
      />
      <IntelCard
        card={CARD_PERSON}
        score={{ ...SCORE, total: 74, low_coverage: true, collision: null }}
        onWatch={() => undefined}
        onFeedback={() => undefined}
        onInvestigate={(c) => toast(`Would investigate ${c.name}`)}
      />
    </div>
  );
}

export function DrawerDemo() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={btn} onClick={() => setOpen(true)} aria-haspopup="dialog">
        Open “Why 87?”
      </button>
      <WhyDrawer open={open} onClose={() => setOpen(false)} score={SCORE} entityName="Example Robotics" />
    </>
  );
}

export function ControlsDemo() {
  const toast = useToast();
  const { openPalette, openHelp } = useKeyboard();
  return (
    <div className="flex flex-wrap items-center gap-6">
      <WatchButton entityType="company" entityId="sample" watched={false} onToggle={() => undefined} />
      <WatchButton entityType="company" entityId="sample" watched size="md" onToggle={() => undefined} />
      <FeedbackMenu onSelect={(l) => toast(`Selected ${l}`)} />
      <button type="button" className={btn} onClick={() => toast("Saved to watchlist")}>
        Toast
      </button>
      <button type="button" className={btn} onClick={() => toast("Backend unreachable", { tone: "error" })}>
        Error toast
      </button>
      <button type="button" className={btn} onClick={() => openPalette()}>
        Palette ⌘K
      </button>
      <button type="button" className={btn} onClick={openHelp}>
        Shortcuts ?
      </button>
    </div>
  );
}

export function StreamDemo() {
  const [events, setEvents] = useState<InvestigateEvent[]>(STREAM);
  const [running, setRunning] = useState(true);
  const finish = () => {
    setEvents((xs) => [
      ...xs,
      { type: "step", step: "arxiv", status: "ok", message: "No matching papers found — recorded as UNKNOWN, not absent" },
      { type: "done", step: "report", message: "Brief written with 6 cited sources", report_id: "sample" },
    ]);
    setRunning(false);
  };
  return (
    <div className="space-y-3">
      <StreamLog events={events} running={running} title="Investigation · Example Robotics" />
      <button
        type="button"
        className={btn}
        onClick={() => {
          if (running) finish();
          else {
            setEvents(STREAM);
            setRunning(true);
          }
        }}
      >
        {running ? "Complete run" : "Restart"}
      </button>
    </div>
  );
}
