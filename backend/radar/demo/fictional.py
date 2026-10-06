"""Convert the fictional demo dataset (fictional.yaml) into a ProviderBatch.

The fictional set flows through exactly the same ingestion → scoring path as
real data; the only differences are `is_demo=True` on every row and `.example`
URLs. Time series are generated deterministically from the YAML shapes and
stored with source='demo'.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from radar import clock
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    JobRecord,
    MetricPoint,
    NormalizedSignal,
    PaperRecord,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    domain_ref,
    gh_org_ref,
    gh_user_ref,
    paper_ref,
    repo_ref,
)
from radar.providers.domains import registrable_domain

FICTIONAL_PATH = Path(__file__).with_name("fictional.yaml")
DEMO_LABEL = "Fictional example"

CATEGORY_HOST = "https://arxiv.example/abs/"
GH_HOST = "https://github.example/"
X_HOST = "https://x.example/"
HN_HOST = "https://news.ycombinator.example/item?id="


def load_fictional(path: Path = FICTIONAL_PATH) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def _at(days_ago: float, hour: int = 15) -> datetime:
    d = clock.today() - timedelta(days=int(days_ago))
    return datetime.combine(d, time(hour, 0), tzinfo=timezone.utc)


def series(start: float, end: float, days: int, shape: str) -> list[tuple[date, float]]:
    """Deterministic cumulative series ending today."""
    today = clock.today()
    pts: list[tuple[date, float]] = []
    for i in range(days + 1):
        x = i / max(days, 1)
        if shape == "accelerating":
            f = (math.exp(2.4 * x) - 1) / (math.exp(2.4) - 1)
        elif shape == "step":
            f = 0.08 * x if x < 0.62 else 0.08 + 0.92 * min(1.0, (x - 0.62) / 0.2)
        else:
            f = x
        pts.append((today - timedelta(days=days - i), round(start + (end - start) * f)))
    return pts


def _stable_id(text: str, mod: int) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16) % mod


def _example_host(url: str | None) -> str | None:
    """`.example` is a reserved TLD (not in the public suffix list); use the bare host."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname if url else None
    return host if host and host.endswith(".example") else None


def _person_ref(p: dict) -> EntityRef:
    if p.get("github"):
        return gh_user_ref(p["github"])
    return EntityRef(type="person", key=f"name:{p['name'].lower()}")


