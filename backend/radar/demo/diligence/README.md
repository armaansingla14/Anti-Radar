# Captured diligence runs

Each `<company-slug>.json.gz` file is one captured Technical Diligence collection. It contains:

- the public artifacts
- the company facts snapshot
- the request trace
- `collected_at`

Files are gzip-compressed JSON with sorted keys and a zero gzip timestamp, so the same capture always produces the same bytes.

- **Capture (live network):** `radar diligence run "<company>" --capture`
- **Replay one (offline):** `radar diligence replay <slug>`
- **Replay all (offline):** `radar diligence replay --all`, or `radar.diligence.capture.replay_all(session)`, which the snapshot seed uses.

A replay re-runs the deterministic analysis over the stored artifacts and never touches the network. The company must already exist in the database; the slug, GitHub org or domain must match. The method is described in `docs/DILIGENCE_METHOD.md`.
