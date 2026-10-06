"use client";

import clsx from "clsx";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, useTransition, type ReactNode } from "react";

export interface SelectOption {
  value: string;
  label: string;
  /** Optional <optgroup> label. */
  group?: string;
}

export interface FilterDef {
  key: string;
  label: string;
  options: SelectOption[];
  /** Value meaning "no filter" (omitted from the URL). */
  allValue?: string;
}

export interface ToggleDef {
  key: string;
  label: string;
  /** URL value when ON (omitted when the toggle is at its default). */
  onValue: string;
  defaultOn: boolean;
  /** URL value when OFF and defaultOn is true. */
  offValue?: string;
  hint?: string;
  accent?: boolean;
}

const fieldLabel = "label mb-1 block text-ink-3";
const selectCls =
  "h-[34px] w-full cursor-pointer appearance-none border-0 border-b border-ink bg-transparent pr-6 font-mono text-[11.5px] uppercase tracking-[0.04em] text-ink outline-none focus-visible:border-accent";

/**
 * URL-driven index controls: debounced search, selects and toggles. Every
 * change replaces the query string, so filtered views are linkable and the
 * server component re-renders with fresh data.
 */
export function IndexControls({
  placeholder,
  sort,
  filters,
  toggles,
  right,
}: {
  placeholder: string;
  sort: FilterDef;
  filters: FilterDef[];
  toggles: ToggleDef[];
  right?: ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();
  const [q, setQ] = useState(params.get("q") ?? "");
  const timer = useRef<number | null>(null);

  const push = (mut: (sp: URLSearchParams) => void) => {
    const sp = new URLSearchParams(params.toString());
    mut(sp);
    const qs = sp.toString();
    startTransition(() => router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false }));
  };

  const setParam = (key: string, value: string | null) =>
    push((sp) => {
      if (value === null || value === "") sp.delete(key);
      else sp.set(key, value);
    });

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  const onQuery = (v: string) => {
    setQ(v);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setParam("q", v.trim() || null), 280);
  };

  const selectFor = (f: FilterDef, current: string) => {
    const groups = Array.from(new Set(f.options.map((o) => o.group ?? "")));
    return (
      <label key={f.key} className="relative block min-w-0">
        <span className={fieldLabel}>{f.label}</span>
        <select
          className={selectCls}
          value={current}
          onChange={(e) => setParam(f.key, e.target.value === (f.allValue ?? "") ? null : e.target.value)}
        >
          {groups.map((g) =>
            g ? (
              <optgroup key={g} label={g}>
                {f.options.filter((o) => o.group === g).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </optgroup>
            ) : (
              f.options.filter((o) => !o.group).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))
            ),
          )}
        </select>
        <span aria-hidden className="pointer-events-none absolute bottom-[9px] right-1 text-[9px] text-ink-2">
          ▼
        </span>
      </label>
    );
  };

  const anyActive = params.toString().length > 0;

  return (
    <div className={clsx("transition-opacity duration-150", pending && "opacity-70")} aria-busy={pending}>
      <div className="grid items-end gap-x-6 gap-y-4 md:grid-cols-[minmax(220px,2fr)_repeat(auto-fit,minmax(130px,1fr))]">
        <label className="block min-w-0">
          <span className={fieldLabel}>Search</span>
          <span className="flex items-center border-b border-ink focus-within:border-accent">
            <span aria-hidden className="pr-2 font-mono text-[12px] text-ink-3">
              ⌕
            </span>
            <input
              type="search"
              value={q}
              onChange={(e) => onQuery(e.target.value)}
              placeholder={placeholder}
              className="h-[34px] w-full bg-transparent font-serif text-[17px] text-ink outline-none placeholder:text-ink-3"
              aria-label="Search"
            />
          </span>
        </label>
        {selectFor(sort, params.get(sort.key) ?? sort.allValue ?? sort.options[0].value)}
        {filters.map((f) => selectFor(f, params.get(f.key) ?? f.allValue ?? ""))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
        {toggles.map((t) => {
          const raw = params.get(t.key);
          const on = raw === null ? t.defaultOn : raw === t.onValue;
          return (
            <button
              key={t.key}
              type="button"
              role="switch"
              aria-checked={on}
              title={t.hint}
              onClick={() => {
                const next = !on;
                setParam(t.key, next === t.defaultOn ? null : next ? t.onValue : (t.offValue ?? null));
              }}
              className={clsx(
                "inline-flex items-center gap-2 font-mono text-[10.5px] uppercase tracking-[0.08em] transition-colors",
                on ? (t.accent ? "text-accent" : "text-ink") : "text-ink-2 hover:text-ink",
              )}
            >
              <span
                aria-hidden
                className={clsx(
                  "relative inline-block h-[12px] w-[22px] border transition-colors",
                  on ? (t.accent ? "border-accent bg-accent" : "border-ink bg-ink") : "border-ink-3",
                )}
              >
                <span
                  className={clsx(
                    "absolute top-[1px] h-[8px] w-[8px] transition-all duration-150",
                    on ? "left-[11px] bg-paper" : "left-[1px] bg-ink-3",
                  )}
                />
              </span>
              {t.label}
            </button>
          );
        })}
        {anyActive && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              startTransition(() => router.replace(pathname, { scroll: false }));
            }}
            className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 underline decoration-rule-strong underline-offset-4 hover:text-accent"
          >
            Reset filters
          </button>
        )}
        {pending && <span className="label text-ink-3">Updating…</span>}
        {right && <span className="ml-auto">{right}</span>}
      </div>
    </div>
  );
}
