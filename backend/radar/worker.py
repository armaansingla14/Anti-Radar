"""Scheduled ingestion for local/docker use. Lightweight by design: one process, APScheduler, no queue.

Production runs the same steps as one-shot `radar sweep --only <step>` commands from GitHub
Actions cron; this worker mirrors that schedule for a long-running local process.

Schedule (UTC): HN every 3h · GitHub + young orgs every 6h · arXiv daily · websites + attention
probe daily · score after every ingestion step · morning brief daily · retention weekly.
Every step is recorded in `ingestion_runs` (see `ops.run_step`).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from radar import clock, ops

log = logging.getLogger("radar.worker")

# (job id, steps, cron kwargs)
SCHEDULE: list[tuple[str, tuple[str, ...], dict[str, object]]] = [
    ("hn", ("hn", "score"), {"hour": "*/3", "minute": 7}),
    ("github", ("github", "orgs", "score"), {"hour": "*/6", "minute": 17}),
    ("arxiv", ("arxiv", "score"), {"hour": 5, "minute": 23}),
    ("web", ("web", "probe", "score"), {"hour": 6, "minute": 41}),
    ("brief", ("brief",), {"hour": 7, "minute": 3}),
    ("retention", ("retention",), {"day_of_week": "sun", "hour": 4, "minute": 13}),
]


def _job(name: str, steps: tuple[str, ...]) -> None:
    try:
        out = ops.sweep(list(steps))
        log.warning("worker: %s complete %s", name, {k: v.get("status") for k, v in out.items()})
    except Exception:  # noqa: BLE001 — recorded in ingestion_runs; keep the scheduler alive
        log.exception("worker: %s failed", name)


def run_forever() -> None:
    clock.init_from_db()
    sched = BlockingScheduler(timezone="UTC")
    for job_id, steps, cron in SCHEDULE:
        sched.add_job(_job, "cron", args=(job_id, steps), id=job_id, **cron)
    log.warning("worker started: %s", [j.id for j in sched.get_jobs()])
    sched.start()
