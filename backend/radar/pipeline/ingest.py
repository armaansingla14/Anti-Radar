"""Ingest a `ProviderBatch`: resolve, upsert with provenance, snapshot, detect change.

Change detection emitted here (all FACT, derived from comparing observations):
``affiliation_changed`` (never from a name-only arXiv identity), ``bio_changed``, ``formation_language`` (people),
``website_changed`` (web pages), ``readme_changed`` (a repository's README content hash
``attrs.readme_sha`` changed between observations) and ``job_posted`` (new ATS jobs after a
company's first crawl).

Provenance: values a person or organization typed into their own GitHub profile
(company, bio, blog, X handle, location, org description/website) are recorded as
INFERENCE with source_type ``github:self-reported`` — they are claims, not observations.
Observed counts and dates (stars, followers, created_at) remain FACT. Everything is idempotent: re-ingesting the same batch
creates no duplicate rows or signals.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from dateutil import parser as dateparser
from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompanyPerson,
    Epistemic,
    Job,
    MetricSnapshot,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    Signal,
    SignalEntity,
    Source,
    WebPage,
)
from radar.pipeline.aliases import normalize_org, normalized_affiliations, org_key, same_org
from radar.pipeline.resolve import (
    CORROBORATED_AUTHOR_VIA,
    AuthorContext,
    Entity,
    Resolver,
    canonical_url,
    record_provenance,
    split_key,
)
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
    arxiv_author_ref,
    normalize_person_name,
)
from radar.providers.domains import github_repos_in, registrable_domain
from radar.providers.formation import _phrase_re, normalize_text
from radar.providers.github import PUBLIC_ORGS_ATTR, merge_lab_contributions
from radar.thesis.loader import Thesis, get_thesis

log = logging.getLogger("radar.pipeline.ingest")

YOUNG_ORG_YEARS = 3
CONTRIBUTOR_MEMBERSHIP_CONFIDENCE = 0.7
BIO_MATERIAL_CHANGE_RATIO = 0.9
_METRIC_PRIORITY = {"observed": 3, "backfill": 2, "demo": 1}
SELF_REPORTED_NOTE = "self-reported on GitHub profile"
SELF_REPORTED_SOURCE = "github:self-reported"
PUBLIC_ORG_NOTE = "Public GitHub organization membership"
LAB_CONTRIBUTIONS_NOTE = ("Public commit history to frontier-lab repositories (GitHub API); contribution is not "
                          "employment")
# Formation language looked for in a person's own profile (bio / company) on top of thesis.formation_phrases.
# A current "founder" self-description stays a formation_language signal: it never becomes a FOUNDED relation.
PROFILE_FORMATION_PHRASES = ("co-founder", "cofounder", "founder", "founding engineer", "stealth",
                             "building something", "starting something", "new company", "day 1")
PROFILE_FORMATION_VALUE = 0.7
# Words just before a phrase (same clause) that make it past tense: "ex-founder", "formerly co-founder of X".
_PAST_CUE = re.compile(r"(?<![a-z0-9])(?:ex|former|formerly|previously|prev|past|retired)(?![a-z0-9])")
_CLAUSE_BREAK = re.compile(r"[.;|•·,\n()\[\]]|\s[-–—/]\s")
# "stealth" alone is ambiguous ("Stealth Browser", stealth games): it counts only as a company status —
# "in stealth", "@stealth", "stealth startup/mode/...", or the whole field / end of a clause.
_STEALTH_STATUS = re.compile(
    r"(?:(?<![a-z0-9])(?:in|at|@)\s*stealth(?![a-z0-9])|stealth\s+(?:startup|start-up|mode|company|co|ai|labs?|"
    r"project|venture)s?(?![a-z0-9])|stealth\s*(?:[.,;:|!)\-–—]|$))")
# Self-reported prior employers: "ex-Google", "ex @meta", "previously @stripe", "formerly Tesla Autopilot".
_TOKEN = r"(?:[\w&+'-]|\.(?=\w))*"  # a dot only inside a word ("scale.ai"), never sentence-final
_ORG_WORDS = rf"[A-Z0-9]{_TOKEN}(?:\s+[A-Z0-9]{_TOKEN}){{0,2}}"
_PRIOR_ORG_PATTERNS = (
    re.compile(rf"(?<![A-Za-z0-9])(?i:ex)\s?[-–]\s?@?(?P<org>[A-Za-z0-9]{_TOKEN}(?:\s+[A-Z0-9]{_TOKEN}){{0,2}})"),
    re.compile(rf"(?<![A-Za-z0-9])(?i:ex)\s+@\s?(?P<org>[A-Za-z0-9]{_TOKEN})"),
    re.compile(rf"(?<![A-Za-z0-9])(?i:formerly|previously|prev|former)[:,]?\s+(?:(?i:at)\s+)?@\s?(?P<org>[A-Za-z0-9]{_TOKEN})"),
    re.compile(rf"(?<![A-Za-z0-9])(?i:formerly|previously|prev|former)[:,]?\s+(?:(?i:at|with)\s+)?(?P<org>{_ORG_WORDS})"),
)
# Captures that are roles or filler, not organizations ("ex-founder", "Former PhD student", "previously a ...").
_NOT_AN_ORG = frozenset({
    "a", "an", "the", "at", "in", "with", "and", "founder", "cofounder", "co-founder", "ceo", "cto", "coo", "cfo",
    "vp", "phd", "ph.d", "engineer", "swe", "sde", "researcher", "scientist", "student", "intern", "lead", "head",
    "director", "manager", "professor", "prof", "postdoc", "ml", "ai", "pm", "em", "faang", "startup", "startups",
    "big", "academic", "academia", "consultant", "freelancer", "employee", "staff", "senior", "principal",
})
# Profile fields a user/org types themselves on GitHub (never VERIFIED).
_SELF_REPORTED_FIELDS = {
    "person": {"bio", "current_company", "location", "personal_site", "x_handle"},
    "company": {"description", "website", "location", "x_handle", "domain"},
}


@dataclass
class IngestStats:
    entities_created: int = 0
    entities_updated: int = 0
    signals_created: int = 0
    signals_duplicate: int = 0
    metrics_upserted: int = 0
    jobs_new: int = 0
    pages_changed: int = 0
    candidates_created: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class _Authorship:
    """One resolved (paper, arXiv author) pair of this batch."""

    person: Person
    via: str
    affiliation: str | None
    paper: ResearchPaper


@dataclass
class _Derived:
    signal: NormalizedSignal
    entity: Entity
    discriminator: str


def ingest_batch(session: Session, batch: ProviderBatch, *, is_demo: bool = False) -> IngestStats:
    """Persist one provider batch. Bad records are rejected individually (savepoints)."""
    return _Ingestor(session, batch, is_demo=is_demo).run()


class _Ingestor:
    def __init__(self, session: Session, batch: ProviderBatch, *, is_demo: bool) -> None:
        self.s = session
        self.batch = batch
        self.provider = batch.provider
        self.is_demo = is_demo
        self.thesis: Thesis = get_thesis()
        self.resolver = Resolver(session, is_demo=is_demo, provider=batch.provider)
        self.stats = IngestStats(rejected=batch.rejected)
        self.derived: list[_Derived] = []
        self.source_ids: dict[str, uuid.UUID] = {}
        self.now = clock.now()
        self.authorships: dict[tuple[str, str], _Authorship] = {}  # (paper key, arxiv_author key)

    # ── driver ──────────────────────────────────────────────────────────────

    def run(self) -> IngestStats:
        b = self.batch
        self.contrib_repos, self.author_names = self._link_hints()
        for rec in b.companies:
            self._guard(f"company {rec.ref.key}", self._company, rec)
        for rec in b.repositories:
            self._guard(f"repository {rec.ref.key}", self._repository, rec)
        for rec in b.papers:
            self._guard(f"paper {rec.ref.key}", self._paper, rec)
        # Structural edges first so the person link rule can see paper code repos.
        early = [r for r in b.relations if r.kind in ("owns", "code_for")]
        arxiv_authors = [r for r in b.relations if r.kind == "author" and _is_arxiv_author(r.src)]
        late = [r for r in b.relations if r.kind not in ("owns", "code_for") and r not in arxiv_authors]
        for rel in early:
            self._guard(f"relation {rel.kind} {rel.src.key}->{rel.dst.key}", self._relation, rel)
        # arXiv authors are resolved per (paper, position) before person records: the name is not an identity.
        paper_recs = {p.ref.key.lower(): p for p in b.papers}
        for rel in arxiv_authors:
            self._guard(f"author {rel.src.key}->{rel.dst.key}", self._arxiv_author, rel,
                        paper_recs.get(rel.dst.key.lower()))
        for rec in b.people:
            self._guard(f"person {rec.ref.key}", self._person, rec)
        for rel in late:
            self._guard(f"relation {rel.kind} {rel.src.key}->{rel.dst.key}", self._relation, rel)
        self._guard("person match candidates", self._person_candidates)
        jobs_by_company: dict[uuid.UUID, list[JobRecord]] = defaultdict(list)
        for j in b.jobs:
            self._guard(f"job {j.company.key}/{j.external_id}", self._group_job, j, jobs_by_company)
        for cid, recs in jobs_by_company.items():
            self._guard(f"jobs {recs[0].company.key}", self._company_jobs, cid, recs)
        self._guard("metrics", self._metrics, b.metrics)
        for src in b.sources:
            self._guard(f"source {src.get('url')}", self._source, src)
        for sig in b.signals:
            self._guard(f"signal {sig.signal_type} {sig.entity.key}", self._signal, sig)
        for d in self.derived:
            self._guard(f"derived signal {d.signal.signal_type}", self._insert_signal, d.signal, d.entity,
                        d.discriminator)
        self.stats.entities_created = len(self.resolver.created)
        self.stats.candidates_created = self.resolver.candidates_created
        self.s.flush()
        return self.stats

    def _guard(self, label: str, fn, *args) -> None:
        n_created, n_derived = len(self.resolver.created), len(self.derived)
        try:
            with self.s.begin_nested():
                fn(*args)
        except Exception as e:  # noqa: BLE001 — one bad record must not sink the batch
            del self.resolver.created[n_created:]
            del self.derived[n_derived:]
            self.resolver.forget()
            self.stats.rejected += 1
            msg = f"{label}: {type(e).__name__}: {e}"[:500]
            self.stats.errors.append(msg)
            log.warning("ingest rejected %s", msg)

    def _link_hints(self) -> tuple[dict[str, set[str]], dict[str, str]]:
        """Pre-scan the batch: repos each GitHub user contributes to (link rule) and arXiv author names."""
        contrib: dict[str, set[str]] = defaultdict(set)
        author_names: dict[str, str] = {}
        for p in self.batch.papers:
            for a in p.authors:
                if a.get("name"):
                    author_names.setdefault(arxiv_author_ref(a["name"]).key, a["name"])
        for r in self.batch.relations:
            if r.kind == "contributor" and r.dst.type == "repository":
                contrib[r.src.key].add(split_key(r.dst)[1].lower())
        return contrib, author_names

    def _person_hint(self, ref: EntityRef) -> set[str]:
        kind, _ = split_key(ref)
        if kind == "github_user":
            return self.contrib_repos.get(ref.key, set())
        return set()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _resolve(self, ref: EntityRef, record: Any = None, **kw) -> Entity:
        res = self.resolver.resolve(ref, record, **kw)
        if res is None:
            raise LookupError(f"cannot resolve {ref.type} {ref.key}")
        if res.created:
            self._apply_first_seen(res.entity, record)
        return res.entity

    def _apply_first_seen(self, entity: Entity, record: Any) -> None:
        attrs = getattr(record, "attrs", None) or {}
        raw = attrs.get("first_seen_at")
        # Use the app clock, not the DB server's now(): when "today" is pinned (snapshot replay,
        # tests) server time would place new rows in the future and hide them from scoring.
        entity.first_seen_at = self.now
        if self.is_demo and raw:
            entity.first_seen_at = _aware(dateparser.isoparse(str(raw)))
        if self.is_demo and attrs.get("demo_label"):
            entity.demo_label = str(attrs["demo_label"])[:64]

    def _prov(self, entity: Entity, etype: str, fld: str, value: Any, source_url: str | None,
              *, self_reported: bool = False) -> None:
        if value in (None, "", [], {}):
            return
        # Fictional demo values are never VERIFIED facts: they are recorded as INFERENCE.
        # Neither are self-reported profile fields: a GitHub "company" is a claim, not an observation.
        inference = self.is_demo or entity.is_demo or self_reported
        record_provenance(
            self.s, etype, entity.id, fld, value,
            source_type=SELF_REPORTED_SOURCE if self_reported else self.provider,
            source_url=source_url or canonical_url(entity),
            epistemic=Epistemic.INFERENCE.value if inference else Epistemic.FACT.value,
            confidence=0.6 if self_reported else 1.0,
            note=SELF_REPORTED_NOTE if self_reported else None,
        )

    def _self_reported(self, etype: str, ref: EntityRef, fld: str) -> bool:
        """A GitHub profile field typed by the user/org itself (user or org keyed records only)."""
        if self.provider != "github" or fld not in _SELF_REPORTED_FIELDS.get(etype, set()):
            return False
        return split_key(ref)[0] in ("github_user", "github_org")

    def _observe(self, entity: Entity, fld: str, new: Any) -> tuple[bool, Any]:
        """Per-provider observation tracking.

        Sets ``entity.<fld> = new`` when the field is empty, when this provider owns the
        current value, or when this provider's own observation changed. Returns
        (field_changed, previous value observed by *this* provider or None).
        """
        if new in (None, ""):
            return False, None
        attrs = dict(entity.attrs or {})
        observed = {k: dict(v) for k, v in (attrs.get("observed") or {}).items()}
        mine = observed.setdefault(self.provider, {})
        owners = dict(attrs.get("field_sources") or {})
        prev = mine.get(fld)
        current = getattr(entity, fld)
        jnew = new.isoformat() if isinstance(new, (date, datetime)) else new
        take = current in (None, "") or owners.get(fld) == self.provider or (prev is not None and prev != jnew)
        changed = False
        if take and current != new:
            setattr(entity, fld, new)
            changed = True
        if take:
            owners[fld] = self.provider
        mine[fld] = jnew
        attrs["observed"], attrs["field_sources"] = observed, owners
        if attrs != (entity.attrs or {}):
            entity.attrs = attrs
        return changed, prev

    @staticmethod
    def _set(entity: Any, fld: str, new: Any) -> bool:
        """Set when new is meaningful and different. Never writes None over a value."""
        if new is None or getattr(entity, fld) == new:
            return False
        setattr(entity, fld, new)
        return True

    def _merge_attrs(self, entity: Entity, extra: dict[str, Any]) -> bool:
        clean = {k: v for k, v in extra.items() if v is not None and k not in ("first_seen_at", "stub")}
        if not clean:
            return False
        attrs = dict(entity.attrs or {})
        before = dict(attrs)
        attrs.update(clean)
        if attrs == before:
            return False
        entity.attrs = attrs
        return True

    def _unstub(self, entity: Entity) -> bool:
        if (entity.attrs or {}).get("stub"):
            entity.attrs = {k: v for k, v in entity.attrs.items() if k != "stub"}
            return True
        return False

    def _derive(self, entity: Entity, etype: str, ref_key: str, signal_type: str, title: str, *,
                description: str | None, metadata: dict, discriminator: str, source_url: str | None,
                normalized_value: float | None = None, epistemic: str = "FACT") -> None:
        sig = NormalizedSignal(
            entity=EntityRef(type=etype, key=ref_key), signal_type=signal_type, source_type=self.provider,
            source_url=source_url, title=title, description=description, event_at=self.now,
            epistemic=epistemic, metadata=metadata, normalized_value=normalized_value,
        )
        self.derived.append(_Derived(sig, entity, discriminator))

    def _count_update(self, created: bool, changed: bool) -> None:
        if changed and not created:
            self.stats.entities_updated += 1

    # ── companies ───────────────────────────────────────────────────────────

    def _company(self, rec: CompanyRecord) -> None:
        res = self.resolver.resolve(rec.ref, rec)
        if res is None:
            raise LookupError(f"cannot resolve company {rec.ref.key}")
        c: Company = res.entity  # type: ignore[assignment]
        if res.created:
            self._apply_first_seen(c, rec)
        changed = False
        was_stub = self._unstub(c)
        if was_stub or res.created:
            changed |= self._set(c, "name", rec.name)
            changed |= self._set(c, "kind", _company_kind(rec, c))
        elif c.kind == "project" and rec.kind == "company" and c.domain:
            c.kind = "company"  # a pre-company project that now has its own company domain
            changed = True
        changed |= was_stub
        for fld in ("description", "website", "location", "founded_date"):
            changed |= self._observe(c, fld, getattr(rec, fld))[0]
        if rec.x_handle and not c.x_handle:
            c.x_handle = rec.x_handle.lstrip("@")
            changed = True
        changed |= self._merge_attrs(c, rec.attrs)
        self._count_update(res.created, changed)
        url = rec.source_url
        for fld in ("name", "description", "website", "location"):
            self._prov(c, "company", fld, getattr(rec, fld), url,
                       self_reported=self._self_reported("company", rec.ref, fld))
        self._prov(c, "company", "domain", c.domain if rec.website else None, url,
                   self_reported=self._self_reported("company", rec.ref, "domain"))
        self._prov(c, "company", "github_org", rec.github_org, url)
        if rec.x_handle:
            self._prov(c, "company", "x_handle", rec.x_handle.lstrip("@"), url,
                       self_reported=self._self_reported("company", rec.ref, "x_handle"))

    # ── people ──────────────────────────────────────────────────────────────

    def _person(self, rec: PersonRecord) -> None:
        if _is_arxiv_author(rec.ref):
            self._arxiv_person(rec)
            return
        res = self.resolver.resolve(rec.ref, rec, code_repos=self._person_hint(rec.ref))
        if res is None:
            raise LookupError(f"cannot resolve person {rec.ref.key}")
        p: Person = res.entity  # type: ignore[assignment]
        if res.created:
            self._apply_first_seen(p, rec)
        self._apply_person(p, rec, created=res.created)

    def _arxiv_person(self, rec: PersonRecord) -> None:
        """Apply a name-keyed arXiv person record to each person that name resolved to in this batch.

        The record's affiliation is the first one seen for the *name* across the batch, so each person
        gets the affiliation printed on their own (most recent) paper instead.
        """
        key = rec.ref.key.lower()
        by_person: dict[uuid.UUID, list[_Authorship]] = defaultdict(list)
        for (_, akey), a in self.authorships.items():
            if akey == key:
                by_person[a.person.id].append(a)
        created = {pid for t, pid in self.resolver.created if t == "person"}
        for pid, items in by_person.items():
            a = max(items, key=lambda x: (_aware(x.paper.published_at) or datetime.min.replace(tzinfo=UTC),
                                          x.paper.arxiv_id))
            attrs = {k: v for k, v in rec.attrs.items() if k != "arxiv_affiliation"}
            if a.affiliation:
                attrs["arxiv_affiliation"] = a.affiliation
            ctx_rec = rec.model_copy(update={"current_company": a.affiliation, "attrs": attrs,
                                             "source_url": a.paper.url or rec.source_url})
            # An affiliation change is only claimed when the identity rests on more than the name.
            self._apply_person(a.person, ctx_rec, created=pid in created,
                               affiliation_signal=any(x.via in CORROBORATED_AUTHOR_VIA for x in items))

    def _apply_person(self, p: Person, rec: PersonRecord, *, created: bool, affiliation_signal: bool = True) -> None:
        url = rec.source_url or canonical_url(p)
        # What this provider saw last time (before the observations below overwrite it).
        seen = dict(((p.attrs or {}).get("observed") or {}).get(self.provider) or {})
        changed = self._unstub(p)
        if changed:
            changed |= self._set(p, "name", rec.name)
        for fld in ("location", "personal_site"):
            changed |= self._observe(p, fld, getattr(rec, fld))[0]
        if rec.followers is not None:
            changed |= self._set(p, "followers", rec.followers)
        if rec.x_handle and not p.x_handle:
            p.x_handle = rec.x_handle.lstrip("@")
            changed = True
        changed |= self._person_affiliation(p, rec, url, emit=affiliation_signal)
        changed |= self._person_bio(p, rec, url)
        if not _is_arxiv_author(rec.ref):  # a name-only identity never carries person signals
            self._profile_formation_language(p, rec, url, seen)
            changed |= self._profile_prior_affiliations(p, rec, url)
        extra = dict(rec.attrs)
        if rec.email_domain:
            extra["email_domain"] = rec.email_domain
        changed |= self._talent_attrs(p, extra)
        changed |= self._merge_attrs(p, extra)
        changed |= _refresh_normalized_affiliations(p)
        self._count_update(created, changed)
        for fld in ("name", "bio", "location", "current_company", "github_handle", "personal_site", "x_handle"):
            val = getattr(rec, fld)
            if fld == "current_company":
                val = _clean_company(val)
            elif fld == "x_handle" and val:
                val = val.lstrip("@")
            self._prov(p, "person", fld, val, url, self_reported=self._self_reported("person", rec.ref, fld))

    def _talent_attrs(self, p: Person, extra: dict[str, Any]) -> bool:
        """Talent-pool attrs that must merge rather than overwrite (pops them from ``extra``).

        ``lab_contributions`` (VERIFIED commit history to frontier-lab repositories, never employment) merges
        per repository; ``talent_pool_since`` is the first time the person entered the pool."""
        new = extra.pop("lab_contributions", None)
        seen_at = extra.pop("talent_pool_observed_at", None)
        if new is None:
            return False
        attrs = dict(p.attrs or {})
        merged = merge_lab_contributions(attrs.get("lab_contributions") or [], new)
        before = (attrs.get("lab_contributions"), attrs.get("talent_pool_since"), attrs.get("talent_pool_observed_at"))
        attrs["lab_contributions"] = merged
        if seen_at:
            attrs["talent_pool_observed_at"] = seen_at
            attrs.setdefault("talent_pool_since", seen_at)
        if before == (attrs.get("lab_contributions"), attrs.get("talent_pool_since"),
                      attrs.get("talent_pool_observed_at")):
            return False
        p.attrs = attrs
        if merged:
            url = f"https://github.com/{p.github_handle}" if p.github_handle else canonical_url(p)
            record_provenance(
                self.s, "person", p.id, "lab_contributions",
                [f"{e.get('commits')} commits to {e.get('repo')}" for e in merged][:20],
                source_type=self.provider, source_url=url,
                epistemic=Epistemic.INFERENCE.value if (self.is_demo or p.is_demo) else Epistemic.FACT.value,
                confidence=1.0, note=LAB_CONTRIBUTIONS_NOTE,
            )
        return True

    def _person_affiliation(self, p: Person, rec: PersonRecord, url: str, *, emit: bool = True) -> bool:
        new = _clean_company(rec.current_company)
        if not new:
            return False
        changed, old = self._observe(p, "current_company", new)
        if emit and old and _affiliation_differs(old, new) and not same_org(old, new):
            if old.lower() not in {a.lower() for a in p.prior_affiliations or []}:
                p.prior_affiliations = [*(p.prior_affiliations or []), old]
            self._derive(
                p, "person", rec.ref.key, "affiliation_changed", f"{p.name}: {old} -> {new}",
                description=f"Listed affiliation changed from '{old}' to '{new}'.",
                metadata={"from": old, "to": new}, discriminator=new.lower(), source_url=url,
                normalized_value=0.6,
            )
            return True
        return changed

    def _person_bio(self, p: Person, rec: PersonRecord, url: str) -> bool:
        new = (rec.bio or "").strip()
        if not new:
            return False
        changed, prev = self._observe(p, "bio", new)
        if not prev or not _material_change(prev, new):
            return changed
        self._derive(
            p, "person", rec.ref.key, "bio_changed", f"{p.name} updated their bio",
            description=new[:500], metadata={"old": prev, "new": new},
            discriminator=_hash(new), source_url=url, normalized_value=0.3,
        )
        return True

    def _profile_formation_language(self, p: Person, rec: PersonRecord, url: str, seen: dict[str, Any]) -> None:
        """formation_language (INFERENCE) for each formation phrase in the person's bio / company field.

        Emitted on the first observation as well as on changes; deduplicated per person + phrase, so a
        phrase that stays in the profile is reported once. Profile text is a self-description, never proof."""
        self_reported = self._self_reported("person", rec.ref, "bio")
        where = "GitHub profile" if self_reported else "profile"
        for fld, text in (("bio", rec.bio), ("current_company", rec.current_company)):
            text = (text or "").strip()
            if not text:
                continue
            before = seen.get(fld)
            known = set(profile_formation_phrases(before, self.thesis)) if isinstance(before, str) else set()
            for phrase in profile_formation_phrases(text, self.thesis):
                label = "bio" if fld == "bio" else "company field"
                verb = "now mentions" if before and phrase not in known else "mentions"
                self._derive(
                    p, "person", rec.ref.key, "formation_language",
                    f"{p.name}'s {where} {label} {verb} '{phrase}'",
                    description=f"Self-described ({label}): \u201c{text[:480]}\u201d" if self_reported else text[:500],
                    metadata={"phrase": phrase, "phrases": [phrase], "field": fld, "text": text[:500],
                              "self_reported": self_reported,
                              "observation": "changed" if before and phrase not in known else "observed"},
                    discriminator=f"phrase:{phrase}", source_url=url, normalized_value=PROFILE_FORMATION_VALUE,
                    epistemic=Epistemic.INFERENCE.value,
                )

    def _profile_prior_affiliations(self, p: Person, rec: PersonRecord, url: str) -> bool:
        """'ex-<org>' / 'previously @org' / 'formerly <org>' in a GitHub bio/company -> prior_affiliations.

        Recorded as INFERENCE provenance with a self-reported note; the matched text is kept in
        ``attrs['self_reported_prior_affiliations']``."""
        if not self._self_reported("person", rec.ref, "bio"):
            return False
        found: list[dict[str, str]] = []
        for fld, text in (("bio", rec.bio), ("current_company", rec.current_company)):
            for org, quote in prior_orgs_in(text):
                if not any(same_org(org, f["org"]) for f in found):
                    found.append({"org": org, "text": quote, "field": fld})
        if not found:
            return False
        changed = False
        prior = list(p.prior_affiliations or [])
        for f in found:
            if same_org(f["org"], p.current_company) or any(same_org(f["org"], a) for a in prior):
                continue
            prior.append(f["org"])
            changed = True
        if changed:
            p.prior_affiliations = prior
        changed |= self._merge_attrs(p, {"self_reported_prior_affiliations": found})
        record_provenance(
            self.s, "person", p.id, "prior_affiliations", [f["org"] for f in found],
            source_type=SELF_REPORTED_SOURCE, source_url=url, epistemic=Epistemic.INFERENCE.value, confidence=0.5,
            note=f"{SELF_REPORTED_NOTE}: " + "; ".join(f"\u201c{f['text']}\u201d" for f in found)[:400],
        )
        return changed

    # ── arXiv authorship ────────────────────────────────────────────────────

    def _arxiv_author(self, rel: Relation, prec: PaperRecord | None) -> None:
        paper = self.resolver.lookup(rel.dst)
        if paper is None:
            raise LookupError(f"paper {rel.dst.key} unknown")
        pos = int(rel.attrs.get("position", 0))
        norm = split_key(rel.src)[1]
        authors = list((prec.authors if prec is not None else None) or paper.authors or [])  # type: ignore[union-attr]
        norms = [normalize_person_name(a.get("name") or "") for a in authors]
        entry: dict[str, Any] = {}
        if pos < len(authors) and norms[pos] == norm:
            entry = authors[pos]
        elif norm in norms:
            entry = authors[norms.index(norm)]
        ctx = AuthorContext(
            name=entry.get("name") or self.author_names.get(rel.src.key) or norm.title(),
            position=pos,
            affiliation=_clean_company(entry.get("affiliation")),
            coauthors={n for n in norms if n != norm and len(n.split()) >= 2},
            code_repos={s.lower() for u in (paper.code_urls or []) for s in github_repos_in(u)},  # type: ignore[union-attr]
        )
        res = self.resolver.resolve_arxiv_author(rel.src, paper, ctx)  # type: ignore[arg-type]
        if res is None:
            raise LookupError(f"cannot resolve arXiv author {rel.src.key}")
        person: Person = res.entity  # type: ignore[assignment]
        self.s.execute(
            pg_insert(PaperAuthor)
            .values(id=uuid.uuid4(), paper_id=paper.id, person_id=person.id, position=pos)
            .on_conflict_do_update(constraint="uq_paper_author", set_={"position": pos})
        )
        self.authorships[(rel.dst.key.lower(), rel.src.key.lower())] = _Authorship(
            person=person, via=res.via, affiliation=ctx.affiliation, paper=paper)  # type: ignore[arg-type]

    def _arxiv_recurrence(self, sig: NormalizedSignal) -> None:
        """Re-derive `author_recurrence` per resolved person: same-name authors that were not tied
        together by evidence do not add up to one person's recurrence. Always INFERENCE."""
        from radar.providers.arxiv import recurrence_signal

        recs = {p.ref.key.lower(): p for p in self.batch.papers}
        groups: dict[uuid.UUID, list[_Authorship]] = defaultdict(list)
        for ref in sig.related:
            a = self.authorships.get((ref.key.lower(), sig.entity.key.lower())) if ref.type == "paper" else None
            if a is not None:
                groups[a.person.id].append(a)
        window = int((sig.metadata or {}).get("window_days") or 21)
        for items in groups.values():
            keys = (f"arxiv:{x.paper.arxiv_id}".lower() for x in items)
            papers = [recs[k] for k in keys if k in recs]
            new = recurrence_signal(
                sig.entity, items[0].person.name, papers, window_days=window,
                metadata={"identity_basis": sorted({x.via for x in items})},
            )
            if new is not None:
                self._insert_signal(new, items[0].person, None)

    # ── repositories & papers ───────────────────────────────────────────────

    def _repository(self, rec: RepoRecord) -> None:
        res = self.resolver.resolve(rec.ref, rec)
        if res is None:
            raise LookupError(f"cannot resolve repository {rec.ref.key}")
        r: Repository = res.entity  # type: ignore[assignment]
        if res.created:
            self._apply_first_seen(r, rec)
            if rec.attrs.get("stub"):
                r.attrs = {**(r.attrs or {}), "stub": True}
        if rec.attrs.get("stub"):
            return  # a reference to a repo, not an observation of it
        changed = self._unstub(r)
        changed |= self._set(r, "full_name", rec.full_name)
        changed |= self._set(r, "owner", rec.owner)
        for fld in ("owner_type", "description", "homepage", "language", "license", "gh_created_at",
                    "pushed_at", "readme_excerpt", "contributors_count"):
            changed |= self._set(r, fld, getattr(rec, fld))
        if rec.readme_excerpt:
            changed |= self._set(r, "readme_hash", _hash(rec.readme_excerpt))
        if rec.topics:
            changed |= self._set(r, "topics", list(rec.topics))
        for fld in ("stars", "forks", "watchers", "open_issues"):
            new = getattr(rec, fld)
            if not (new == 0 and (getattr(r, fld) or 0) > 0):  # 0 over a real count = missing data
                changed |= self._set(r, fld, new)
        changed |= self._set(r, "archived", rec.archived)
        old_sha = (r.attrs or {}).get("readme_sha")
        new_sha = rec.attrs.get("readme_sha")
        changed |= self._merge_attrs(r, rec.attrs)
        if old_sha and new_sha and old_sha != new_sha:
            self._derive(
                r, "repository", rec.ref.key, "readme_changed", f"{r.full_name}: README changed",
                description=(rec.readme_excerpt or "")[:500] or None,
                metadata={"old_sha": old_sha, "new_sha": new_sha, "repo": r.full_name},
                discriminator=str(new_sha), source_url=canonical_url(r), normalized_value=0.3,
            )
        if rec.owner_company is not None:
            company = self._resolve(rec.owner_company, name_hint=rec.owner)
            if r.company_id is None:
                r.company_id = company.id
                changed = True
        self._count_update(res.created, changed)
        url = canonical_url(r)
        self._prov(r, "repository", "description", rec.description, url)
        self._prov(r, "repository", "homepage", rec.homepage, url)

    def _paper(self, rec: PaperRecord) -> None:
        res = self.resolver.resolve(rec.ref, rec)
        if res is None:
            raise LookupError(f"cannot resolve paper {rec.ref.key}")
        p: ResearchPaper = res.entity  # type: ignore[assignment]
        if res.created:
            self._apply_first_seen(p, rec)
        if rec.attrs.get("stub"):
            # A link to a paper (e.g. from HN), not an observation of it: never overwrite the real title/url
            # with the placeholder; a new row stays flagged as a stub until arXiv itself is ingested.
            if res.created:
                p.attrs = {**(p.attrs or {}), **{k: v for k, v in rec.attrs.items() if v is not None}, "stub": True}
                self._set(p, "url", rec.url)
            return
        changed = self._unstub(p)
        for fld, val in (("title", rec.title), ("abstract", rec.abstract), ("primary_category", rec.primary_category),
                         ("published_at", rec.published_at), ("updated_at_src", rec.updated_at), ("url", rec.url)):
            changed |= self._set(p, fld, val)
        if rec.categories:
            changed |= self._set(p, "categories", list(rec.categories))
        if rec.authors:
            changed |= self._set(p, "authors", list(rec.authors))
        for u in rec.code_urls:
            changed |= _add_code_url(p, u)
        changed |= self._merge_attrs(p, rec.attrs)
        self._count_update(res.created, changed)
        self._prov(p, "paper", "title", rec.title, rec.url)

    # ── relations ───────────────────────────────────────────────────────────

    def _relation(self, rel: Relation) -> None:
        kind = rel.kind
        if kind == "mentions":
            return  # mentions carry no structural edge; they arrive as signals with `related` refs
        if kind == "owns":
            company = self._resolve(rel.src)
            repo: Repository = self._resolve(rel.dst)  # type: ignore[assignment]
            if repo.company_id is None:
                repo.company_id = company.id
            return
        if kind == "code_for":
            paper = self.resolver.lookup(rel.src)
            if paper is None:
                raise LookupError(f"paper {rel.src.key} unknown")
            repo = self._resolve(rel.dst)  # type: ignore[assignment]
            # NOTE: services (graph/entities) still key paper<->repo links on github.com URLs, so this stays
            # github.com even for demo rows until they also understand the `.example` host.
            _add_code_url(paper, f"https://github.com/{repo.full_name}")  # type: ignore[arg-type]
            if repo.company_id and paper.company_id is None:  # type: ignore[union-attr]
                paper.company_id = repo.company_id  # type: ignore[union-attr]
            return
        if kind == "member" and PUBLIC_ORGS_ATTR in rel.attrs:
            self._public_orgs(rel)
            return
        person: Person = self._resolve(
            rel.src, name_hint=self.author_names.get(rel.src.key), code_repos=self._person_hint(rel.src) or None
        )  # type: ignore[assignment]
        if kind == "contributor":
            repo = self._resolve(rel.dst)  # type: ignore[assignment]
            self._contributor(person, repo, rel)
        elif kind == "author":
            paper = self.resolver.lookup(rel.dst)
            if paper is None:
                raise LookupError(f"paper {rel.dst.key} unknown")
            self.s.execute(
                pg_insert(PaperAuthor)
                .values(id=uuid.uuid4(), paper_id=paper.id, person_id=person.id,
                        position=int(rel.attrs.get("position", 0)))
                .on_conflict_do_update(constraint="uq_paper_author", set_={"position": int(rel.attrs.get("position", 0))})
            )
        elif kind in ("member", "founder"):
            company = self._resolve(rel.dst)
            self._membership(company.id, person.id, role=rel.attrs.get("role") or (kind if kind == "founder" else None),
                             is_founder=kind == "founder", confidence=rel.confidence, source_url=rel.source_url)

    def _public_orgs(self, rel: Relation) -> None:
        """A tracked GitHub user's public organization memberships -> ``Person.attrs['github_orgs']``.

        Public membership as returned by GitHub's API is an observed fact (FACT provenance, VERIFIED in the
        relationship graph) about membership *now* — not about employment or when it started. Unknown users
        are skipped (never created); no company rows are created for the orgs. Each org keeps the first and
        latest time it was observed; ``github_orgs_checked_at`` records the check even when the list is empty."""
        p = self.resolver.lookup(rel.src)
        if not isinstance(p, Person):
            return
        observed = str(rel.attrs.get("observed_at") or self.now.isoformat())
        before = {str(o.get("login") or "").lower(): o for o in ((p.attrs or {}).get("github_orgs") or [])
                  if isinstance(o, dict)}
        orgs = []
        for o in rel.attrs.get(PUBLIC_ORGS_ATTR) or []:
            login = str(o.get("login") or "").strip()
            if not login:
                continue
            prev = before.get(login.lower()) or {}
            entry = {"login": login, "name": o.get("name") or login,
                     "first_observed_at": prev.get("first_observed_at") or observed, "observed_at": observed}
            created = o.get("created_at") or prev.get("created_at")  # the org's own creation date, when checked
            if created:
                entry["created_at"] = created
            orgs.append(entry)
        attrs = dict(p.attrs or {})
        attrs["github_orgs"] = orgs
        attrs["github_orgs_checked_at"] = observed
        p.attrs = attrs
        url = rel.source_url or (f"https://github.com/{p.github_handle}" if p.github_handle else canonical_url(p))
        if orgs:
            demo = self.is_demo or p.is_demo
            record_provenance(
                self.s, "person", p.id, "github_orgs", [o["login"] for o in orgs],
                source_type=self.provider, source_url=url,
                epistemic=Epistemic.INFERENCE.value if demo else Epistemic.FACT.value,
                confidence=1.0, note=PUBLIC_ORG_NOTE,
            )
        self.stats.entities_updated += 1

    def _contributor(self, person: Person, repo: Repository, rel: Relation) -> None:
        vals: dict[str, Any] = {}
        if rel.attrs.get("contributions") is not None:
            vals["contributions"] = int(rel.attrs["contributions"])
        # Exact dates only: ``first_commit_at_approximate`` (repo created_at) is never persisted, it would
        # pose as a real first commit. ``first_commit_week`` is the older attr name for the same fact.
        raw = {"first_commit_at": rel.attrs.get("first_commit_at") or rel.attrs.get("first_commit_week"),
               "last_commit_at": rel.attrs.get("last_commit_at")}
        for k, v in raw.items():
            if v:
                vals[k] = _aware(v if isinstance(v, datetime) else dateparser.isoparse(str(v)))
        if vals.get("first_commit_at") and vals.get("last_commit_at") and vals["last_commit_at"] < vals["first_commit_at"]:
            vals["last_commit_at"] = vals["first_commit_at"]
        stmt = pg_insert(RepoContributor).values(
            id=uuid.uuid4(), repository_id=repo.id, person_id=person.id, **{"contributions": 0, **vals}
        )
        if vals:
            # Merge across runs: earliest first commit, latest last commit (LEAST/GREATEST ignore NULLs).
            merge = {"first_commit_at": func.least, "last_commit_at": func.greatest}
            stmt = stmt.on_conflict_do_update(constraint="uq_repo_contributor", set_={
                k: merge[k](getattr(RepoContributor.__table__.c, k), stmt.excluded[k]) if k in merge
                else stmt.excluded[k] for k in vals})
        else:
            stmt = stmt.on_conflict_do_nothing(constraint="uq_repo_contributor")
        self.s.execute(stmt)
        company = self.s.get(Company, repo.company_id) if repo.company_id else None
        if company is not None and _owned_by_young_org(repo, company):
            self._membership(company.id, person.id, role="contributor", is_founder=False,
                             confidence=CONTRIBUTOR_MEMBERSHIP_CONFIDENCE,
                             source_url=canonical_url(repo), weak=True)

    def _membership(self, company_id: uuid.UUID, person_id: uuid.UUID, *, role: str | None, is_founder: bool,
                    confidence: float, source_url: str | None, weak: bool = False) -> None:
        existing = self.s.scalars(
            select(CompanyPerson).where(CompanyPerson.company_id == company_id, CompanyPerson.person_id == person_id)
        ).first()
        if existing is None:
            self.s.add(CompanyPerson(id=uuid.uuid4(), company_id=company_id, person_id=person_id, role=role,
                                     is_founder=is_founder, confidence=confidence, source_url=source_url))
            return
        if weak:
            return  # never downgrade an explicit membership with a contributor-derived one
        existing.is_founder = existing.is_founder or is_founder
        existing.confidence = max(existing.confidence or 0.0, confidence)
        if role and (existing.role in (None, "contributor") or is_founder):
            existing.role = role
        existing.source_url = existing.source_url or source_url

    def _person_candidates(self) -> None:
        """Identical names + shared repo/paper/company/affiliation -> pending MatchCandidate."""
        for etype, pid in list(self.resolver.created):
            if etype != "person":
                continue
            p = self.s.get(Person, pid)
            norm = normalize_person_name(p.name)
            if len(norm.split()) < 2:
                continue
            for other_id in self.resolver.people_with_name(norm):
                if other_id == pid:
                    continue
                shared = _shared_context(self.s, pid, other_id)
                if shared:
                    kind, detail = shared
                    conf = 0.8 if kind in ("repo", "paper") else 0.6
                    self.resolver.add_candidate("person", pid, other_id, method=f"same_name+{kind}",
                                                confidence=conf, evidence={"name": norm, "shared": detail})

    # ── jobs ────────────────────────────────────────────────────────────────

    def _group_job(self, job: JobRecord, groups: dict[uuid.UUID, list[JobRecord]]) -> None:
        groups[self._resolve(job.company).id].append(job)

    def _company_jobs(self, company_id: uuid.UUID, recs: list[JobRecord]) -> None:
        """Upsert one company's jobs. A batch's job list is a full refresh per (company, ATS) when complete."""
        company = self.s.get(Company, company_id)
        if company is None:
            raise LookupError(f"company {company_id} vanished")
        prior_crawl = bool((company.attrs or {}).get("jobs_first_crawled_at")) or self.s.scalar(
            select(exists().where(Job.company_id == company.id))
        ) or self.s.scalar(select(exists().where(
            MetricSnapshot.entity_id == company.id, MetricSnapshot.metric == "open_roles",
            MetricSnapshot.day < clock.today(),
        )))
        existing = {j.external_id: j for j in self.s.scalars(select(Job).where(Job.company_id == company.id))}
        seen: set[str] = set()
        for rec in recs:
            if rec.external_id in seen:
                continue
            seen.add(rec.external_id)
            role_class = classify_role(rec.title, rec.department, self.thesis)
            job = existing.get(rec.external_id)
            if job is None:
                job = Job(id=uuid.uuid4(), company_id=company.id, external_id=rec.external_id, ats=rec.ats,
                          title=rec.title, department=rec.department, role_class=role_class,
                          location=rec.location, url=rec.url, posted_at=_aware(rec.posted_at),
                          first_seen_at=self.now, is_demo=self.is_demo)
                self.s.add(job)
                self.stats.jobs_new += 1
                if prior_crawl:
                    self._derive(
                        company, "company", f"job:{company.id}", "job_posted", f"{company.name} is hiring: {rec.title}",
                        description=", ".join(x for x in (rec.department, rec.location) if x) or None,
                        metadata={"role_class": role_class, "external_id": rec.external_id, "ats": rec.ats,
                                  "title": rec.title, "department": rec.department, "location": rec.location},
                        discriminator=f"{rec.ats}:{rec.external_id}", source_url=rec.url,
                    )
            else:
                for fld, val in (("title", rec.title), ("department", rec.department), ("location", rec.location),
                                 ("url", rec.url), ("role_class", role_class), ("posted_at", _aware(rec.posted_at))):
                    self._set(job, fld, val)
                job.closed_at = None
        # Full refresh only for boards fetched completely; a truncated list is a lower bound.
        ats_seen = {r.ats for r in recs} - {r.ats for r in recs if not r.complete}
        for ext, job in existing.items():
            if ext not in seen and job.closed_at is None and job.ats in ats_seen:
                job.closed_at = self.now
        attrs = dict(company.attrs or {})
        attrs.setdefault("jobs_first_crawled_at", self.now.isoformat())
        attrs["jobs_last_crawled_at"] = self.now.isoformat()
        company.attrs = attrs

    # ── metrics ─────────────────────────────────────────────────────────────

    def _metrics(self, points: list[MetricPoint]) -> None:
        rows: dict[tuple[uuid.UUID, str, date], dict[str, Any]] = {}
        for mp in points:
            try:
                ent = self._resolve(mp.entity)
            except LookupError as e:
                self.stats.rejected += 1
                self.stats.errors.append(f"metric {mp.metric} {mp.entity.key}: {e}"[:500])
                continue
            demo = self.is_demo or mp.source == "demo"
            row = {"id_": (ent.id, mp.metric, mp.day), "entity_type": mp.entity.type, "entity_id": ent.id,
                   "metric": mp.metric, "day": mp.day, "value": float(mp.value), "source": mp.source,
                   "is_demo": demo}
            key = row.pop("id_")
            prev = rows.get(key)
            if prev is None or _METRIC_PRIORITY[mp.source] >= _METRIC_PRIORITY[prev["source"]]:
                rows[key] = row
        values = list(rows.values())
        t = MetricSnapshot.__table__
        for i in range(0, len(values), 500):
            stmt = pg_insert(MetricSnapshot).values(values[i:i + 500])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_metric_day",
                set_={"value": stmt.excluded.value, "source": stmt.excluded.source, "is_demo": stmt.excluded.is_demo},
                where=and_(
                    not_(and_(t.c.source == "observed", stmt.excluded.source != "observed")),
                    not_(and_(t.c.is_demo.is_(False), stmt.excluded.is_demo.is_(True))),
                ),
            )
            self.stats.metrics_upserted += len(self.s.scalars(stmt.returning(MetricSnapshot.id)).all())

    # ── sources & web pages ─────────────────────────────────────────────────

    def _source(self, src: dict[str, Any]) -> None:
        url = src.get("url")
        if not url:
            raise ValueError("source without url")
        chash = src.get("content_hash")
        row = {"url": url, "source_type": src.get("source_type") or self.provider, "title": src.get("title"),
               "snippet": src.get("snippet"), "content_hash": chash, "http_status": src.get("http_status"),
               "is_demo": self.is_demo}
        if chash is None:
            sid = self.s.scalar(select(Source.id).where(Source.url == url, Source.content_hash.is_(None)))
        else:
            sid = self.s.scalar(
                pg_insert(Source).values(id=uuid.uuid4(), **row).on_conflict_do_nothing(
                    constraint="uq_source_url_hash").returning(Source.id)
            ) or self.s.scalar(select(Source.id).where(Source.url == url, Source.content_hash == chash))
        if sid is None:
            sid = uuid.uuid4()
            self.s.add(Source(id=sid, **row))
        self.source_ids[url] = sid
        if src.get("page_kind"):
            self._web_page(src)

    def _web_page(self, src: dict[str, Any]) -> None:
        url, kind, chash = src["url"], src["page_kind"], src.get("content_hash")
        company = self._page_company(src)
        page = self.s.scalars(select(WebPage).where(WebPage.url == url)).first()
        text = src.get("text_excerpt") or src.get("text") or src.get("snippet")
        if page is None:
            self.s.add(WebPage(id=uuid.uuid4(), company_id=company.id if company else None, url=url, page_kind=kind,
                               title=src.get("title"), text_excerpt=text, content_hash=chash,
                               last_fetched_at=self.now, last_changed_at=None, extracted=src.get("extracted") or {}))
            return
        old = page.content_hash
        page.last_fetched_at = self.now
        page.page_kind = kind
        if company is not None and page.company_id is None:
            page.company_id = company.id
        if src.get("title"):
            page.title = src["title"]
        if text:
            page.text_excerpt = text
        if src.get("extracted"):
            page.extracted = dict(src["extracted"])
        if chash and old and chash != old:
            page.content_hash = chash
            page.last_changed_at = self.now
            self.stats.pages_changed += 1
            owner = company or (self.s.get(Company, page.company_id) if page.company_id else None)
            if owner is not None:
                label = "Careers page" if kind == "careers" else f"Website {kind} page"
                self._derive(
                    owner, "company", f"company:{owner.id}", "website_changed", f"{owner.name}: {label} changed",
                    description=url,
                    metadata={"page_kind": kind, "url": url, "old_hash": old, "new_hash": chash,
                              "careers_page": kind == "careers"},
                    discriminator=chash, source_url=url, normalized_value=0.5 if kind == "careers" else 0.3,
                )
        elif chash:
            page.content_hash = chash

    def _page_company(self, src: dict[str, Any]) -> Company | None:
        ref = src.get("company")
        if isinstance(ref, dict):
            ref = EntityRef(**ref)
        elif isinstance(ref, str):
            ref = EntityRef(type="company", key=ref)
        if isinstance(ref, EntityRef):
            return self._resolve(ref)  # type: ignore[return-value]
        dom = registrable_domain(src["url"])
        return self.resolver.lookup(EntityRef(type="company", key=f"domain:{dom}")) if dom else None  # type: ignore[return-value]

    # ── signals ─────────────────────────────────────────────────────────────

    def _signal(self, sig: NormalizedSignal) -> None:
        if _is_arxiv_author(sig.entity):
            if sig.signal_type != "author_recurrence":
                raise LookupError(f"{sig.signal_type} on name-only arXiv author {sig.entity.key}")
            self._arxiv_recurrence(sig)
            return
        ent = self._resolve(sig.entity) if sig.entity.type != "paper" else self.resolver.lookup(sig.entity)
        if ent is None:
            raise LookupError(f"paper {sig.entity.key} unknown")
        self._insert_signal(sig, ent, None)

    def _insert_signal(self, sig: NormalizedSignal, ent: Entity, discriminator: str | None) -> None:
        dedup = sig.dedup_key()
        if discriminator is not None:
            dedup = _hash(f"{sig.signal_type}|{ent.id}|{discriminator}")
        etype = sig.entity.type
        sid = self.s.scalar(
            pg_insert(Signal).values(
                id=uuid.uuid4(), entity_id=ent.id, entity_type=etype, signal_type=sig.signal_type,
                source_type=sig.source_type, source_url=sig.source_url,
                source_id=self.source_ids.get(sig.source_url or ""), title=sig.title[:2000],
                description=sig.description, raw_value=sig.raw_value, normalized_value=sig.normalized_value,
                detected_at=self.now, event_at=_aware(sig.event_at), confidence=sig.confidence,
                epistemic=sig.epistemic, dedup_key=dedup, is_demo=self.is_demo, meta=sig.metadata,
            ).on_conflict_do_nothing(index_elements=["dedup_key"]).returning(Signal.id)
        )
        if sid is None:
            self.stats.signals_duplicate += 1
            return
        self.stats.signals_created += 1
        links: list[tuple[uuid.UUID, str, str]] = []
        ctx_paper = sig.entity.key if sig.entity.type == "paper" else next(
            (r.key for r in sig.related if r.type == "paper"), None)
        for ref in sig.related:
            if _is_arxiv_author(ref):
                a = self.authorships.get((ctx_paper.lower(), ref.key.lower())) if ctx_paper else None
                other = a.person if a is not None else None  # no paper context: never link by name
            elif ref.type == "paper":
                other = self.resolver.lookup(ref)
            else:
                other = self._resolve(ref)
            if other is not None and other.id != ent.id:
                links.append((other.id, ref.type, "related"))
        if isinstance(ent, Repository) and ent.company_id:
            links.append((ent.company_id, "company", "repo_owner"))
        if isinstance(ent, ResearchPaper):
            for pid in self.s.scalars(select(PaperAuthor.person_id).where(PaperAuthor.paper_id == ent.id)):
                links.append((pid, "person", "paper_author"))
        for eid, et, rel in links:
            self.s.execute(
                pg_insert(SignalEntity).values(id=uuid.uuid4(), signal_id=sid, entity_id=eid, entity_type=et,
                                               relation=rel).on_conflict_do_nothing(constraint="uq_signal_entity")
            )