def build_fictional_batch(data: dict[str, Any] | None = None) -> tuple[ProviderBatch, dict[str, Any]]:
    """Returns (batch, post) where `post` carries fields the generic pipeline doesn't set
    (prior affiliations, stage, headline, first_seen offsets) for the seeder to apply."""
    data = data or load_fictional()
    b = ProviderBatch(provider="demo")
    post: dict[str, Any] = {"people": {}, "companies": {}, "papers": {}}
    people = {p["key"]: p for p in data["people"]}
    pref = {k: _person_ref(p) for k, p in people.items()}

    for key, p in people.items():
        b.people.append(
            PersonRecord(
                ref=pref[key],
                name=p["name"],
                bio=p.get("bio"),
                github_handle=p.get("github"),
                x_handle=p.get("x"),
                current_company=p.get("current_company"),
                followers=p.get("followers"),
                attrs={"demo": True, "headline": p.get("headline"), "papers_24mo": p.get("papers_24mo", 0)},
            )
        )
        post["people"][key] = {
            "ref": pref[key],
            "prior": p.get("prior", []),
            "domains": p.get("domains", []),
            "role": p.get("role"),
            "headline": p.get("headline"),
            "left_days_ago": p.get("left_days_ago"),
        }
        if p.get("followers"):
            b.metrics.append(MetricPoint(entity=pref[key], metric="followers", day=clock.today(),
                                         value=p["followers"], source="demo"))

    def add_repo(owner: str, owner_ref: EntityRef | None, r: dict, owner_type: str) -> EntityRef:
        full = f"{owner}/{r['name']}"
        ref = repo_ref(full)
        stars = series(**r["stars"]) if r.get("stars") else []
        forks = series(**r["forks"]) if r.get("forks") else []
        b.repositories.append(
            RepoRecord(
                ref=ref,
                full_name=full,
                owner=owner,
                owner_type=owner_type,
                description=r.get("description"),
                language=r.get("language"),
                topics=r.get("topics", []),
                stars=int(stars[-1][1]) if stars else 0,
                forks=int(forks[-1][1]) if forks else 0,
                contributors_count=len(r.get("contributors", [])) or None,
                gh_created_at=_at(r.get("created_days_ago", 30)),
                pushed_at=_at(0),
                owner_company=owner_ref if owner_type == "Organization" else None,
                attrs={"demo": True, "html_url": f"{GH_HOST}{full}"},
            )
        )
        for d, v in stars:
            b.metrics.append(MetricPoint(entity=ref, metric="stars", day=d, value=v, source="demo"))
        for d, v in forks:
            b.metrics.append(MetricPoint(entity=ref, metric="forks", day=d, value=v, source="demo"))
        weeks = r.get("commits_week", [])
        for i, c in enumerate(weeks):
            day = clock.today() - timedelta(weeks=len(weeks) - 1 - i)
            day = day - timedelta(days=day.weekday())
            b.metrics.append(MetricPoint(entity=ref, metric="commits_week", day=day, value=c, source="demo"))
        contribs = r.get("contributors", [])
        if contribs:
            b.metrics.append(MetricPoint(entity=ref, metric="contributors", day=clock.today(), value=len(contribs),
                                         source="demo"))
        for i, ck in enumerate(contribs):
            b.relations.append(Relation(kind="contributor", src=pref[ck], dst=ref,
                                        attrs={"contributions": max(5, 120 - 25 * i)}))
        return ref

    def add_signal(s: dict, default_entity: EntityRef, related: list[EntityRef], url_base: str) -> None:
        entity = pref[s["person"]] if s.get("person") else default_entity
        rel = [r for r in related if r != entity]
        if s.get("person"):
            rel.append(default_entity)
        fam = s["family"]
        url = {
            "github": f"{GH_HOST}{url_base}",
            "arxiv": f"{CATEGORY_HOST}{url_base}",
            "x": f"{X_HOST}{url_base}",
            "hackernews": f"{HN_HOST}{_stable_id(s['title'], 10_000_000)}",
        }.get(fam, f"https://{url_base}.example/")
        b.signals.append(
            NormalizedSignal(
                entity=entity,
                signal_type=s["type"],
                source_type=fam,
                source_url=url,
                title=s["title"],
                description=s.get("detail"),
                normalized_value=s.get("strength", 0.5),
                event_at=_at(s["days_ago"]),
                confidence=1.0,
                related=rel,
                metadata={"synthetic": True, "demo_label": DEMO_LABEL},
            )
        )

    company_refs: dict[str, EntityRef] = {}
    for c in data["companies"]:
        domain = registrable_domain(c.get("website")) or _example_host(c.get("website"))
        cref = gh_org_ref(c["github_org"]) if c.get("github_org") else domain_ref(domain)
        alt = [domain_ref(domain)] if (c.get("github_org") and domain) else []
        company_refs[c["key"]] = cref
        b.companies.append(
            CompanyRecord(
                ref=cref,
                name=c["name"],
                description=c.get("description"),
                website=c.get("website"),
                github_org=c.get("github_org"),
                x_handle=c.get("x_handle"),
                location=c.get("location"),
                kind=c.get("kind", "company"),
                founded_date=clock.today() - timedelta(days=c.get("founded_days_ago", 365)),
                alt_refs=alt,
                attrs={"demo": True, "press_mentions": (c.get("attention") or {}).get("press_mentions")},
            )
        )
        post["companies"][c["key"]] = {"ref": cref, "stage": c.get("stage"), "topics": c.get("topics", []),
                                       "first_seen_days_ago": c.get("first_seen_days_ago")}
        att = c.get("attention") or {}
        if att.get("x_followers") is not None:
            b.metrics.append(MetricPoint(entity=cref, metric="followers", day=clock.today(),
                                         value=att["x_followers"], source="demo"))
        if att.get("hn_points_90d"):
            b.metrics.append(MetricPoint(entity=cref, metric="hn_points", day=clock.today() - timedelta(days=20),
                                         value=att["hn_points_90d"], source="demo"))
        repo_refs = [add_repo(c["github_org"], cref, r, "Organization") for r in c.get("repos", [])
                     if c.get("github_org")]
        for fk in c.get("founders", []):
            b.relations.append(Relation(kind="founder", src=pref[fk], dst=cref,
                                        attrs={"role": people[fk].get("role")}))
        for mk in c.get("members", []):
            b.relations.append(Relation(kind="member", src=pref[mk], dst=cref, attrs={"role": people[mk].get("role")}))
        for i, j in enumerate(c.get("jobs", [])):
            b.jobs.append(
                JobRecord(
                    company=cref,
                    external_id=f"demo-{c['key']}-{i}",
                    ats="demo",
                    title=j["title"],
                    department=j.get("department"),
                    location=c.get("location"),
                    url=f"{c.get('website', 'https://demo.example')}/careers#{i}",
                    posted_at=_at(j["days_ago"]),
                )
            )
        url_base = c.get("github_org") or c["key"]
        for s in c.get("signals", []):
            add_signal(s, cref, repo_refs, url_base)
        for xp in c.get("x_posts", []):
            post.setdefault("x_posts", []).append({**xp, "company": c["key"]})

    for pc in data.get("pre_company", []):
        r = pc["repo"]
        if pc.get("org"):
            oref = gh_org_ref(pc["org"])
            b.companies.append(
                CompanyRecord(
                    ref=oref,
                    name=pc["org"],
                    description=r.get("description"),
                    github_org=pc["org"],
                    kind="project",
                    founded_date=clock.today() - timedelta(days=pc.get("org_created_days_ago", 30)),
                    attrs={"demo": True, "gh_org_created_at": _at(pc.get("org_created_days_ago", 30)).isoformat()},
                )
            )
            r = {**r, "contributors": pc.get("members", [])}
            rref = add_repo(pc["org"], oref, r, "Organization")
            for mk in pc.get("members", []):
                b.relations.append(Relation(kind="member", src=pref[mk], dst=oref, attrs={"role": "Member"}))
            anchor = oref
            post["companies"][pc["key"]] = {"ref": oref, "stage": "formation", "topics": [],
                                            "first_seen_days_ago": 3}
        else:
            owner = people[pc["owner"]]
            r = {**r, "contributors": [pc["owner"]]}
            rref = add_repo(owner["github"], None, r, "User")
            anchor = pref[pc["owner"]]
        for s in pc.get("signals", []):
            add_signal(s, anchor, [rref], pc.get("org") or people.get(pc.get("owner", ""), {}).get("github", "demo"))

    for p in data.get("papers", []):
        ref = paper_ref(p["id"])
        authors = [{"name": people[a]["name"]} for a in p["authors"]]
        code = [f"{GH_HOST}{p['code']}"] if p.get("code") else []
        b.papers.append(
            PaperRecord(
                ref=ref,
                arxiv_id=p["id"],
                title=p["title"],
                abstract=p.get("abstract"),
                categories=p.get("categories", []),
                primary_category=(p.get("categories") or [None])[0],
                published_at=_at(p["days_ago"], 9),
                authors=authors,
                code_urls=code,
                url=f"{CATEGORY_HOST}{p['id']}",
                attrs={"demo": True},
            )
        )
        for i, a in enumerate(p["authors"]):
            b.relations.append(Relation(kind="author", src=pref[a], dst=ref, attrs={"position": i}))
        if p.get("code"):
            b.relations.append(Relation(kind="code_for", src=ref, dst=repo_ref(p["code"])))
        post["papers"][p["id"]] = {"company": p.get("company")}
        b.signals.append(
            NormalizedSignal(
                entity=ref,
                signal_type="paper_published",
                source_type="arxiv",
                source_url=f"{CATEGORY_HOST}{p['id']}",
                title=f"Paper: {p['title']}",
                description=", ".join(a["name"] for a in authors) + (" — code released" if code else ""),
                normalized_value=0.6 if code else 0.4,
                event_at=_at(p["days_ago"], 9),
                related=[pref[a] for a in p["authors"]]
                + ([company_refs[p["company"]]] if p.get("company") in company_refs else []),
                metadata={"synthetic": True, "demo_label": DEMO_LABEL},
            )
        )

    # X posts: attach the (fictional) post text to the matching founder-post signal, so one post
    # is one signal; only posts without a matching signal get their own row.
    for xp in post.get("x_posts", []):
        person = people[xp["person"]]
        pref_key = pref[xp["person"]]
        when = _at(xp["days_ago"])
        match = next((sg for sg in b.signals if sg.entity == pref_key and sg.source_type == "x"
                      and sg.event_at.date() == when.date()), None)
        if match is not None:
            match.description = f"{match.description} — “{xp['text']}”" if match.description else f"“{xp['text']}”"
            match.metadata = {**match.metadata, "text": xp["text"]}
            continue
        b.signals.append(
            NormalizedSignal(
                entity=pref_key,
                signal_type="x_post",
                source_type="x",
                source_url=f"{X_HOST}{person.get('x', 'demo')}/status/{_stable_id(xp['text'], 10**12)}",
                title=f"Post by @{person.get('x', 'demo')}",
                description=xp["text"],
                normalized_value=0.3,
                event_at=_at(xp["days_ago"], 17),
                related=[company_refs[xp["company"]]],
                metadata={"synthetic": True, "demo_label": DEMO_LABEL, "text": xp["text"]},
            )
        )
    return b, post
