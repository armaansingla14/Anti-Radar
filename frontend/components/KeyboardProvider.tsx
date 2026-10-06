"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ALL_NAV } from "@/lib/routes";
import { CommandPalette } from "./CommandPalette";
import { Sheet } from "./Sheet";

export type CardAction = "open" | "watch" | "investigate" | "why";
export type CardHandler = (card: HTMLElement) => void;
export type CardHandlers = Partial<Record<CardAction, CardHandler>>;

export interface PaletteCommand {
  id: string;
  label: string;
  /** Right-aligned hint, e.g. a shortcut. */
  hint?: string;
  /** Extra words to match on. */
  keywords?: string;
  run: () => void;
}

export interface KeyboardApi {
  /** Register handlers for card actions; latest registration wins. Returns an unregister fn. */
  registerCardActions: (handlers: CardHandlers) => () => void;
  /** Add a page-level command to the palette. Returns an unregister fn. */
  registerCommand: (cmd: PaletteCommand) => () => void;
  commands: PaletteCommand[];
  openPalette: (query?: string) => void;
  openHelp: () => void;
  /** The card currently selected with J/K, if any. */
  activeCard: () => HTMLElement | null;
}

const KeyboardContext = createContext<KeyboardApi | null>(null);

export function useKeyboard(): KeyboardApi {
  const ctx = useContext(KeyboardContext);
  if (!ctx) throw new Error("useKeyboard must be used inside <KeyboardProvider>");
  return ctx;
}

/** Register card-action handlers for the lifetime of the calling component. */
export function useCardActions(handlers: CardHandlers): void {
  const { registerCardActions } = useKeyboard();
  const ref = useRef(handlers);
  useEffect(() => {
    ref.current = handlers;
  }, [handlers]);
  useEffect(
    () =>
      registerCardActions({
        open: (c) => (ref.current.open ? ref.current.open(c) : clickAction(c, "open")),
        watch: (c) => (ref.current.watch ? ref.current.watch(c) : clickAction(c, "watch")),
        investigate: (c) => (ref.current.investigate ? ref.current.investigate(c) : clickAction(c, "investigate")),
        why: (c) => (ref.current.why ? ref.current.why(c) : clickAction(c, "why")),
      }),
    [registerCardActions],
  );
}

/** Register a palette command while the calling component is mounted. */
export function useCommand(cmd: PaletteCommand): void {
  const { registerCommand } = useKeyboard();
  const ref = useRef(cmd);
  useEffect(() => {
    ref.current = cmd;
  }, [cmd]);
  const { id, label, hint, keywords } = cmd;
  useEffect(
    () => registerCommand({ id, label, hint, keywords, run: () => ref.current.run() }),
    [registerCommand, id, label, hint, keywords],
  );
}

function clickAction(card: HTMLElement, action: string): boolean {
  const el = card.querySelector<HTMLElement>(`[data-action="${action}"]`);
  if (!el) return false;
  el.click();
  return true;
}

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset", "range"].includes(type);
  }
  return false;
}

function visibleCards(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-card]")).filter(
    (el) => el.offsetParent !== null || el.getClientRects().length > 0,
  );
}

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["⌘", "K"], label: "Command palette" },
  { keys: ["/"], label: "Search" },
  { keys: ["J"], label: "Next card" },
  { keys: ["K"], label: "Previous card" },
  { keys: ["O"], label: "Open focused card (or Enter)" },
  { keys: ["W"], label: "Watch / unwatch focused card" },
  { keys: ["I"], label: "Investigate focused card" },
  { keys: ["Y"], label: "Why this score?" },
  { keys: ["Esc"], label: "Close / clear selection" },
  { keys: ["?"], label: "This help" },
];

const JUMPS = ALL_NAV.filter((n) => n.jump);

/**
 * Global keyboard layer: command palette (⌘K, Ctrl+K, /), J/K card navigation
 * over [data-card] elements, card actions (O/Enter, W, I, Y), G-jumps and a
 * shortcuts overlay. Keys are ignored while typing or when a dialog is open.
 */
