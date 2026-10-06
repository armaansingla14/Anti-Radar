# ANTI RADAR — 3-minute demo script

Setup: `make up` (or `make dev`), open http://localhost:3000. With `ANTHROPIC_API_KEY` set, Investigate runs live; without it, the pre-generated briefs/memos for Halcyon, Dexmal and ESPARGOS are shown and live runs produce clearly-labeled template briefs.

| # | Show | Say |
|---|------|-----|
| 1 | **Radar** (`/`) — the date header and the prose deck | "Instead of starting with companies that have already raised money, Radar starts with technical signals. This is what changed overnight." |
| 2 | Scroll the numbered cards; point at **Why it surfaced** and the right rail (Founders worth watching, **Strange Signals**) | "These are the companies and founders whose underlying signals changed most recently. The strange signals are patterns no single source shows — three people from the same aerospace company forming one GitHub org; a robotics repo with 1,700 new stars and not one Hacker News thread, which RADAR actually checked." |
| 3 | Open **Halcyon Dynamics** (DEMO) | "This company surfaced because four independent signals collided in under three weeks: a founder's bio change, a new GitHub org, a paper with code, and a first controls-engineering hire." Point at the **Signal Collision** strip and timeline. |
| 4 | Click **Why 77?** | "The ranking is explainable rather than an LLM saying 'this seems interesting'. Every component has its measured inputs and the evidence that produced it. No model sets this number." |
| 5 | **Attention Gap** panel | "Technical momentum at the 100th percentile, public attention far lower — and the panel shows which attention sources were actually available. Unknown is shown as unknown." |
| 6 | Click a founder (**Mira Okafor**), or `/founders` and the *Pre-company* toggle | "Radar also tracks people before they have companies — here, people who just left labs and are already shipping." |
| 7 | Back on a real company — **Dexmal** — click **Investigate** (or open its existing Anti Brief) | "RADAR ANALYST researches it live: GitHub, the website, arXiv, Hacker News. Every claim cites a source; facts, inferences and opinions are marked; and it argues against itself — 'this looks like a launch spike that's flattening'." Then **Generate Anti Memo** → Export Markdown. |
| 8 | **Thesis Map** (`/thesis`) | "Instead of only finding companies, we can see whole categories moving — here robotics manipulation and tactile sensing show momentum ahead of attention." |
| 9 | Back to the masthead | "Conviction before consensus." |

Extras if there's time: ⌘K palette, `J`/`K` to move between cards and `W` to watch; **Status** shows every source's health, rejects, rate limits and LLM spend; **Retrospective** applies the framework to vLLM, Ollama, openpi and others — explicitly *not* a backtest.

Honesty notes for the narration: Halcyon, Kestrel, Tessellate, Perihelion, Orbitform, Loomwork and their founders are fictional (badged DEMO DATA); Dexmal, TokenRhythm, ESPARGOS, ShengShu and the retrospective projects are real public data captured on Oct 6, 2026.
