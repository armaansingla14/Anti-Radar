# Final application audit (Oct 21, 2026)

This was the pre-send pass before Anti Radar goes to Anti Fund in a cold email. It was not a feature cycle. The goal was reliability, trust, speed and clarity for a reviewer who gives it about 60 seconds.

## Issues found (before)

| # | Issue | Why it mattered |
|---|---|---|
| 1 | Every page was a per-request server render that fetched the Render backend (`no-store`). A sleeping free instance meant 30–60 s of "Waking the radar…", and a dead one meant "Signal lost" errors. | The first click could fail or stall. |
| 2 | The narrated video existed only on a laptop. There was no GitHub release, and the README linked to an empty `../../releases`. | The video link was dead. |
| 3 | Five visible labels read "Anti Fund · Internal" or "Not for distribution" (masthead, footer, demo title card, report and brief headers). The page metadata said "Anti Fund's private intelligence terminal". | It implied this was official Anti Fund software. |
| 4 | `demo/DEMO_SHOT_LIST.md` described an older recording (Tangency Labs, Yineng Zhang, "Kevin Zakka 51"). `docs/DEMO.md` and `docs/screenshots/` were Oct 6 leftovers built on fictional data. | The demo artifacts contradicted each other. |
| 5 | The home page said "collected today" for a snapshot. On any later day that would be false. | Stale information. |
| 6 | **Bug:** every attention-gap panel showed `gap_points`, the 0–10 score component, as the gap. ShengShu read "+7" instead of "+71". | A wrong number on screen. |
| 7 | In a read-only deployment, watch, feedback, dismiss, investigate and memo controls would fail with an error toast. | Visible broken interactions. |
| 8 | Overclaim risks: "Strong technical founding team." could appear with no founder relation; early person candidates had no "not verified" caveat; Meet had no note that GitHub activity dominates the evidence; the attention gap had no explanation of how it is computed. | Trust. |
| 9 | There was no OpenGraph or Twitter metadata and no social preview image. | Plain link previews in email or DMs. |
| 10 | Housekeeping: `e2e/package.json` was an `npm init` stub; `.gitignore` had duplicate entries; `e2e/screenshots.mjs` wrote fictional-data screenshots. | Unfinished feel. |

## Changes made

**The public site no longer depends on any backend** (`DATA_SOURCE=snapshot`).
- `frontend/snapshot/` holds the verified snapshot (Oct 20, 2026) as the backend's own API responses: 9,038 responses covering all 220 companies, 2,804 people, 6 formation candidates and 11 reports. They are packed into two files (`responses.pack` + index), so a lookup is one positioned read.
- The archive is recorded by `frontend/scripts/export-snapshot.mjs` from a backend running the verified snapshot. Nothing is synthesised.
- A server-only fetch hook, installed by `instrumentation.ts`, answers the existing choke points from the archive:
  - `request()` in `lib/api.ts`
  - `getJson()` in `lib/api-pages.ts`
  - `getSession()`
  - the browser proxy

  UI logic is not duplicated. Writes get a 403 `snapshot_read_only`. Search uses a small name index.
- `DATA_SOURCE=auto` keeps the live architecture: the live backend comes first, and the snapshot is the fallback, with a "Live intelligence unavailable · showing verified snapshot" notice.
- Displayed time is pinned to the snapshot. The date line shows Oct 20, and relative times are measured from the freeze moment, so nothing drifts or reads "in the future".
- Write controls render nothing in the snapshot, or open the stored report. Template meeting briefs (all five Meet people) and Anti Briefs (the home-feed companies) were generated deterministically from database facts before export, so primary buttons always lead somewhere.

**Branding:**
- "Built for Anti Fund" in the masthead, footer and demo title card.
- "Anti Radar · Research brief" and "Anti Radar · Morning brief" headers.
- The README says the project is independent and not affiliated with Anti Fund.
- New title, description, OpenGraph/Twitter metadata and a 1200×630 preview image.

