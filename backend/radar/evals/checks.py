"""Deterministic checks over one agent output (a stored ``ResearchReport``).

Two views are kept apart on purpose:

* **delivered** — the body the user reads. Stored reports already passed ``validate_citations`` (invalid ids
  stripped, uncited FACTs downgraded), so these checks look for what that guard cannot see: hedged FACTs,
  affiliation or funding claims resting on the wrong kind of source, stale sources, sources that may not say
  what the claim says.
* **guard interventions** — what the model produced *before* the guard, recorded only in the trace's
  ``validation`` entry. They become ``guard_*`` flags and are never counted in the delivered unsupported rate.

Every rule has a stable ``rule`` id (used for de-duplication and documented in docs/AGENT_EVALUATION.md).
No rule judges semantic support: the token-overlap rule only says a source *may* be unrelated and asks a human.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse

from radar.evals.taxonomy import FailureCategory as C
from radar.evals.taxonomy import Severity as S
from radar.llm.guard import detect_injection, normalize_source_id

STALE_DAYS = 90


@dataclass
class Flag:
    rule: str
    category: str
    severity: str
    section: str | None = None
    claim_text: str | None = None
    citation: str | None = None
    detail: str = ""
    needs_human_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"], d["severity"] = str(self.category), str(self.severity)
        return d


@dataclass
class ClaimRef:
    section: str
    text: str
    epistemic: str | None
    source_ids: list[str]


@dataclass
class SourceInfo:
    id: str
    url: str | None = None
    title: str | None = None
    source_type: str | None = None
    fetched_at: datetime | None = None
    content: str | None = None


@dataclass
class CheckResult:
    claims: list[ClaimRef] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    citations_total: int = 0
    invalid_citations: int = 0
    cited_ids: set[str] = field(default_factory=set)
    unsupported: set[int] = field(default_factory=set)  # indices into ``claims``
    unverifiable_citations: int = 0  # cited sources whose content could not be resolved
    possibly_unrelated: int = 0  # claims flagged source_may_be_unrelated (human review, not unsupported)
    sources: dict[str, SourceInfo] = field(default_factory=dict)
    has_claim_schema: bool = True  # False: the report kind has no source-cited claims (metrics stay null)


# ── claims and sources ───────────────────────────────────────────────────────


def _is_claim(d: Any) -> bool:
    return isinstance(d, dict) and "text" in d and "epistemic" in d and "source_ids" in d


def iter_claims(body: Any, path: str = "") -> Iterator[ClaimRef]:
    """Every ``{text, epistemic, source_ids}`` dict in a report body; ``section`` is its dotted key path."""
    if _is_claim(body):
        yield ClaimRef(section=path or "body", text=str(body.get("text") or ""), epistemic=body.get("epistemic"),
                       source_ids=[str(s) for s in body.get("source_ids") or []])
    elif isinstance(body, dict):
        for k, v in body.items():
            yield from iter_claims(v, f"{path}.{k}" if path else str(k))
    elif isinstance(body, list):
        for item in body:
            yield from iter_claims(item, path)


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return None


def report_time(report: Any) -> datetime:
    """The logical time of a report: its body ``date`` (end of that day), else ``created_at``, else now.

    Sources are registered with the app clock (which may be pinned to a snapshot day) while ``created_at`` is
    wall-clock time, so the body date is the comparable one."""
    body = getattr(report, "body", None) or {}
    d = body.get("date") if isinstance(body, dict) else None
    if isinstance(d, str):
        try:
            return datetime.combine(datetime.fromisoformat(d[:10]).date(), time(23, 59, 59), tzinfo=UTC)
        except ValueError:
            pass
    created = _dt(getattr(report, "created_at", None))
    if created is not None:
        return created
    from radar import clock

    return clock.now()


def resolve_sources(session: Any, report: Any) -> dict[str, SourceInfo]:
    """Report sources by id, with content (title + stored 500-char snippet) resolved from ``Source`` rows by url.

    The row chosen per url is the latest with ``fetched_at <= report time + 1 day``, else the latest row."""
    out: dict[str, SourceInfo] = {}
    for s in getattr(report, "sources", None) or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        sid = normalize_source_id(str(s["id"]))
        out[sid] = SourceInfo(id=sid, url=s.get("url"), title=s.get("title"),
                              source_type=s.get("source_type") or s.get("type"), fetched_at=_dt(s.get("fetched_at")),
                              content=s.get("snippet") or s.get("content"))
    if session is None:
        return out
    urls = sorted({i.url[:2000] for i in out.values() if i.url and i.content is None})
    if not urls:
        return out
    from sqlalchemy import select

    from radar.models import Source

    limit = report_time(report) + timedelta(days=1)
    by_url: dict[str, list[Any]] = {}
    for row in session.execute(select(Source.url, Source.snippet, Source.fetched_at).where(Source.url.in_(urls))):
        by_url.setdefault(row.url, []).append(row)
    for info in out.values():
        rows = by_url.get((info.url or "")[:2000])
        if not rows or info.content is not None:
            continue
        rows.sort(key=lambda r: _dt(r.fetched_at) or datetime.min.replace(tzinfo=UTC))
        before = [r for r in rows if (_dt(r.fetched_at) or limit) <= limit]
        pick = (before or rows)[-1]
        info.content = pick.snippet or ""
    return out


# ── rule vocabulary ──────────────────────────────────────────────────────────

NEGATION_RE = re.compile(r"\b(no|not|without|unknown|none|never)\b", re.IGNORECASE)
AFFILIATION_RE = re.compile(r"\b(works? at|working at|joined|left|former(ly)?|currently at|hired( by)?|ex-[A-Z]"
                            r"|employed (at|by)|now at)\b", re.IGNORECASE)
FUNDING_RE = re.compile(r"\b(raised|raises|raising|funding|seed round|pre-seed round|Series [A-Z]\b|valuation|"
                        r"valued at|backed by)\b|\$\d+(\.\d+)?\s?(M|B|million|billion)\b", re.IGNORECASE)
CUSTOMER_RE = re.compile(r"\b(customers?|used by|trusted by|deployed at|clients?)\b", re.IGNORECASE)
HEDGE_RE = re.compile(r"(?i:\b(likely|might|suggests?|probably|appears? to|possibly|perhaps|presumably)\b)"
                      r"|\bmay\b(?! \d)")
QUOTED_RE = re.compile(r"\"([^\"]{2,})\"|“([^”]{2,})”")

EVENT_SOURCE_TYPES = frozenset({"conference", "talk", "event", "paper", "arxiv", "hackernews", "hn", "video",
                                "podcast"})
EVENT_URL_RE = re.compile(r"gtc|conference|summit|/talks?/|/events?/|youtube|neurips|icml|iclr|cvpr|arxiv\.org|"
                          r"news\.ycombinator", re.IGNORECASE)
# crunchbase is listed although no such source is collected today.
FUNDING_SOURCE_TYPES = frozenset({"press", "news", "announcement", "company_announcement", "filing", "sec",
                                  "crunchbase"})
FUNDING_URL_RE = re.compile(r"/press|/news|/blog/announc|techcrunch|sec\.gov", re.IGNORECASE)
OWN_SITE_TYPES = frozenset({"website", "company"})

_STOPWORDS = frozenset("""
a an and are as at be but by for from has have he her his i if in into is it its no not of on or our she so
that the their them then there these they this those to was we were what when where which who why will with
you your all any also each every first last new one two three our however because although while after before
during since until about above below over under between both either neither other same such than too very
unknown recent recently currently linked fit bear github repository repositories
""".split())
_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s?%?")
_CAP_RE = re.compile(r"\b[A-Z][A-Za-z0-9&'-]+\b")


def _strip_quotes(text: str) -> str:
    return QUOTED_RE.sub(" ", text)


def _norm(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", text or "").lower()


def key_tokens(text: str) -> list[str]:
    """Distinctive tokens of a claim: numbers (>= 2 digits, or with $/%), quoted strings, and capitalized words
    that are neither sentence-initial nor stopwords. Lower-cased; numbers without separators."""
    toks: list[str] = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).strip()
        digits = re.sub(r"[^\d.]", "", raw).strip(".")
        if digits and (len(re.sub(r"\D", "", digits)) >= 2 or "$" in raw or "%" in raw):
            toks.append(digits)
    for m in QUOTED_RE.finditer(text):
        q = (m.group(1) or m.group(2) or "").strip().lower()
        if q:
            toks.append(q)
    for m in _CAP_RE.finditer(text):
        before = text[: m.start()].rstrip()
        if not before or before[-1] in ".!?:":
            continue
        w = m.group(0).strip("'-")
        if len(w) > 1 and w.lower() not in _STOPWORDS:
            toks.append(w.lower())
    return list(dict.fromkeys(toks))


def _token_in(token: str, haystack: str) -> bool:
    if re.fullmatch(r"[\d.]+", token):
        return re.search(rf"(?<![\d.]){re.escape(token)}(?![\d])", haystack) is not None
    return token in haystack


def _clause(text: str, start: int, end: int) -> str:
    left = max(text.rfind(c, 0, start) for c in ".;")
    rights = [i for i in (text.find(c, end) for c in ".;") if i != -1]
    return text[left + 1: min(rights) if rights else len(text)]


def _negated_match(rx: re.Pattern[str], text: str) -> bool:
    """True when ``rx`` matches ``text`` only inside negated clauses (no / not / without / unknown / none / never,
    or "not collected")."""
    hits = list(rx.finditer(text))
    if not hits:
        return False
    for m in hits:
        cl = _clause(text, m.start(), m.end())
        if not (NEGATION_RE.search(cl) or "not collected" in cl.lower()):
            return False
    return True


def _matches_affirmatively(rx: re.Pattern[str], text: str) -> bool:
    return bool(rx.search(text)) and not _negated_match(rx, text)


def _host(url: str | None) -> str:
    if not url:
        return ""
    s = url if "://" in url else "http://" + url
    return (urlparse(s).hostname or "").lower().removeprefix("www.")


def domain_of(url: str | None) -> str | None:
    if not url or url.startswith("radar://"):
        return None
    from radar.providers.domains import registrable_domain

    return registrable_domain(url) or _host(url) or None


def is_event_source(s: SourceInfo) -> bool:
    if (s.source_type or "").lower() in EVENT_SOURCE_TYPES:
        return True
    return bool(EVENT_URL_RE.search(s.url or "") or EVENT_URL_RE.search(s.title or ""))


def is_funding_source(s: SourceInfo) -> bool:
    if (s.source_type or "").lower() in FUNDING_SOURCE_TYPES:
        return True
    return bool(FUNDING_URL_RE.search(s.url or ""))


def is_own_marketing(s: SourceInfo, subject_domain: str | None) -> bool:
    if (s.source_type or "").lower() in OWN_SITE_TYPES:
        return True
    d = domain_of(s.url)
    return bool(subject_domain and d and d == subject_domain.lower().removeprefix("www."))


def _short(text: str, n: int = 300) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ── claim rules ──────────────────────────────────────────────────────────────


def check_claims(claims: list[ClaimRef], sources: dict[str, SourceInfo], *, report_at: datetime,
                 subject_domain: str | None = None) -> CheckResult:
    """Apply the claim rules (see module doc and docs/AGENT_EVALUATION.md) to delivered claims."""
    res = CheckResult(claims=list(claims), sources=sources)
    report_at = _dt(report_at) or report_at
    for i, c in enumerate(claims):
        text = c.text or ""
        epi = (c.epistemic or "").upper()
        ids = [normalize_source_id(s) for s in c.source_ids]
        valid = [s for s in dict.fromkeys(ids) if s in sources]
        cited = [sources[s] for s in valid]

        def flag(rule: str, cat: str, sev: str, detail: str, citation: str | None = None, review: bool = False,
                 _c: ClaimRef = c, _t: str = text) -> None:
            res.flags.append(Flag(rule=rule, category=cat, severity=sev, section=_c.section, claim_text=_short(_t),
                                  citation=citation, detail=detail, needs_human_review=review))

        res.citations_total += len(ids)
        res.cited_ids.update(valid)
        for s in ids:
            if s not in sources:
                res.invalid_citations += 1
                flag("nonexistent_citation", C.CITATION_MISMATCH, S.HIGH, f"claim citing nonexistent source {s}", s)
        if ids and not valid:
            res.unsupported.add(i)

        # 1. FACT without any citation
        if epi == "FACT" and not ids:
            flag("fact_without_citation", C.UNSUPPORTED_CLAIM, S.HIGH, "FACT with no cited source")
            res.unsupported.add(i)

        # 3. FACT whose cited sources share none of its key tokens (human review; not unsupported)
        if epi == "FACT" and cited:
            unresolved = [s for s in cited if s.content is None]
            res.unverifiable_citations += len(unresolved)
            toks = key_tokens(text)
            if toks and not unresolved:
                hay = " ".join(_norm(f"{s.title or ''}\n{s.content or ''}") for s in cited)
                if not any(_token_in(t, hay) for t in toks):
                    res.possibly_unrelated += 1
                    flag("source_may_be_unrelated", C.CITATION_MISMATCH, S.LOW,
                         f"none of the claim's key tokens ({', '.join(toks[:6])}) appear in the cited source text "
                         f"({', '.join(valid)}); the source may not say this", ",".join(valid), review=True)

        # 4. employment / affiliation stated as FACT on event-like sources only
        if epi == "FACT" and cited and _matches_affirmatively(AFFILIATION_RE, text) and all(
                is_event_source(s) for s in cited):
            flag("affiliation_from_event_source", C.AFFILIATION_INFERENCE, S.HIGH,
                 "employment/affiliation stated as fact, but every cited source is a talk, event, paper or post "
                 "(appearing at an event is not employment)", ",".join(valid))
            res.unsupported.add(i)

        # 5. funding without a funding-type source
        if epi != "MODEL_OPINION" and _matches_affirmatively(FUNDING_RE, text) and not any(
                is_funding_source(s) for s in cited):
            flag("funding_without_funding_source", C.UNSUPPORTED_CLAIM, S.HIGH,
                 "funding claim without a press, announcement or filing source", ",".join(valid) or None)
            res.unsupported.add(i)

        # 6. customers / adoption sourced only from the company's own pages
        if epi != "MODEL_OPINION" and cited and _matches_affirmatively(CUSTOMER_RE, text) and all(
                is_own_marketing(s, subject_domain) for s in cited):
            flag("customer_from_own_marketing", C.SOURCE_QUALITY, S.MEDIUM,
                 "customer/adoption claim sourced only from the company's own pages", ",".join(valid))

        # 7. hedged language labelled FACT
        if epi == "FACT" and HEDGE_RE.search(_strip_quotes(text)):
            flag("hedged_fact", C.OVERCONFIDENT_INFERENCE, S.MEDIUM, "FACT mislabeled; should be INFERENCE")

        # 8. stale sources (one flag per claim)
        old = [s for s in cited if s.fetched_at is not None and (report_at - s.fetched_at).days > STALE_DAYS]
        if old:
            worst = max((report_at - s.fetched_at).days for s in old if s.fetched_at is not None)
            flag("stale_source", C.STALE_INFORMATION, S.LOW,
                 f"cited source fetched {worst} days before the report (> {STALE_DAYS})", ",".join(s.id for s in old))
    return res


# ── trace rules ──────────────────────────────────────────────────────────────

_NO_LLM_PREFIX = "LLMUnavailable: no live LLM provider configured"
_STRUCTURED_HINTS = ("llmoutputinvalid", "validation", "schema", "parse", "truncated")


def fallback_info(trace: list[dict] | None, kind: str | None = None) -> tuple[bool, str | None]:
    """(an LLM was attempted and the template replaced it, reason).

    analyst: a step labelled "LLM unavailable (X); using deterministic template" (only written when a provider
    was available). meeting: a ``fallback`` entry, except "no live LLM provider configured" (nothing attempted).
    memo: not recorded, always False."""
    if kind == "memo":
        return False, None
    for t in trace or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "fallback":
            reason = str(t.get("reason") or "")
            if reason.startswith(_NO_LLM_PREFIX):
                continue
            return True, reason
        label = str(t.get("label") or "")
        if t.get("type") == "step" and label.startswith("LLM unavailable") and "deterministic template" in label:
            return True, label
    return False, None


def _output_texts(body: Any) -> Iterator[tuple[str, str]]:
    """(section, text) for every claim text and every free string in lists (questions, unknowns...)."""
    if isinstance(body, dict):
        if _is_claim(body):
            yield "claim", str(body.get("text") or "")
            return
        for k, v in body.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        yield k, item
                    else:
                        for sec, t in _output_texts(item):
                            yield (k if sec == "claim" else sec), t
            elif isinstance(v, dict):
                yield from _output_texts(v)
            elif isinstance(v, str) and k in ("one_liner", "text", "anti_score_summary"):
                yield k, v


def check_trace(trace: list[dict] | None, body: Any, sources: dict[str, SourceInfo] | None = None,
                *, kind: str | None = None, cited: set[str] | None = None) -> list[Flag]:
    """Injection, tool, fallback and guard flags from the run trace (plus injection in cited source content)."""
    flags: list[Flag] = []
    seen_inj: set[str] = set()

    def inj(url: str | None, sid: str | None, phrases: list[str]) -> None:
        key = url or sid or ""
        if key in seen_inj:
            return
        seen_inj.add(key)
        flags.append(Flag(rule="injection_in_sources", category=C.PROMPT_INJECTION_ATTEMPT, severity=S.INFO,
                          citation=sid, detail=f"prompt-injection patterns in source {sid or ''} {url or ''}: "
                                               f"{'; '.join(phrases[:3])} (treated as data)".replace("  ", " ")))

    for t in trace or []:
        if not isinstance(t, dict):
            continue
        typ = t.get("type")
        if typ in ("step", "injection_flags") and t.get("flags"):
            for f in t["flags"]:
                if isinstance(f, dict):
                    inj(f.get("url"), f.get("id"), [str(p) for p in f.get("phrases") or []])
        elif typ == "tool_result" and t.get("is_error"):
            flags.append(Flag(rule="tool_error", category=C.TOOL_FAILURE, severity=S.LOW,
                              detail=f"tool {t.get('tool')} failed: {_short(str(t.get('summary') or ''), 200)}"))
        elif typ == "error":
            flags.append(Flag(rule="run_error", category=C.TOOL_FAILURE, severity=S.HIGH,
                              detail=_short(str(t.get("message") or "run error"), 300)))
        elif typ == "validation":
            invalid = [str(x) for x in t.get("invalid_ids") or []]
            if invalid:
                flags.append(Flag(rule="guard_removed_invalid_ids", category=C.CITATION_MISMATCH, severity=S.MEDIUM,
                                  detail=f"citation guard removed nonexistent source ids before delivery: "
                                         f"{', '.join(invalid[:10])}"))
            if int(t.get("downgraded") or 0) > 0:
                flags.append(Flag(rule="guard_downgraded_fact", category=C.UNSUPPORTED_CLAIM, severity=S.LOW,
                                  detail=f"citation guard downgraded {int(t['downgraded'])} uncited FACT(s) to "
                                         "INFERENCE (unverified) before delivery"))
    fell_back, reason = fallback_info(trace, kind)
    if fell_back:
        low = (reason or "").lower()
        cat = C.STRUCTURED_OUTPUT_FAILURE if any(h in low for h in _STRUCTURED_HINTS) else C.TOOL_FAILURE
        flags.append(Flag(rule="llm_fallback", category=cat, severity=S.MEDIUM,
                          detail=f"LLM attempted but replaced by the deterministic template: {_short(reason or '', 200)}"))
    if sources:
        for sid in sorted(cited if cited is not None else sources):
            s = sources.get(sid)
            if s is None or (s.url or "").startswith("radar://") or not s.content:
                continue
            hits = detect_injection(f"{s.title or ''}\n{s.content}")
            if hits:
                inj(s.url, sid, hits)
    for sec, text in _output_texts(body):
        hits = detect_injection(text)
        if hits:
            flags.append(Flag(rule="output_follows_injection", category=C.PROMPT_INJECTION_ATTEMPT, severity=S.HIGH,
                              section=sec, claim_text=_short(text),
                              detail=f"output text contains injection patterns: {'; '.join(hits[:3])}"))
    return flags


# ── diligence ────────────────────────────────────────────────────────────────

NO_EVIDENCE_STATUSES = frozenset({"UNVERIFIED", "UNTESTABLE", "NOT_TESTABLE", "COMPANY_CLAIM", ""})


def _independent(items: Any) -> list[dict]:
    return [e for e in items or [] if isinstance(e, dict) and not e.get("gap")
            and str(e.get("publisher") or "").lower() not in ("company", "")]


def _cited_source_ids(cl: dict) -> set[str]:
    """S-ids a diligence claim cites: its own source and every evidence item's (gaps cite nothing)."""
    ids = {str(cl.get("source_id")).upper()} if cl.get("source_id") else set()
    for key in ("company_evidence", "independent_evidence", "contradictory_evidence", "evidence"):
        for e in cl.get(key) or []:
            if isinstance(e, dict) and e.get("source_id") and not e.get("gap"):
                ids.add(str(e["source_id"]).upper())
    return ids


