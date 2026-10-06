import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-[620px] py-16 text-center">
      <p className="label mb-4">Error 404</p>
      <h1 className="font-serif text-[44px] leading-tight tracking-[-0.01em]">Not on the radar.</h1>
      <p className="mx-auto mt-3 max-w-[46ch] text-[15px] leading-relaxed text-ink-2">
        We have no record at this address. The entity may have been merged, renamed, or never observed by any source.
      </p>
      <div className="mt-8 flex justify-center gap-6 font-mono text-[11px] uppercase tracking-[0.08em]">
        <Link href="/" className="link text-ink">
          ← Back to the radar
        </Link>
        <Link href="/search" className="link text-ink-2">
          Search
        </Link>
      </div>
    </div>
  );
}
