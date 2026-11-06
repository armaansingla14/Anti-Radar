"""Stage 2: type raw changes as candidate events with explicit, ordered rules (no LLM).

Signal kinds map directly (a USAspending row is a government contract; a release is a repository release by
semver). Company posts and Hacker News story titles go through ``TITLE_RULES`` in order; the first match wins:

=====  ==========  ======================  ===============================================================
N1     RISK        security_incident …     breach / outage / incident / lawsuit / recall / vulnerability
N2     FINANCING   acquisition             acquires / acquired / acquisition
N3     FINANCING   new_round               series A–H / seed round / funding round / raises|raised + $amount …
N4     FINANCING   strategic_investment    strategic investment / invests in / investment from
N5     REGULATORY  regulatory_milestone    FAA / FCC / NRC / FDA / export control / operating licence / regulat…
N6     COMMERCIAL  government_contract     contract / awarded / award + an agency word
N7     COMMERCIAL  partnership             partner / partnership / collaboration
N8     COMMERCIAL  customer_announcement   deploys / deployed / deployment / selects / customer
N9     TALENT      executive_join / …      joins as / appoints / welcomes … + a leadership title; steps down
N11    PRODUCT     product_launch          launch / introducing / announcing / now available / GA / unveils
N10    TECHNICAL   benchmark_claim         benchmark / N× faster / state-of-the-art / SOTA / outperforms
N12    TECHNICAL   research_paper          paper / technical report / we present / arXiv
N13    TECHNICAL   open_source_release     open-source / open sourcing
N14    PRODUCT     new_feature             new feature / now supports / adds support / integration
N15    PRODUCT     pricing_change          pricing
N16    STRATEGIC   market_entry            expands to / expansion / enters / new office / opens in
—      NOISE       generic_post            anything else
=====  ==========  ======================  ===============================================================

A performance claim anywhere in a title (``N× faster``, ``benchmark``, ``state-of-the-art``…) is also kept as
``meta.performance_claim`` so a launch that makes one is routed to RUN DILIGENCE. Hiring clusters are built from
job postings per specialised role class (INFERENCE). External text is untrusted: titles are only matched and
displayed, and instruction-like text is flagged (``meta.injection_flags``) by ``llm.guard``.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from radar.llm.guard import detect_injection
from radar.portfolio.types import NOISE_TYPE, Candidate, RawChange
from radar.providers.roles import GENERIC_CLASSES, GTM_CLASS

AMOUNT_RE = re.compile(r"\$\s?(\d+(?:[.,]\d+)?)\s?(k|m|b|mm|bn|million|billion|thousand)?\b", re.IGNORECASE)
PERF_RE = re.compile(r"\b(benchmarks?|\d+(?:\.\d+)?\s?[x×]\s(?:faster|cheaper|more efficient|speedup)|"
                     r"state[- ]of[- ]the[- ]art|sota|outperform\w*|record[- ]breaking|fastest)\b", re.IGNORECASE)
AGENCY_RE = re.compile(r"\b(DoD|Department of (?:Defense|Energy|the \w+)|Air Force|AFWERX|Army|Navy|Marine Corps|"
                       r"Space Force|NASA|DARPA|DIU|SBIR|STTR|DOE|federal|government|NATO|Pentagon|Missile Defense)\b",
                       re.IGNORECASE)
LEADER_TITLE_RE = re.compile(r"\b(CEO|CTO|COO|CFO|CPO|Chief \w+ Officer|President|VP|Vice President|Head of|"
                             r"General Counsel|board of directors|co-?founder)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TitleRule:
    id: str
    event_type: str
    subtype: str
    pattern: re.Pattern[str]
    needs: re.Pattern[str] | None = None  # a second pattern that must also match (e.g. an agency word)


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


TITLE_RULES: tuple[TitleRule, ...] = (
    TitleRule("N1", "RISK", "security_incident",
              _rx(r"\b(breach|security incident|vulnerabilit(y|ies)|cve-\d{4}|compromised|exploit(ed)?)\b")),
    TitleRule("N1", "RISK", "outage", _rx(r"\b(outage|downtime|service disruption|post-?mortem|incident report)\b")),
    TitleRule("N1", "RISK", "lawsuit", _rx(r"\b(lawsuit|sued|sues|litigation|settlement|class action)\b")),
    TitleRule("N1", "RISK", "recall", _rx(r"\b(recall|recalls|recalled)\b")),
    TitleRule("N2", "FINANCING", "acquisition", _rx(r"\b(acquires|acquired|acquisition of|to acquire|acquisition)\b")),
    TitleRule("N3", "FINANCING", "new_round",
              _rx(r"\b(series [a-h]\b|seed round|pre-seed|funding round|(raises|raised|raising|secures|closes)\b"
                  r"[^.]{0,40}(\$|funding|round|financing|investment)|\$\s?\d[\d.,]*\s?(m|b|million|billion)\b"
                  r"[^.]{0,30}\b(round|funding|raise|financing))")),
    TitleRule("N4", "FINANCING", "strategic_investment",
              _rx(r"\b(strategic investment|invests in|investment from|backed by)\b")),
    TitleRule("N5", "REGULATORY", "regulatory_milestone",
              _rx(r"\b(FAA|FCC|NRC|FDA|EPA|ITAR|export controls?|regulat\w*|type certificate|"
                  r"(?:operating|construction|launch|spectrum|reactor|commercial|flight|fusion) licen[cs]\w*|"
                  r"licen[cs]e[ds]? (?:from|by) the|regulatory approval)\b")),
    TitleRule("N6", "COMMERCIAL", "government_contract", _rx(r"\b(contracts?|awarded|awards?|task order|OTA)\b"),
              AGENCY_RE),
    TitleRule("N7", "COMMERCIAL", "partnership", _rx(r"\b(partners? with|partnership|partnering|collaborat\w+|"
                                                     r"teams up)\b")),
    TitleRule("N8", "COMMERCIAL", "customer_announcement",
              _rx(r"\b(deploys|deployed (at|with|by)|deployment (at|with)|selects|chooses|customer)\b")),
    TitleRule("N9", "TALENT", "executive_departure",
              _rx(r"\b(steps down|stepping down|departs|departure|leaves|leaving|resigns)\b"), LEADER_TITLE_RE),
    TitleRule("N9", "TALENT", "executive_join",
              _rx(r"\b(joins|joined|appoints|appointed|names|welcomes|hires|hired|promotes|new)\b"), LEADER_TITLE_RE),
    TitleRule("N11", "PRODUCT", "product_launch",
              _rx(r"\b(launch(es|ed|ing)?|introducing|introduces|announcing|announces|unveil\w*|now available|"
                  r"generally available|general availability|GA)\b")),
    TitleRule("N10", "TECHNICAL", "benchmark_claim", PERF_RE),
    TitleRule("N12", "TECHNICAL", "research_paper",
              _rx(r"\b(paper|technical report|tech report|we present|arxiv|preprint|research)\b")),
    TitleRule("N13", "TECHNICAL", "open_source_release", _rx(r"\b(open[- ]sourc\w+)\b")),
    TitleRule("N14", "PRODUCT", "new_feature",
              _rx(r"\b(new feature|now supports?|adds? support|support for|integration with|integrates with)\b")),
    TitleRule("N15", "PRODUCT", "pricing_change", _rx(r"\b(pricing|price change|new prices?|free tier)\b")),
    TitleRule("N16", "STRATEGIC", "market_entry",
              _rx(r"\b(expands? (to|into)|expansion|enters|new office|opens (in|an?)|now in)\b")),
)

NOISE_SUBTYPES = frozenset({"generic_post", "minor_copy_update", "routine_docs", "routine_release", "minor_repo",
                            "hiring_state", "activity_metric", "low_engagement_discussion", "single_postings",
                            "other"})
# The stored event type of a NOISE candidate (its materiality is NOISE; the type only says which area it touched).
NOISE_EVENT_TYPE = {"generic_post": "STRATEGIC", "minor_copy_update": "STRATEGIC", "low_engagement_discussion":
                    "STRATEGIC", "other": "STRATEGIC", "routine_docs": "TECHNICAL", "routine_release": "TECHNICAL",
                    "minor_repo": "TECHNICAL", "activity_metric": "TECHNICAL", "hiring_state": "TALENT",
                    "single_postings": "TALENT"}
HN_TYPES = frozenset({"hn_launch", "hn_show", "hn_discussion"})
HN_ENGAGED_POINTS = 50  # below this, an unmatched HN discussion is low-engagement social noise
SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+.]?([0-9A-Za-z.-]+))?$")
CLUSTER_DAYS = 14
CLUSTER_WATCH = 5
CLUSTER_MATERIAL = 10
NEW_CLASS_BASELINE = 5  # dated postings before the window needed to call a role class "never seen before"
ACCEL_MIN, ACCEL_RATIO = 5, 2.0
CLOSURES_MIN = 5
OSS_TRACTION_STARS = 50


def amount_usd(text: str) -> float | None:
    """The first dollar amount in ``text`` in USD ("$12M" -> 12e6), or None."""
    m = AMOUNT_RE.search(text or "")
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
    return v * mult.get(unit, 1.0)


def match_title(title: str) -> TitleRule | None:
    for rule in TITLE_RULES:
        if rule.pattern.search(title or "") and (rule.needs is None or rule.needs.search(title or "")):
            return rule
    return None


def semver_level(tag: str) -> str | None:
    """major (X.0.0, X >= 1) | minor (X.Y.0) | patch | prerelease | None (not semver)."""
    m = SEMVER_RE.search((tag or "").strip())
    if not m:
        return None
    major, minor, patch, pre = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4)
    if pre and re.search(r"(alpha|beta|rc|pre|dev|nightly)", pre, re.IGNORECASE):
        return "prerelease"
    if patch:
        return "patch"
    if minor == 0 and major >= 1:
        return "major"
    return "minor"


def _cand(r: RawChange, event_type: str, subtype: str, rule: str, **kw) -> Candidate:
    meta = {"signal_type": r.signal_type, **{k: v for k, v in r.meta.items() if k in (
        "repo", "repo_stars", "tag", "page_kind", "url", "points", "num_comments", "link", "amount", "agency",
        "award_id", "role_class", "person", "role", "arxiv_id", "date_basis", "first_release", "from", "to",
        "old_title", "new_title", "award_group")}}
    flags = detect_injection(f"{r.title}\n{r.description or ''}")
    if flags:
        meta["injection_flags"] = flags[:3]
    if PERF_RE.search(r.title or ""):
        meta["performance_claim"] = True
    meta.update(kw.pop("meta", {}))
    return Candidate(company_id=r.company_id, event_type=event_type, subtype=subtype, title=r.title.strip()[:500],
                     event_at=r.event_at, sources=[r.source], epistemic=kw.pop("epistemic", r.epistemic),
                     confidence=kw.pop("confidence", r.confidence), description=r.description, meta=meta, rule=rule,
                     in_window=r.in_window, **kw)


def _by_title(r: RawChange, *, noise_subtype: str = "generic_post") -> Candidate:
    rule = match_title(r.title)
    if rule is None:
        return _cand(r, NOISE_TYPE, noise_subtype, "N0")
    meta = {}
    if rule.event_type in ("FINANCING", "COMMERCIAL"):
        amt = amount_usd(r.title)
        if amt is not None:
            meta["amount"] = amt
    return _cand(r, rule.event_type, rule.subtype, rule.id, meta=meta)


def normalize_one(r: RawChange) -> Candidate | None:
    """Type one raw change. Job postings / closures return None (they are clustered by ``normalize``)."""
    st = r.signal_type
    if r.kind in ("job_posted", "job_closed"):
        return None
    if st == "company_post":
        return _by_title(r)
    if st == "federal_award":
        amt = r.raw_value if r.raw_value is not None else r.meta.get("amount")
        return _cand(r, "COMMERCIAL", "government_contract", "N6",
                     meta={"amount": float(amt) if amt is not None else None, "agency": r.meta.get("agency")})
    if st in HN_TYPES:
        c = _by_title(r)
        points = int(r.meta.get("points") or r.raw_value or 0)
        if c.event_type == NOISE_TYPE:
            if st in ("hn_launch", "hn_show"):
                return _cand(r, "PRODUCT", "product_launch", "N11", confidence=min(r.confidence, 0.8))
            if points < HN_ENGAGED_POINTS:
                return _cand(r, NOISE_TYPE, "low_engagement_discussion", "N0")
            return _cand(r, "STRATEGIC", "public_attention", "NH", epistemic="FACT")
        return c
    if st == "release_published":
        level = semver_level(str(r.meta.get("tag") or ""))
        if r.meta.get("prerelease") or level in ("prerelease", "patch"):
            return _cand(r, NOISE_TYPE, "routine_release", "NR", meta={"semver": level})
        if r.meta.get("first_release"):
            level = level or "first"
        if level is None:
            return _cand(r, NOISE_TYPE, "routine_release", "NR", meta={"semver": None})
        return _cand(r, "TECHNICAL", "repository_release", "NR", meta={"semver": level})
    if st == "repo_created":
        stars = int(r.meta.get("repo_stars") or r.raw_value or 0)
        if stars >= OSS_TRACTION_STARS:
            return _cand(r, "TECHNICAL", "open_source_release", "N13", meta={"stars": stars})
        return _cand(r, NOISE_TYPE, "minor_repo", "N0", meta={"stars": stars})
    if st in ("paper_published", "paper_with_code"):
        return _cand(r, "TECHNICAL", "research_paper", "N12")
    if st == "star_acceleration":
        return _cand(r, "TECHNICAL", "repo_traction", "NT", epistemic="INFERENCE")
    if st in ("commit_acceleration", "star_velocity", "new_contributors"):
        return _cand(r, NOISE_TYPE, "activity_metric", "N0")
    if st == "hiring_active":
        return _cand(r, NOISE_TYPE, "hiring_state", "N0")
    if st == "affiliation_changed":
        return _affiliation(r)
    if st == "website_changed":
        return _page_change(r)
    if st in ("readme_changed", "bio_changed"):
        return _cand(r, NOISE_TYPE, "routine_docs", "N0")
    if st == "formation_language":
        return _cand(r, NOISE_TYPE, "minor_copy_update", "N0")
    if st == "open_roles_jump":
        return _cand(r, "TALENT", "hiring_acceleration", "NJ", epistemic="INFERENCE")
    return _cand(r, NOISE_TYPE, "other", "N0")


def _affiliation(r: RawChange) -> Candidate:
    """A linked founder / leader whose public affiliation moved to or away from the company."""
    name = str(r.meta.get("company_name") or "").lower()
    to, frm = str(r.meta.get("to") or "").lower(), str(r.meta.get("from") or "").lower()
    if name and name in to:
        return _cand(r, "TALENT", "executive_join", "N9")
    if name and name in frm:
        return _cand(r, "TALENT", "executive_departure", "N9")
    return _cand(r, NOISE_TYPE, "other", "N0")


def _page_change(r: RawChange) -> Candidate:
    """A page whose content hash changed. Homepage title change = repositioning; everything else is copy."""
    kind = str(r.meta.get("page_kind") or "")
    url = str(r.meta.get("url") or r.source.url or "")
    old_t, new_t = r.meta.get("old_title"), r.meta.get("new_title")
    if kind == "home" and old_t and new_t and _norm(old_t) != _norm(new_t):
        c = _cand(r, "STRATEGIC", "repositioning", "NS", meta={"old_title": old_t, "new_title": new_t})
        c.title = f"Homepage title changed from “{old_t}” to “{new_t}”"[:500]
        return c
    if "/pricing" in url.lower():
        return _cand(r, "PRODUCT", "pricing_change", "N15")
    if kind == "docs":
        return _cand(r, NOISE_TYPE, "routine_docs", "N0")
    return _cand(r, NOISE_TYPE, "minor_copy_update", "N0")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# ── hiring clusters ──────────────────────────────────────────────────────────


def specialised(role_class: str | None) -> bool:
    return bool(role_class) and role_class != GTM_CLASS and role_class not in GENERIC_CLASSES


def technical(role_class: str | None) -> bool:
    return bool(role_class) and role_class != GTM_CLASS


def hiring_candidates(raw: list[RawChange], day: date) -> list[Candidate]:
    """Clusters from dated postings / closures of one company (every job is one raw change of the cluster).

    * ``specialized_hiring_cluster``: >= 5 new postings of one specialised role class in the last 14 days;
    * ``new_role_class``: a specialised class posted in the last 14 days with no earlier dated posting, when the
      company has >= 5 dated postings before the window (enough history to say "never seen before");
    * ``hiring_acceleration``: >= 5 new technical postings in 14 days and >= 2x the prior 14 days;
    * ``role_closures``: >= 5 technical postings closed in 14 days (filled or withdrawn: the board does not say).
    Postings that form no cluster become one NOISE candidate per company (``single_postings``)."""
    end = datetime.combine(day, time.max, tzinfo=UTC)
    cut, prior_cut = end - timedelta(days=CLUSTER_DAYS), end - timedelta(days=2 * CLUSTER_DAYS)
    by_company: dict[uuid.UUID, list[RawChange]] = defaultdict(list)
    for r in raw:
        if r.kind in ("job_posted", "job_closed"):
            by_company[r.company_id].append(r)
    out: list[Candidate] = []
    for cid, items in by_company.items():
        posted = [r for r in items if r.kind == "job_posted"]
        closed = [r for r in items if r.kind == "job_closed" and r.in_window]
        recent = [r for r in posted if r.event_at >= cut and r.in_window]
        prior = [r for r in posted if prior_cut <= r.event_at < cut]
        before_window = [r for r in posted if not r.in_window]
        used: set[int] = set()
        by_class: dict[str, list[RawChange]] = defaultdict(list)
        for r in recent:
            by_class[str(r.meta.get("role_class") or "")].append(r)
        seen_classes = {str(r.meta.get("role_class") or "") for r in posted if r.event_at < cut}
        for rc, rs in sorted(by_class.items()):
            if not specialised(rc):
                continue
            label = rc.replace("_", " ")
            if len(rs) >= CLUSTER_WATCH:
                out.append(_cluster(cid, "specialized_hiring_cluster", rs,
                                    f"{len(rs)} new {label} roles posted in {CLUSTER_DAYS} days",
                                    {"role_class": rc, "count": len(rs), "window_days": CLUSTER_DAYS}))
                used.update(id(r) for r in rs)
            elif rc not in seen_classes and len(before_window) >= NEW_CLASS_BASELINE:
                out.append(_cluster(cid, "new_role_class", rs,
                                    f"First {label} role{'s' if len(rs) != 1 else ''} posted "
                                    f"({len(rs)} in {CLUSTER_DAYS} days; none in the earlier dated postings)",
                                    {"role_class": rc, "count": len(rs), "window_days": CLUSTER_DAYS,
                                     "baseline_postings": len(before_window)}))
                used.update(id(r) for r in rs)
        tech_recent = [r for r in recent if technical(r.meta.get("role_class"))]
        tech_prior = [r for r in prior if technical(r.meta.get("role_class"))]
        if len(tech_recent) >= ACCEL_MIN and len(tech_recent) >= ACCEL_RATIO * max(len(tech_prior), 1) \
                and any(id(r) not in used for r in tech_recent):
            rest = [r for r in tech_recent if id(r) not in used]
            out.append(_cluster(cid, "hiring_acceleration", rest or tech_recent,
                                f"{len(tech_recent)} new technical roles posted in the last {CLUSTER_DAYS} days "
                                f"(vs {len(tech_prior)} in the {CLUSTER_DAYS} days before)",
                                {"count": len(tech_recent), "prior": len(tech_prior), "window_days": CLUSTER_DAYS}))
            used.update(id(r) for r in rest)
        tech_closed = [r for r in closed if r.event_at >= cut and technical(r.meta.get("role_class"))]
        if len(tech_closed) >= CLOSURES_MIN:
            out.append(_cluster(cid, "role_closures", tech_closed,
                                f"{len(tech_closed)} technical roles closed on the public job board in "
                                f"{CLUSTER_DAYS} days (filled or withdrawn; the board does not say which)",
                                {"count": len(tech_closed), "window_days": CLUSTER_DAYS}))
            used.update(id(r) for r in tech_closed)
        leftovers = [r for r in items if r.in_window and id(r) not in used]
        if leftovers:
            c = _cluster(cid, "single_postings", leftovers,
                         f"{len(leftovers)} job board change{'s' if len(leftovers) != 1 else ''} with no cluster",
                         {"count": len(leftovers)})
            c.event_type = NOISE_TYPE
            out.append(c)
    return out


def _cluster(cid: uuid.UUID, subtype: str, rs: list[RawChange], title: str, meta: dict) -> Candidate:
    rs = sorted(rs, key=lambda r: (r.event_at, r.title))
    return Candidate(company_id=cid, event_type="TALENT", subtype=subtype, title=title, event_at=rs[-1].event_at,
                     sources=[r.source for r in rs], epistemic="INFERENCE", confidence=0.9,
                     description="; ".join(f"{r.title} ({r.event_at.date().isoformat()})" for r in rs[:12]),
                     raw_count=len(rs), meta={**meta, "first_at": rs[0].event_at.isoformat(),
                                              "titles": [r.title for r in rs[:20]]},
                     rule="NH-jobs", in_window=True)


def normalize(raw: list[RawChange], day: date) -> list[Candidate]:
    """All candidates (window and horizon) from raw changes, deterministic order."""
    out = [c for r in raw if (c := normalize_one(r)) is not None]
    out += hiring_candidates(raw, day)
    out.sort(key=lambda c: (c.event_at, str(c.company_id), c.event_type, c.subtype, c.title))
    return out
