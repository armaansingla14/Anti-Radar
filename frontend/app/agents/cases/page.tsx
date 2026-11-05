import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { PageHeader } from "@/components/insight/PageHeader";
import { ResultChip } from "@/components/agents/Chips";
import { caseResult } from "@/components/agents/logic";
import { getAgentCases, getAgentFailures } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

export const metadata: Metadata = { title: "Regression cases · Agents" };

const th = "label whitespace-nowrap px-2 py-2 text-left font-normal";
const td = "px-2 py-3 align-top";

export default async function CasesPage() {
  const [cases, failures] = await Promise.all([getAgentCases(), getAgentFailures().catch(() => [])]);
  const failureFor = new Map(failures.filter((f) => f.regression_case).map((f) => [f.regression_case!, f]));
  const results = cases.map(caseResult);
  const pass = results.filter((r) => r === "PASS").length;
  const fail = results.filter((r) => r === "FAIL").length;

  return (
    <div className="pb-24">
      <PageHeader
        kicker={
          <>
            <Link href="/agents" className="link">
              Agents
            </Link>{" "}
            · Regression cases
          </>
        }
        title="Regression cases"
        dek="YAML cases run by `radar eval`: each pins a known failure so it cannot return silently. The latest result is shown exactly as recorded, with the provider and model it ran on."
        aside={
          <dl className="grid grid-cols-3 gap-x-5">
            {[
              ["Cases", cases.length],
              ["Pass", pass],
              ["Fail", fail],
            ].map(([k, v]) => (
              <div key={k} className={clsx("border-t pt-2", k === "Fail" && Number(v) > 0 ? "border-accent" : "border-ink")}>
                <dt className="label">{k}</dt>
                <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        }
      />
      <SectionLabel index={1} right={<span className="tnum">{cases.length}</span>}>
        Cases
      </SectionLabel>
      {cases.length === 0 ? (
        <EmptyState className="mt-6" title="No regression cases loaded." />
      ) : (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse border-t border-ink text-[12.5px]">
            <thead>
              <tr className="border-b border-ink">
                <th className={th} scope="col">Case</th>
                <th className={th} scope="col">Agent · task</th>
                <th className={th} scope="col">Failure category</th>
                <th className={th} scope="col">Latest result</th>
                <th className={th} scope="col">Run</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c, i) => {
                const f = failureFor.get(c.id);
                const r = results[i];
                return (
                  <tr key={c.id} id={`case-${c.id}`} className="scroll-mt-24 border-b border-rule target:bg-accent-wash">
                    <td className={clsx(td, "min-w-[300px]")}>
                      <p className="font-mono text-[11px] text-ink-2">{c.id}</p>
                      <p className="mt-0.5 text-[13.5px] leading-snug text-ink">{c.title}</p>
                      {(c.regression_for || f) && (
                        <p className="mt-0.5 text-[11.5px] text-ink-3">
                          Regression for {c.regression_for ?? ""}
                          {f && (
                            <Link href={`/agents/failures/${String(f.number).padStart(4, "0")}`} className="link ml-1 text-ink-2">
                              {f.display}
                            </Link>
                          )}
                        </p>
                      )}
                    </td>
                    <td className={clsx(td, "font-mono text-[10.5px] uppercase text-ink-2")}>
                      {c.agent}
                      <span className="block normal-case text-ink-3">{c.task}</span>
                    </td>
                    <td className={td}>
                      <Link href={`/agents#cat-${c.failure_category}`} className="font-mono text-[10.5px] uppercase text-ink hover:text-accent">
                        {c.failure_category.replace(/_/g, " ")}
                      </Link>
                    </td>
                    <td className={td}>
                      <ResultChip value={r} />
                      {c.latest && c.latest.failures.length > 0 && (
                        <ul className="mt-1 space-y-0.5 text-[11.5px] text-accent">
                          {c.latest.failures.slice(0, 3).map((x, j) => (
                            <li key={j}>{x}</li>
                          ))}
                        </ul>
                      )}
                      {c.latest?.error && <p className="mt-1 text-[11.5px] text-accent">{c.latest.error}</p>}
                    </td>
                    <td className={clsx(td, "whitespace-nowrap font-mono text-[10.5px] text-ink-2")}>
                      {c.latest ? (
                        <>
                          {fmtDateTime(c.latest.run_at)}
                          <span className="block text-ink-3">
                            {c.latest.provider}
                            {c.latest.model !== c.latest.provider ? ` / ${c.latest.model}` : ""}
                          </span>
                        </>
                      ) : (
                        <span className="text-ink-3">never run</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
