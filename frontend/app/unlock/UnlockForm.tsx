"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const input =
  "w-full border-0 border-b border-rule-strong bg-transparent px-0 py-1.5 font-mono text-[13px] text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none";
const button =
  "border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-60";

const MESSAGES: Record<string, string> = {
  invalid_token: "That token was not accepted.",
  backend_waking: "The radar is waking up — try again in a few seconds.",
  backend_not_configured: "The backend is not configured for this deployment.",
  cross_site_write: "Request refused.",
  backend_error: "The backend did not recognise the unlock request (is it up to date?).",
};

export default function UnlockForm({ initiallyUnlocked }: { initiallyUnlocked: boolean }) {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [unlocked, setUnlocked] = useState(initiallyUnlocked);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function call(path: string, body?: unknown) {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string; admin?: boolean };
      if (!res.ok) {
        setError(MESSAGES[data.detail ?? ""] ?? "Something went wrong.");
        return;
      }
      setUnlocked(Boolean(data.admin));
      setToken("");
      router.refresh();
    } catch {
      setError(MESSAGES.backend_waking);
    } finally {
      setPending(false);
    }
  }

  if (unlocked) {
    return (
      <div className="flex items-center justify-between gap-6">
        <p className="font-serif text-[18px]">Unlocked on this browser.</p>
        <button type="button" className={button} disabled={pending} onClick={() => void call("/api/admin/lock")}>
          {pending ? "Locking…" : "Lock"}
        </button>
      </div>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!token.trim()) return setError("Enter the operator token.");
        void call("/api/admin/unlock", { token: token.trim() });
      }}
    >
      <label htmlFor="admin-token" className="label mb-1 block">
        Operator token
      </label>
      <input
        id="admin-token"
        type="password"
        className={input}
        value={token}
        onChange={(e) => setToken(e.target.value)}
        autoComplete="current-password"
        spellCheck={false}
        maxLength={512}
      />
      <div className="mt-6 flex items-center justify-between gap-6">
        <p role="status" aria-live="polite" className="text-[14px] text-accent">
          {error}
        </p>
        <button type="submit" className={button} disabled={pending} aria-busy={pending}>
          {pending ? "Checking…" : "Unlock"}
        </button>
      </div>
    </form>
  );
}
