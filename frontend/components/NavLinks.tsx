"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { isActive, NAV_PRIMARY, NAV_SECONDARY, type NavItem } from "@/lib/routes";
import { useKeyboard } from "./KeyboardProvider";

function NavLink({ item, quiet }: { item: NavItem; quiet?: boolean }) {
  const pathname = usePathname();
  const active = isActive(pathname, item.href);
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      title={item.description}
      className={clsx(
        "relative whitespace-nowrap py-3 font-mono text-[11px] uppercase tracking-[0.1em] transition-colors",
        active ? "text-ink" : quiet ? "text-ink-3 hover:text-ink" : "text-ink-2 hover:text-ink",
      )}
    >
      {item.label}
      <span
        aria-hidden
        className={clsx("absolute inset-x-0 bottom-[7px] h-px transition-opacity", active ? "bg-accent opacity-100" : "opacity-0")}
      />
    </Link>
  );
}

/** Primary + secondary navigation with the ⌘K affordance. */
export function NavLinks() {
  const { openPalette } = useKeyboard();
  return (
    <nav aria-label="Primary" className="flex items-center gap-6">
      <div className="-mx-1 flex min-w-0 flex-1 items-center gap-x-5 overflow-x-auto px-1 [scrollbar-width:none]">
        {NAV_PRIMARY.map((item) => (
          <NavLink key={item.href} item={item} />
        ))}
        <span aria-hidden className="mx-1 h-3 w-px shrink-0 bg-rule-strong" />
        {NAV_SECONDARY.map((item) => (
          <NavLink key={item.href} item={item} quiet />
        ))}
      </div>
      <button
        type="button"
        onClick={() => openPalette()}
        className="hidden shrink-0 items-center gap-3 border border-rule-strong px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:border-ink hover:text-ink sm:inline-flex"
        aria-label="Open command palette"
      >
        Jump to…
        <kbd className="font-mono text-[10.5px] text-ink-3">⌘K</kbd>
      </button>
    </nav>
  );
}