# ── pure helpers ─────────────────────────────────────────────────────────────


def _is_arxiv_author(ref: EntityRef) -> bool:
    return ref.type == "person" and split_key(ref)[0] == "arxiv_author"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _clean_company(name: str | None) -> str | None:
    if not name:
        return None
    n = re.sub(r"\s+", " ", name).strip().lstrip("@").strip()
    return n or None


def _company_kind(rec: CompanyRecord, c: Company) -> str:
    """A GitHub org with no company domain stays a pre-company 'project' (see resolve._initial_company_kind)."""
    if rec.kind == "company" and split_key(rec.ref)[0] == "github_org" and not c.domain:
        return "project"
    return rec.kind


def _refresh_normalized_affiliations(p: Person) -> bool:
    """Person.attrs['affiliations_normalized'] := alias-normalized current/prior affiliations.

    The raw strings stay in Person.current_company / prior_affiliations.
    """
    if not p.current_company and not p.prior_affiliations:
        return False
    norm = normalized_affiliations(p.current_company, list(p.prior_affiliations or []))
    if (p.attrs or {}).get("affiliations_normalized") == norm:
        return False
    p.attrs = {**(p.attrs or {}), "affiliations_normalized": norm}
    return True


def _affiliation_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _affiliation_differs(a: str, b: str) -> bool:
    """'@openai' vs 'OpenAI' or 'MIT' vs 'MIT CSAIL' are not changes."""
    ka, kb = _affiliation_key(a), _affiliation_key(b)
    if not ka or not kb or ka == kb:
        return False
    return not (re.search(rf"\b{re.escape(ka)}\b", kb) or re.search(rf"\b{re.escape(kb)}\b", ka))