def check_diligence(body: dict) -> CheckResult:
    """Structural rules for a diligence report (claims with a verification status)."""
    res = CheckResult()
    for i, cl in enumerate(c for c in (body or {}).get("claims") or [] if isinstance(c, dict)):
        text = str(cl.get("claim_text") or cl.get("text") or "")
        status = str(cl.get("verification_status") or cl.get("status") or "").upper()
        url = cl.get("source_url")
        quote = cl.get("source_quote") or cl.get("quote")
        ids = _cited_source_ids(cl)
        res.cited_ids.update(ids)
        res.claims.append(ClaimRef(section="claims", text=text, epistemic=cl.get("epistemic") or "COMPANY_CLAIM",
                                   source_ids=sorted(ids)))
        if url:
            res.citations_total += 1
        if not url or not quote:
            res.unsupported.add(i)
            res.flags.append(Flag(rule="diligence_claim_unsourced", category=C.UNSUPPORTED_CLAIM, severity=S.HIGH,
                                  section="claims", claim_text=_short(text),
                                  detail="extracted claim without a source url or quote"))
        if status not in NO_EVIDENCE_STATUSES:
            indep = (_independent(cl.get("independent_evidence")) or _independent(cl.get("evidence"))
                     or _independent(cl.get("contradictory_evidence")))
            if not indep:
                res.unsupported.add(i)
                res.flags.append(Flag(rule="diligence_status_without_independent_evidence",
                                      category=C.UNSUPPORTED_CLAIM, severity=S.HIGH, section="claims",
                                      claim_text=_short(text),
                                      detail=f"status {status} without any independent (non-company) evidence",
                                      needs_human_review=True))
    return res


