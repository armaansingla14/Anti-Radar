import clsx from "clsx";
import Link from "next/link";
import { entityGlyph, entityHref, type LooseEntityLink } from "./prose";

export interface EntityLinksProps {
  entities: LooseEntityLink[];
  /** Cap the visible links; the remainder is summarised as "+N". */
  limit?: number;
  className?: string;
}

/** Inline mono list of linked entities: "CO Halcyon Dynamics · FN Mira Okafor". */
export function EntityLinks({ entities, limit = 6, className }: EntityLinksProps) {
  if (entities.length === 0) return null;
  const shown = entities.slice(0, limit);
  return (
    <ul className={clsx("flex flex-wrap items-baseline gap-x-3 gap-y-1", className)}>
      {shown.map((e, i) => {
        const { href, external } = entityHref(e);
        const body = (
          <>
            <span aria-hidden className="mr-1 text-[9px] text-ink-3">
              {entityGlyph(e.type)}
            </span>
            <span className="link text-ink">{e.name}</span>
            {e.is_demo && (
              <span className="ml-1 text-[9px] text-ink-3" title="Fictional demo entity">
                DEMO
              </span>
            )}
            {external && (
              <span aria-hidden className="ml-0.5 text-ink-3">
                ↗
              </span>
            )}
          </>
        );
        const cls = "font-mono text-[11px] tracking-[0.01em] hover:text-accent";
        return (
          <li key={`${e.type}-${e.id ?? e.slug ?? e.name}-${i}`} className="min-w-0 max-w-full truncate">
            {external ? (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={cls}>
                {body}
              </a>
            ) : (
              <Link href={href} className={cls}>
                {body}
              </Link>
            )}
          </li>
        );
      })}
      {entities.length > limit && <li className="font-mono text-[10.5px] text-ink-3">+{entities.length - limit}</li>}
    </ul>
  );
}
