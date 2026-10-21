import type { Metadata } from "next";
import Link from "next/link";
import { getSession } from "@/lib/session";
import UnlockForm from "./UnlockForm";

export const metadata: Metadata = { title: "Unlock", robots: { index: false, follow: false } };
export const dynamic = "force-dynamic";

export default async function UnlockPage() {
  const session = await getSession();
  const writesOpen = session.reachable && !session.read_only && !session.admin;
  return (
    <div className="mx-auto max-w-[520px] py-16">
      <p className="label mb-4">Operator access</p>
      <h1 className="font-serif text-[40px] leading-tight tracking-[-0.01em]">Unlock the radar.</h1>
      <p className="mt-3 max-w-[46ch] text-[15px] leading-relaxed text-ink-2">
        Reading is open to everyone. Adding sources, running investigations, writing memos and recording feedback
        need the operator token. It is kept in an http-only cookie on this browser and is never shown to scripts.
      </p>
      <div className="mt-10 border-t border-rule pt-8">
        {session.static_snapshot ? (
          <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            This deployment serves the verified snapshot and is read-only. Investigations, memos and feedback run on
            a live Anti Radar backend.
          </p>
        ) : !session.reachable ? (
          <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            Waking the radar… the backend is starting. You can still submit the token.
          </p>
        ) : writesOpen ? (
          <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            This deployment has no operator token configured — writes are already open.
          </p>
        ) : null}
        {!session.static_snapshot && <UnlockForm initiallyUnlocked={session.admin} />}
      </div>
      <div className="mt-10 font-mono text-[11px] uppercase tracking-[0.08em]">
        <Link href="/" className="link text-ink">
          ← Back to the radar
        </Link>
      </div>
    </div>
  );
}
