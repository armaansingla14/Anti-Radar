"""Scheduled ingestion. Lightweight by design: one process, APScheduler, no queue.

Schedule (UTC): HN hourly · GitHub every 6h · arXiv daily · websites daily ·
recompute after every source run. Each job is recorded in `ingestion_runs`.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from radar import clock, ops

log = logging.getLogger("radar.worker")


def _job(name: str, fn) -> None:
    try:
        fn()
        ops.recompute()
        log.warning("worker: %s complete", name)
    except Exception:  # noqa: BLE001 — recorded in ingestion_runs; keep the scheduler alive
        log.exception("worker: %s failed", name)


def run_forever() -> None:
    clock.init_from_db()
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(lambda: _job("hackernews", ops.ingest_hn), "cron", minute=7, id="hn")
    sched.add_job(lambda: _job("github", ops.ingest_github), "cron", hour="*/6", minute=17, id="github")
    sched.add_job(lambda: _job("arxiv", ops.ingest_arxiv), "cron", hour=5, minute=23, id="arxiv")
    sched.add_job(lambda: _job("websites", lambda: ops.crawl_company_sites(limit=20)), "cron", hour=6,
                  minute=41, id="websites")
    log.warning("worker started: %s", [j.id for j in sched.get_jobs()])
    sched.start()