def _material_change(old: str, new: str) -> bool:
    a = re.sub(r"\s+", " ", old).strip().lower()
    b = re.sub(r"\s+", " ", new).strip().lower()
    return a != b and difflib.SequenceMatcher(None, a, b).ratio() < BIO_MATERIAL_CHANGE_RATIO


def formation_phrases_in(text: str | None, thesis: Thesis) -> list[str]:
    """Thesis formation phrases present in text (case-insensitive, word-bounded)."""
    if not text:
        return []
    t = text.lower().replace("’", "'").replace("‘", "'")
    return [ph for ph in thesis.formation_phrases
            if re.search(r"(?<![a-z0-9])" + re.escape(ph.lower()) + r"(?![a-z0-9])", t)]


def profile_formation_phrases(text: str | None, thesis: Thesis) -> list[str]:
    """Formation phrases in a person's own profile text, in order of appearance.

    thesis.formation_phrases plus PROFILE_FORMATION_PHRASES, word-bounded and case-insensitive.
    A phrase in a past-tense clause ("ex-founder", "formerly co-founder of X") is skipped, and a
    phrase lying inside a longer matched one ("founder" in "co-founder") is dropped."""
    if not text:
        return []
    norm = normalize_text(text)
    phrases = list(dict.fromkeys(ph.lower() for ph in [*thesis.formation_phrases, *PROFILE_FORMATION_PHRASES]))
    hits: list[tuple[int, int, str]] = []
    for ph in phrases:
        for m in _phrase_re(ph).finditer(norm):
            clause = _CLAUSE_BREAK.split(norm[: m.start()])[-1]
            if ph == "stealth" and not _STEALTH_STATUS.search(norm, max(0, m.start() - 4), m.end() + 12):
                continue
            if not _PAST_CUE.search(clause):
                hits.append((m.start(), m.end(), ph))
                break
    kept = [h for h in hits if not any(o is not h and o[0] <= h[0] and h[1] <= o[1] and (o[1] - o[0]) > (h[1] - h[0])
                                       for o in hits)]
    return [ph for _, _, ph in sorted(kept)]


