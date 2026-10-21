# Ready to send

| | |
|---|---|
| **Public demo** | https://anti-radar-one.vercel.app |
| **Guided tour** | https://anti-radar-one.vercel.app/demo |
| **Video** (3:13, narrated, captioned) | https://anti-radar-one.vercel.app/video. Also on the release: https://github.com/armaansingla14/Anti-Radar/releases/download/v1.0.0/anti-radar-demo.mp4 |
| **GitHub** | https://github.com/armaansingla14/Anti-Radar |
| **Release** | [v1.0.0, "Anti Radar v1.0 — Conviction Before Consensus"](https://github.com/armaansingla14/Anti-Radar/releases/tag/v1.0.0) |
| **CI** | green on `main` (backend + frontend, including the snapshot site smoke test with no backend) |
| **Tests** | 939 backend (real Postgres) · 132 frontend · Playwright `/demo` tour · 59-check public-demo verifier |
| **Verified snapshot** | collected **Oct 20, 2026**: 220 companies, 2,804 people, 6 formation candidates, 11 stored reports |
| **Deployment** | Vercel serves the bundled snapshot (`DATA_SOURCE=snapshot`) with no backend on the request path. The Render and Neon backend stays deployed but is optional. Cost: $0/month. |

## Before you hit send

```sh
node scripts/verify_public_demo.mjs            # add --insecure on a network that intercepts TLS
```

Expected: `59 passed, 0 failed`.

## Known limitations

These are said openly in the README and the evaluation:
- **One fixed snapshot.** It was collected Oct 20, 2026 and history is short.
- **Formation is inference, not proof.** The current candidates are early signals.
- **No X or press data.** There is no API access, so both are shown as unavailable rather than zero.
- **No job postings** matched any formation candidate.
- **The public site is read-only.** Live investigations need `deploy.py --live`.

See [FINAL_APPLICATION_AUDIT.md](FINAL_APPLICATION_AUDIT.md) for what was checked and how.
