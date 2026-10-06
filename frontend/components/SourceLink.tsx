import clsx from "clsx";
import type { ReactNode } from "react";
import { domainOf, isSafeUrl, sourceGlyph, sourceLabel } from "@/lib/format";

export interface SourceGlyphProps {
  sourceType: string | null | undefined;
  className?: string;
}

/** Two-letter mono source mark ("GH", "AX", "HN") in a hairline box. */
export function SourceGlyph({ sourceType, className }: SourceGlyphProps) {
  return (
    <span
      className={clsx(
        "inline-flex h-[16px] min-w-[22px] items-center justify-center border border-rule px-[3px] font-mono text-[9px] leading-none tracking-[0.04em] text-ink-2",
        className,
      )}
      title={sourceLabel(sourceType)}
      aria-label={sourceLabel(sourceType)}
    >
      {sourceGlyph(sourceType)}
    </span>
  );
}

export interface SourceLinkProps {
  url: string | null | undefined;
  /** Text to show; defaults to the URL's domain. */
  children?: ReactNode;
  className?: string;
}

/** External source link (http/https only) with ↗ and safe rel attributes. */
export function SourceLink({ url, children, className }: SourceLinkProps) {
  if (!isSafeUrl(url)) {
    return children ? <span className={className}>{children}</span> : null;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className={clsx("link font-mono text-[10.5px] text-ink-2", className)}
      title={url}
    >
      {children ?? domainOf(url)}
      <span aria-hidden className="ml-0.5">↗</span>
      <span className="sr-only"> (opens in new tab)</span>
    </a>
  );
}
