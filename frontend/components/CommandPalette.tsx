"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { search } from "@/lib/api";
import { hrefFor } from "@/lib/format";
import { fuzzyRank } from "@/lib/fuzzy";
import { ALL_NAV } from "@/lib/routes";
import type { SearchResult } from "@/lib/types";
import { useMounted } from "@/lib/useMounted";
import type { PaletteCommand } from "./KeyboardProvider";

export interface CommandPaletteProps {
  open: boolean;
  initialQuery?: string;
  onClose: () => void;
  /** Page-registered commands (via useCommand). */
  commands: PaletteCommand[];
  onShowHelp: () => void;
}

interface Item {
  id: string;
  group: "Search" | "Entities" | "Go to" | "Actions";
  label: string;
  hint?: string;
  detail?: string;
  run: () => void;
}

/** ⌘K palette: fuzzy route navigation, live entity lookup, search hand-off and actions. */
export function CommandPalette(props: CommandPaletteProps) {
  const mounted = useMounted();
  if (!mounted || !props.open) return null;
  return createPortal(<PaletteDialog {...props} />, document.body);
}

function PaletteDialog({ initialQuery = "", onClose, commands, onShowHelp }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState(initialQuery);
  const [cursor, setCursor] = useState(0);
  const [entities, setEntities] = useState<{ q: string; results: SearchResult[] } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
      opener?.focus?.();
    };
  }, []);

  const q = query.trim();
  useEffect(() => {
    if (q.length < 2) return;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => {
      search(q, { limit: 6 })
        .then((r) => !ctrl.signal.aborted && setEntities({ q, results: r.results.slice(0, 6) }))
        .catch(() => !ctrl.signal.aborted && setEntities({ q, results: [] }));
    }, 180);
    return () => {
      ctrl.abort();
      window.clearTimeout(t);
    };
  }, [q]);

  const items = useMemo<Item[]>(() => {
    const go = (href: string) => () => {
      onClose();
      router.push(href);
    };
    const out: Item[] = [];
    if (q) {
      out.push({
        id: "search",
        group: "Search",
        label: `Search for “${q}”`,
        hint: "↵",
        run: go(`/search?q=${encodeURIComponent(q)}`),
      });
    }
    if (entities && entities.q === q) {
      for (const r of entities.results) {
        out.push({
          id: `entity-${r.entity_type}-${r.id}`,
          group: "Entities",
          label: r.name,
          detail: [r.entity_type === "person" ? "Founder" : r.entity_type, r.subtitle].filter(Boolean).join(" · "),
          hint: r.score !== null ? String(r.score) : undefined,
          run: go(hrefFor(r.entity_type, r.slug)),
        });
      }
    }
    const nav = fuzzyRank(q, ALL_NAV, (n) => [n.label, n.description]);
    for (const n of nav) {
      out.push({
        id: `nav-${n.href}`,
        group: "Go to",
        label: n.label,
        detail: n.description,
        hint: n.jump ? `G ${n.jump.toUpperCase()}` : undefined,
        run: go(n.href),
      });
    }
    const actions: PaletteCommand[] = [
      ...commands,
      { id: "help", label: "Keyboard shortcuts", hint: "?", run: onShowHelp },
      {
        id: "refresh",
        label: "Refresh data on this page",
        keywords: "reload",
        run: () => {
          onClose();
          router.refresh();
        },
      },
      {
        id: "copy-link",
        label: "Copy link to this page",
        keywords: "share url",
        run: () => {
          void navigator.clipboard?.writeText(window.location.href);
          onClose();
        },
      },
    ];
    for (const a of fuzzyRank(q, actions, (a) => [a.label, a.keywords ?? ""])) {
      out.push({
        id: `action-${a.id}`,
        group: "Actions",
        label: a.label,
        hint: a.hint,
        run: () => {
          a.run();
          if (!["help", "refresh", "copy-link"].includes(a.id)) onClose();
        },
      });
    }
    return out;
  }, [q, entities, commands, onClose, onShowHelp, router]);

  const active = Math.min(cursor, Math.max(0, items.length - 1));

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) {
      e.preventDefault();
      setCursor((active + 1) % Math.max(1, items.length));
    } else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) {
      e.preventDefault();
      setCursor((active - 1 + items.length) % Math.max(1, items.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      items[active]?.run();
    }
  };

  return (
    <div className="fixed inset-0 z-[65]" onKeyDown={onKey}>
      <div className="absolute inset-0 animate-fade-in bg-ink/25" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="absolute left-1/2 top-[14vh] w-[min(640px,calc(100vw-32px))] -translate-x-1/2 animate-rise border border-ink bg-paper"
      >
        <div className="flex items-center gap-3 border-b border-ink px-4">
          <span aria-hidden className="font-mono text-[12px] text-ink-3">⌘K</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setCursor(0);
            }}
            placeholder="Search founders, companies, or jump to…"
            className="h-14 w-full bg-transparent font-serif text-[20px] text-ink outline-none placeholder:text-ink-3"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={items[active] ? `${listId}-${active}` : undefined}
            aria-autocomplete="list"
            spellCheck={false}
          />
        </div>
        <ul ref={listRef} id={listId} role="listbox" className="max-h-[52vh] overflow-y-auto py-2">
          {items.length === 0 && <li className="px-4 py-6 text-center text-[13px] text-ink-3">No matches.</li>}
          {items.map((item, i) => {
            const header = i === 0 || items[i - 1].group !== item.group ? item.group : null;
            return (
              <li key={item.id} role="presentation">
                {header && <div className="label px-4 pb-1 pt-3 text-ink-3">{header}</div>}
                <div
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === active}
                  data-index={i}
                  onMouseMove={() => i !== active && setCursor(i)}
                  onClick={() => item.run()}
                  className={clsx(
                    "flex cursor-pointer items-baseline justify-between gap-4 border-l-2 px-4 py-2",
                    i === active ? "border-accent bg-panel" : "border-transparent",
                  )}
                >
                  <span className="min-w-0 truncate">
                    <span className="text-[14px] text-ink">{item.label}</span>
                    {item.detail && <span className="ml-3 text-[12px] text-ink-3">{item.detail}</span>}
                  </span>
                  {item.hint && <span className="tnum shrink-0 font-mono text-[10.5px] uppercase text-ink-2">{item.hint}</span>}
                </div>
              </li>
            );
          })}
        </ul>
        <div className="flex justify-between border-t border-rule px-4 py-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
          <span>↑↓ navigate · ↵ open · esc close</span>
          <span>Anti Radar</span>
        </div>
      </div>
    </div>
  );
}