export function KeyboardProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [commands, setCommands] = useState<PaletteCommand[]>([]);
  const handlerStack = useRef<CardHandlers[]>([]);
  const activeRef = useRef<HTMLElement | null>(null);
  const gPending = useRef<number | null>(null);

  const registerCardActions = useCallback((handlers: CardHandlers) => {
    handlerStack.current.push(handlers);
    return () => {
      handlerStack.current = handlerStack.current.filter((h) => h !== handlers);
    };
  }, []);

  const registerCommand = useCallback((cmd: PaletteCommand) => {
    setCommands((cs) => [...cs.filter((c) => c.id !== cmd.id), cmd]);
    return () => setCommands((cs) => cs.filter((c) => c !== cmd));
  }, []);

  const openPalette = useCallback((query = "") => {
    setPaletteQuery(query);
    setPaletteOpen(true);
  }, []);
  const openHelp = useCallback(() => setHelpOpen(true), []);

  const setActive = useCallback((card: HTMLElement | null) => {
    if (activeRef.current && activeRef.current !== card) activeRef.current.removeAttribute("data-kb-active");
    activeRef.current = card;
    if (!card) return;
    card.setAttribute("data-kb-active", "true");
    card.focus({ preventScroll: true });
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, []);

  const activeCard = useCallback((): HTMLElement | null => {
    const focused = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-card]");
    if (focused) return focused;
    return activeRef.current && document.contains(activeRef.current) ? activeRef.current : null;
  }, []);

  const move = useCallback(
    (dir: 1 | -1) => {
      const cards = visibleCards();
      if (cards.length === 0) return;
      const current = activeCard();
      let idx = current ? cards.indexOf(current) : -1;
      if (idx < 0) {
        // Start from the first card in view rather than the top of the page.
        const firstVisible = cards.findIndex((c) => c.getBoundingClientRect().bottom > 80);
        idx = firstVisible < 0 ? (dir > 0 ? -1 : cards.length) : dir > 0 ? firstVisible - 1 : firstVisible;
      }
      const next = Math.max(0, Math.min(cards.length - 1, idx + dir));
      setActive(cards[next]);
    },
    [activeCard, setActive],
  );

  const runCard = useCallback(
    (action: CardAction) => {
      const card = activeCard();
      if (!card) return;
      const handler = handlerStack.current[handlerStack.current.length - 1]?.[action];
      if (handler) return handler(card);
      if (action === "open") {
        const href = card.dataset.href;
        if (href) router.push(href);
        return;
      }
      clickAction(card, action);
    },
    [activeCard, router],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const key = e.key;
      if ((e.metaKey || e.ctrlKey) && key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        setPaletteQuery("");
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      if (document.querySelector('[aria-modal="true"]')) return;

      if (gPending.current !== null) {
        window.clearTimeout(gPending.current);
        gPending.current = null;
        const jump = JUMPS.find((n) => n.jump === key.toLowerCase());
        if (jump) {
          e.preventDefault();
          router.push(jump.href);
        }
        return;
      }

      switch (key) {
        case "/":
          e.preventDefault();
          openPalette();
          return;
        case "?":
          e.preventDefault();
          setHelpOpen(true);
          return;
        case "g":
        case "G":
          gPending.current = window.setTimeout(() => (gPending.current = null), 1200);
          return;
        case "j":
        case "J":
          e.preventDefault();
          move(1);
          return;
        case "k":
        case "K":
          e.preventDefault();
          move(-1);
          return;
        case "o":
        case "O":
          runCard("open");
          return;
        case "Enter":
          if ((e.target as HTMLElement | null)?.hasAttribute?.("data-card")) {
            e.preventDefault();
            runCard("open");
          }
          return;
        case "w":
        case "W":
          runCard("watch");
          return;
        case "i":
        case "I":
          runCard("investigate");
          return;
        case "y":
        case "Y":
          runCard("why");
          return;
        case "Escape":
          if (activeRef.current) {
            activeRef.current.blur();
            setActive(null);
          }
          return;
      }
    };
    const onFocusIn = (e: FocusEvent) => {
      const card = activeRef.current;
      if (card && !card.contains(e.target as Node)) {
        card.removeAttribute("data-kb-active");
        activeRef.current = null;
      }
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [move, openPalette, router, runCard, setActive]);

  const api = useMemo<KeyboardApi>(
    () => ({ registerCardActions, registerCommand, commands, openPalette, openHelp, activeCard }),
    [registerCardActions, registerCommand, commands, openPalette, openHelp, activeCard],
  );

  return (
    <KeyboardContext.Provider value={api}>
      {children}
      <CommandPalette
        open={paletteOpen}
        initialQuery={paletteQuery}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
        onShowHelp={() => {
          setPaletteOpen(false);
          setHelpOpen(true);
        }}
      />
      <Sheet open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard shortcuts" width={460}>
        <dl className="divide-y divide-rule border-y border-rule">
          {SHORTCUTS.map((s) => (
            <div key={s.label} className="flex items-center justify-between py-2">
              <dt className="text-[13px] text-ink">{s.label}</dt>
              <dd className="flex gap-1">
                {s.keys.map((k) => (
                  <kbd key={k} className="min-w-[22px] border border-rule-strong px-1.5 text-center font-mono text-[11px] text-ink">
                    {k}
                  </kbd>
                ))}
              </dd>
            </div>
          ))}
        </dl>
        <h3 className="label mb-2 mt-6 text-ink">Jump — press G then</h3>
        <dl className="divide-y divide-rule border-y border-rule">
          {JUMPS.map((n) => (
            <div key={n.href} className="flex items-center justify-between py-2">
              <dt className="text-[13px] text-ink">{n.label}</dt>
              <dd>
                <kbd className="border border-rule-strong px-1.5 font-mono text-[11px] uppercase text-ink">{n.jump}</kbd>
              </dd>
            </div>
          ))}
        </dl>
      </Sheet>
    </KeyboardContext.Provider>
  );
}