def prior_orgs_in(text: str | None) -> list[tuple[str, str]]:
    """Self-reported prior organizations: [(normalized org, matched text)] from 'ex-<org>',
    'ex @org', 'previously @org' / 'previously at <Org>', 'formerly <Org>'."""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    for pat in _PRIOR_ORG_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group("org").strip().rstrip(".,;:'-")
            first = raw.split()[0].lower().lstrip("@") if raw.split() else ""
            if not raw or first in _NOT_AN_ORG or len(org_key(raw)) < 2:
                continue
            org = normalize_org(raw)
            if org and not any(same_org(org, o) for o, _ in out):
                out.append((org, m.group(0).strip()))
    return out


def classify_role(title: str, department: str | None, thesis: Thesis) -> str | None:
    from radar.providers.roles import classify_role as provider_classify

    return provider_classify(title, department, thesis)


def _add_code_url(paper: ResearchPaper, url: str) -> bool:
    slugs = {s.lower() for u in (paper.code_urls or []) for s in github_repos_in(u)}
    new = {s.lower() for s in github_repos_in(url)}
    if (new and new <= slugs) or url in (paper.code_urls or []):
        return False
    paper.code_urls = [*(paper.code_urls or []), url]
    return True


def _org_created(company: Company) -> date | None:
    if company.founded_date:
        return company.founded_date
    for k in ("created_at", "org_created_at", "github_created_at"):
        raw = (company.attrs or {}).get(k)
        if raw:
            try:
                return dateparser.isoparse(str(raw)).date()
            except (ValueError, OverflowError):
                continue
    return None