**Claims:**
- The founding-team line now reads "Strong technical team linked to it" and no longer implies founders.
- Early candidates show "Strong technical trajectory · formation not yet verified".
- The Meet caveat now says that "people who work in the open are over-represented".
- The attention gap shows "How it's measured", and unmeasured sources are excluded rather than counted as zero.
- The gap bug (#6) is fixed, with a test.

**Video:**
- The narration was laid over a fresh recording of the snapshot build, recorded with the backend stopped.
- Silent page transitions were cut (`voiceover.py --tighten`).
- Result: 3:13, 1920×1080, −16 LUFS, captions burned in.
- Published at https://anti-radar-one.vercel.app/video and as an asset on the v1.0.0 release.

**Documents:**
- `DEMO_SCRIPT.md` and `DEMO_SHOT_LIST.md` were rewritten from the final cut's scene timings, with every number checked against the snapshot.
- Screenshots were regenerated from the same build.
- The README was rewritten for a 20-second read.
- `DEPLOYMENT.md` now explains how the site is served.
- The obsolete fictional demo document and its screenshots were deleted.

**Tooling and CI:**
- `scripts/verify_public_demo.mjs` is the pre-send smoke test.
- The CI frontend job now builds the snapshot site and smoke-tests it with **no backend running**.
- The deploy script uploads a single tgz; Vercel's free tier caps per-file uploads.

## Verification performed

| Check | Result |
|---|---|
| Backend tests (CI, real Postgres) | **939 passed** |
| Frontend `tsc`, eslint, `node --test` (CI) | **132 passed**; clean |
| Snapshot production build | passes; also built and smoke-tested in CI with no backend |
| Every page in the snapshot, local snapshot build with the API stopped | **3,058 pages, 0 errors**, 0 server render errors |
| `/demo` Playwright tour (functional pass) | passes |
| `scripts/verify_public_demo.mjs --insecure` against production | **59 passed, 0 failed**. Covers every critical route, the video on the site and on the release, every README link, the 14-step reviewer journey in a clean browser context (no cookies, no storage, Render asleep), 7 pages at phone width with no horizontal overflow, and 27 external source links. |
| Production speed with Render asleep | every route 0.1–0.8 s from a corporate network; first content 232 ms |
| Video | 3:12.6, H.264 1920×1080 at 30 fps, AAC stereo, −16.1 LUFS integrated, −4.4 dBFS peak (no clipping), voice from 1 s. Frames checked for all 10 scenes: no loading, error, localhost, DevTools or test overlays. |
| Branding, misspelling and secret sweep (tracked files and snapshot data) | no "Internal" or "Not for distribution", no "Anti-Rader", no secrets, no tracked `.env`. The one "localhost" in the data is a GitHub user's self-reported location. |
| CI on the final commit | green (backend + frontend) |

## QA questions from the brief

| Question | Answer |
|---|---|
| Does the demo work without Render? | Yes. Production answered every route while Render was asleep, and CI proves it with no backend. |
| Does the public app work from a cold browser? | Yes, from a clean Playwright context. |
| Does every README link work? | Yes. All are checked by the verifier. |
| Does the final demo video work? | Yes: it plays from `/video` (3:13) and the release asset. |
| Is the GitHub release real? | Yes: v1.0.0 with `anti-radar-demo.mp4`. |
| Does the demo script match the video? | Yes, scene by scene, with timestamps from the final cut. |
| Does the shot list match the video? | Yes. It was rewritten from the final cut. |
| Do screenshots match the snapshot? | Yes. They were regenerated from the same build. |
| Is "ANTI FUND · INTERNAL" gone? | Yes. |
| Are all claims verified, inferred or unknown? | Yes. Badges are on every fact; self-reported fields are labelled; unknowns stay unknown. |
| Do source links work? | Yes: 27/27 checked on the journey. |
| Does CI pass? | Yes. |
| Does the production build pass? | Yes. |
| Does `/demo` load quickly? | Yes: about 0.1 s, and the plan resolves without waking anything. |
| Any console errors? | None on the journey. |
| Any secrets? | None. |
| Any stale references to Anti-Rader? | None. |
| Can someone understand it without me? | The README leads with the pitch, the video and the live link, then the evaluation and limitations. |

## Remaining known limitations

- **The snapshot is fixed at Oct 20, 2026.** Velocities rest on short observed history, and most formation candidates are honest EARLY signals with no team candidates. This is documented in the evaluation.
- **X data and press coverage aren't measured.** There is no API access, so they are shown as unavailable.
- **No job postings matched any formation candidate**, so the hiring family is empty in this snapshot.
- **Snapshot search matches names, slugs and subtitles only.** The live backend's structured query parser needs the backend.
- **The snapshot is read-only.** Investigations, memos, feedback and watchlists run on a live backend (`deploy.py --live`).
- **Video hosting.** The video on the site is uploaded by the CLI deploy and not committed. A deploy from a fresh checkout would serve `/video` from the release asset instead, through the page's second `<source>`.