def _is_diligence_body(body: Any) -> bool:
    claims = body.get("claims") if isinstance(body, dict) else None
    return isinstance(claims, list) and any(isinstance(c, dict) and ("verification_status" in c or "status" in c)
                                            for c in claims)


# ── dispatch ─────────────────────────────────────────────────────────────────


def _subject_domain(session: Any, report: Any) -> str | None:
    if session is None or getattr(report, "entity_type", None) != "company" or not getattr(report, "entity_id", None):
        return None
    from radar.models import Company

    c = session.get(Company, report.entity_id)
    return domain_of(c.website) if c is not None and c.website else None


def check_report(session: Any, report: Any) -> CheckResult:
    """All checks for one stored report, dispatched by kind."""
    kind = getattr(report, "kind", None)
    body = getattr(report, "body", None) or {}
    trace = getattr(report, "trace", None) or []
    if kind == "diligence" or (kind not in ("brief", "memo", "meeting_brief", "morning_brief")
                               and _is_diligence_body(body)):
        res = check_diligence(body)
        res.flags += check_trace(trace, {}, None, kind=kind)
        return res
    if kind == "morning_brief":
        res = CheckResult(has_claim_schema=False)
        res.flags += check_trace(trace, body, None, kind=kind)
        return res
    sources = resolve_sources(session, report)
    res = check_claims(list(iter_claims(body)), sources, report_at=report_time(report),
                       subject_domain=_subject_domain(session, report))
    res.flags += check_trace(trace, body, sources, kind=kind, cited=res.cited_ids)
    return res