def _owned_by_young_org(repo: Repository, company: Company) -> bool:
    """Contributor -> member only for repos of the org itself, created within 3 years."""
    if not company.github_org or company.github_org.lower() != (repo.owner or "").lower():
        return False
    created = _org_created(company)
    return created is not None and created >= clock.today() - timedelta(days=365 * YOUNG_ORG_YEARS)


def _shared_context(s: Session, a: uuid.UUID, b: uuid.UUID) -> tuple[str, list[str]] | None:
    repos = s.execute(
        select(Repository.full_name).join(RepoContributor, RepoContributor.repository_id == Repository.id)
        .where(RepoContributor.person_id == a)
        .where(RepoContributor.repository_id.in_(select(RepoContributor.repository_id).where(RepoContributor.person_id == b)))
    ).scalars().all()
    if repos:
        return "repo", sorted(repos)
    papers = s.execute(
        select(ResearchPaper.arxiv_id).join(PaperAuthor, PaperAuthor.paper_id == ResearchPaper.id)
        .where(PaperAuthor.person_id == a)
        .where(PaperAuthor.paper_id.in_(select(PaperAuthor.paper_id).where(PaperAuthor.person_id == b)))
    ).scalars().all()
    if papers:
        return "paper", sorted(papers)
    comps = s.execute(
        select(Company.name).join(CompanyPerson, CompanyPerson.company_id == Company.id)
        .where(CompanyPerson.person_id == a)
        .where(CompanyPerson.company_id.in_(select(CompanyPerson.company_id).where(CompanyPerson.person_id == b)))
    ).scalars().all()
    if comps:
        return "company", sorted(comps)
    pa, pb = s.get(Person, a), s.get(Person, b)
    if pa.current_company and pb.current_company and (
            not _affiliation_differs(pa.current_company, pb.current_company)
            or same_org(pa.current_company, pb.current_company)):
        return "affiliation", [pa.current_company, pb.current_company]
    return None


__all__ = ["IngestStats", "classify_role", "formation_phrases_in", "ingest_batch", "prior_orgs_in",
           "profile_formation_phrases"]
