import Link from "next/link";

/** Inline notice for a filter value the API does not recognise. */
export function FilterNotice({ label, value, resetHref, detail }: { label: string; value: string; resetHref: string; detail?: string }) {
  return (
    <p role="status" className="mb-6 border-l-2 border-accent pl-3 text-[13px] leading-snug text-ink-2">
      Unknown {label} <span className="font-mono text-[12px] text-ink">“{value}”</span> — {detail ?? "it may have been renamed, so it was ignored."}{" "}
      <Link href={resetHref} className="link text-ink">
        Reset filters
      </Link>
    </p>
  );
}
