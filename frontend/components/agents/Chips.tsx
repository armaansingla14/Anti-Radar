import clsx from "clsx";

const chip = "inline-block whitespace-nowrap border px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.08em]";

export function SeverityChip({ value }: { value: string }) {
  const v = (value ?? "").toUpperCase();
  return <span className={clsx(chip, v === "HIGH" || v === "CRITICAL" ? "border-accent text-accent" : v === "MEDIUM" ? "border-ink text-ink" : "border-rule-strong text-ink-2")}>{v || "—"}</span>;
}

export function FailureStatusChip({ value }: { value: string }) {
  const v = (value ?? "").toUpperCase();
  const cls =
    v === "OPEN"
      ? "border-accent bg-accent text-paper"
      : v === "CONFIRMED"
        ? "border-accent text-accent"
        : v === "FIXED"
          ? "border-ink text-ink"
          : "border-dotted border-ink-3 text-ink-3";
  return <span className={clsx(chip, cls)}>{v || "—"}</span>;
}

export function ResultChip({ value }: { value: "PASS" | "FAIL" | "NOT RUN" }) {
  const cls = value === "PASS" ? "border-ink text-ink" : value === "FAIL" ? "border-accent bg-accent text-paper" : "border-dotted border-ink-3 text-ink-3";
  return <span className={clsx(chip, cls)}>{value}</span>;
}
