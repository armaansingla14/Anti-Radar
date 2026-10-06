import type { ReactNode } from "react";
import { externalHref, githubUrl, xUrl } from "@/lib/format";

/**
 * An external link that degrades to plain text when the href is unsafe or
 * points at a real host for a fictional (demo) entity.
 */
export function MaybeExternal({
  href,
  children,
  className,
  plain = false,
}: {
  href: string | null;
  children: ReactNode;
  className?: string;
  /** Skip the default `link` underline style (caller supplies its own). */
  plain?: boolean;
}) {
  if (!href) return plain ? <>{children}</> : <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={[plain ? null : "link", className].filter(Boolean).join(" ") || undefined}>
      {children}
    </a>
  );
}

export interface HandleLinksProps {
  github?: string | null;
  x?: string | null;
  site?: string | null;
  isDemo: boolean;
  /** Text renderers so callers keep their own label style. */
  format?: { github?: (h: string) => ReactNode; x?: (h: string) => ReactNode; site?: ReactNode };
}

/** GitHub / X / site handles. Demo entities never link to real github.com / x.com. */
export function HandleLinks({ github, x, site, isDemo, format = {} }: HandleLinksProps) {
  const siteHref = externalHref(site, isDemo);
  return (
    <>
      {github && <MaybeExternal href={githubUrl(github, isDemo)}>{format.github ? format.github(github) : `GH @${github}`}</MaybeExternal>}
      {x && <MaybeExternal href={xUrl(x, isDemo)}>{format.x ? format.x(x) : `X @${x}`}</MaybeExternal>}
      {siteHref && <MaybeExternal href={siteHref}>{format.site ?? "Site ↗"}</MaybeExternal>}
    </>
  );
}
