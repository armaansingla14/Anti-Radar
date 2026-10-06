/**
 * Fallback plans for the W / I / Y card shortcuts when the focused card has
 * no [data-action] control of its own (dense index rows, search results).
 * The card describes its entity through data attributes:
 *   data-href, data-entity-type ("company" | "person"), data-entity-id,
 *   data-watched ("true" | "false"; absent when unknown).
 * Pure so it can be unit-tested without a DOM.
 */
export type CardAction = "open" | "watch" | "investigate" | "why";

export interface CardData {
  href?: string;
  entityType?: string;
  entityId?: string;
  watched?: string;
}

export type CardPlan =
  | { kind: "navigate"; href: string }
  | { kind: "why"; entityType: "company" | "person"; idOrSlug: string }
  | { kind: "watch"; entityType: "company" | "person"; entityId: string; next: boolean }
  | { kind: "unavailable"; message: string };

const ENTITY_PATH = /^\/(companies|founders)\/([^/?#]+)\/?$/;

function entityFromHref(href: string | undefined): { entityType: "company" | "person"; slug: string } | null {
  const m = href ? ENTITY_PATH.exec(href) : null;
  if (!m) return null;
  return { entityType: m[1] === "companies" ? "company" : "person", slug: decodeURIComponent(m[2]) };
}

export function planCardAction(action: CardAction, d: CardData): CardPlan {
  const fromHref = entityFromHref(d.href);
  const entityType = d.entityType === "company" || d.entityType === "person" ? d.entityType : fromHref?.entityType;
  switch (action) {
    case "open":
      return d.href ? { kind: "navigate", href: d.href } : { kind: "unavailable", message: "Nothing to open on this card" };
    case "investigate":
      return fromHref
        ? { kind: "navigate", href: `${d.href}?investigate=1` }
        : { kind: "unavailable", message: "Investigate is available on companies and people only" };
    case "why":
      return entityType && (d.entityId || fromHref)
        ? { kind: "why", entityType, idOrSlug: d.entityId || fromHref!.slug }
        : { kind: "unavailable", message: "No score breakdown for this card" };
    case "watch":
      // Unknown state → add (the API is idempotent); only an explicit "true" removes.
      return entityType && d.entityId
        ? { kind: "watch", entityType, entityId: d.entityId, next: d.watched !== "true" }
        : { kind: "unavailable", message: "Watch is available on companies and people only" };
  }
}
