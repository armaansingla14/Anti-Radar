"""Claim extraction: material, concrete statements a company makes about itself.

Rules first (deterministic): segment company-published text into sentences (markdown code, badges, tables and
HTML stripped), classify each by ordered cue patterns, keep the concrete and specific ones, merge
near-duplicates (the strongest source wins; every source is remembered), cap the ledger.

``source_quote`` is always a verbatim substring of the artifact text, so a reader can find it; ``text`` is the
same sentence with markdown removed. Sentences flagged as prompt injection are never claims.

The optional LLM pass (:func:`extract_with_llm`) may only *add* claims, and only those whose quote is found
verbatim in the cited artifact; it never assigns a status.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from radar.diligence.taxonomy import ClaimType, Publisher, Relation, Strength
from radar.diligence.types import ArtifactDraft, ClaimDraft, evidence_item, norm, tokens
from radar.diligence.vocab import find_terms
from radar.llm.guard import SYSTEM_GUARD, detect_injection, wrap_source

log = logging.getLogger(__name__)

MAX_CLAIMS = 12
MAX_PER_TYPE = 4
MIN_WORDS, MAX_WORDS = 5, 60
DUPLICATE_JACCARD = 0.6
SOURCE_KINDS = ("web_page", "readme", "doc", "hn_story")

_N = r"\d+(?:[.,]\d+)?"
# Ordered: the first type whose cue matches wins.
CUES: tuple[tuple[ClaimType, re.Pattern[str]], ...] = tuple(
    (t, re.compile(p, re.IGNORECASE)) for t, p in (
        (ClaimType.PERFORMANCE,
         rf"\b{_N}\s?(?:x|×)\s*(?:faster|lower|less|more|cheaper|higher|smaller|speed-?ups?|throughput|better|the throughput)"
         rf"|\b{_N}\s?%\s+(?:less|lower|faster|fewer|smaller|higher|more|reduction|improvement|cheaper|speed-?up)"
         r"|\bfaster than\b|\boutperform(?:s|ed|ing)?\b|\bstate[- ]of[- ]the[- ]art\b|\bSOTA\b"
         rf"|\b{_N}\s?(?:ms|tokens?/s(?:ec)?|tok/s|req(?:uests)?/s|QPS|GB of VRAM)\b"
         rf"|\b(?:latency|throughput|tokens per second|time[- ]to[- ]first[- ]token|TTFT|VRAM|memory (?:usage|footprint))\b(?=.*\d)"),
        (ClaimType.SECURITY,
         r"\bSOC ?2\b|\bHIPAA\b|\bGDPR\b|\bISO ?27001\b|\bencrypt(?:ed|ion|s)?\b|\bzero[- ]trust\b|\bair[- ]gapped\b"
         r"|\bdata never leaves\b|\bon[- ]prem(?:ise|ises)?\b|\bself[- ]host(?:ed|able)?\b|\bSSO\b|\bRBAC\b"
         r"|\baudit logs?\b|\bsecure\b|\bprivacy\b"),
        (ClaimType.OPEN_SOURCE,
         r"\bopen[- ]source[ds]?\b|\bopen[- ]weights?\b|\bApache(?:[- ]2(?:\.0)?| License)\b|\bMIT[- ]licen[sc]ed?\b"
         r"|\bMIT License\b|\b(?:BSD|GPL|AGPL|LGPL|MPL)(?:-?v?\d(?:\.\d)?)?\b(?=.*licen[sc])|\bsource[- ]available\b"
         r"|\blicen[sc]ed under\b|\bactively maintained\b|\bactive development\b"),
        (ClaimType.CUSTOMER_TRACTION,
         rf"\b{_N}\s?(?:k|m|b|thousand|million|billion)?\+?\s+(?:monthly |weekly |daily |active |GitHub |paying |production )?"
         r"(?:users|customers|companies|teams|developers|downloads|installs|stars|contributors|organi[sz]ations|orgs"
         r"|enterprises|businesses|deployments|startups)\b"
         r"|\b(?:used|trusted|loved|relied on|adopted|deployed) by\b|\bin production at\b|\bFortune \d+\b"),
        (ClaimType.ECONOMICS,
         rf"\$\s?{_N}|\bcosts?\b|\bcheaper\b|\bpric(?:e|es|ing)\b|\bTCO\b|\bper (?:million|1M|token|hour|GPU[- ]hour)\b"
         r"|\bsav(?:e|es|ings)\b(?=[^.]{0,40}(?:\$|%|\bcosts?\b|\bmoney\b|\bspend|\bhours?\b|\btime\b))"
         r"|\bfree tier\b"),
        (ClaimType.SCALABILITY,
         r"\bscal(?:e|es|able|ability|ing)\b|\bdistributed\b|\bmulti[- ]?(?:GPU|node)s?\b|\bthousands of\b"
         r"|\bmillions? of\b|\bbillions? of\b|\bhorizontal(?:ly)?\b|\bclusters?\b|\btensor[- ]parallel"),
        (ClaimType.TECHNICAL_CAPABILITY,
         r"\b(?:supports?|supported|support for|compatible with|works with|runs? on|running on|integrates? with"
         r"|deploy(?:s|ed)? (?:on|to)|available on|optimi[sz]ed for|native(?:ly)? (?:on|for)|out of the box)\b"
         r"|\bmulti[- ]cloud\b|\bany cloud\b|\bcloud[- ]agnostic\b"),
        (ClaimType.MARKET,
         r"\b(?:fastest|leading|first|only|#1|number one|largest|best|most (?:popular|advanced|efficient|powerful))\b"
         r"|\bindustry[- ]leading\b|\bworld'?s\b"),
        (ClaimType.ARCHITECTURE,
         r"\bbuilt (?:on|with|in|from)\b|\bwritten in\b|\bkernels?\b|\barchitecture\b|\bengine\b|\bcompiler\b"
         r"|\bruntime\b|\bfrom scratch\b|\bimplemented in\b|\bpowered by\b|\bzero-copy\b|\bpaged ?attention\b"
         r"|\bspeculative decoding\b|\bquantization\b|\bcontinuous batching\b"),
        (ClaimType.PRODUCT,
         r"\b(?:lets? you|enables?|allows?|provides?|offers?|includes?|comes with|one[- ]command|one[- ]line"
         r"|drop[- ]in|API|SDK|CLI|dashboard|automatically|in minutes|production[- ](?:ready|grade)|stable"
         r"|battle[- ]tested|enterprise[- ]ready)\b"),
    )
)

TYPE_WEIGHT: dict[str, float] = {
    ClaimType.PERFORMANCE: 3.0, ClaimType.TECHNICAL_CAPABILITY: 2.5, ClaimType.OPEN_SOURCE: 2.5,
    ClaimType.CUSTOMER_TRACTION: 2.5, ClaimType.SECURITY: 2.2, ClaimType.SCALABILITY: 2.0,
    ClaimType.ECONOMICS: 2.0, ClaimType.ARCHITECTURE: 1.8, ClaimType.MARKET: 1.0, ClaimType.PRODUCT: 1.0,
}
TYPE_ORDER = tuple(t.value for t in ClaimType)

_BOILERPLATE = re.compile(
    r"\b(?:cookies?|privacy policy|terms of (?:service|use)|all rights reserved|subscribe|newsletter|sign up|sign in"
    r"|log in|copyright|click here|learn more|read more|get started|contact (?:us|sales)|book a demo|request a demo"
    r"|join (?:our|the) (?:discord|slack|community)|star us|follow us|we'?re hiring|careers)\b|©",
    re.IGNORECASE,
)
# UI / setup instructions ("Pick the build…", "Click Install") describe how to use the product, not what it claims.
_INSTRUCTION = re.compile(r"^\W*(?:pick|click|select|choose|press|tap|open|enter|type|navigate|go to|copy|paste|"
                          r"download|scroll|drag|toggle|set|make sure|ensure you|note:?)\b", re.IGNORECASE)
_QUANT = re.compile(rf"\b{_N}\s?(?:x|×|%|ms|k\b|m\b|b\b|\+|tokens|GB|TB|tok/s)|\$\s?\d", re.IGNORECASE)
_MATURITY = re.compile(r"\bproduction[- ](?:ready|grade)\b|\bbattle[- ]tested\b|\benterprise[- ]ready\b", re.I)
_FIGURE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s?(x|×|%)(?![A-Za-z0-9])", re.IGNORECASE)
_ROADMAP = re.compile(r"\b(?:coming soon|roadmap|will (?:soon )?(?:support|add|ship)|planned)\b", re.IGNORECASE)

# ── segmentation ─────────────────────────────────────────────────────────────

_FENCE = re.compile(r"^\s*(```|~~~)")
_LEAD = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)*")
_ABBREV = re.compile(r"(?:\be\.g|\bi\.e|\bvs|\betc|\bInc|\bapprox|\bv\d+(?:\.\d+)*|\bNo|\bFig|\bDr|\bMr|\bMs)\.$",
                     re.IGNORECASE)
_BOUNDARY = re.compile(r"[.!?]+[\"')\]]*\s+(?=[A-Z0-9\"“`\[(*_])")


def _line_spans(text: str, markdown: bool) -> Iterable[tuple[int, int]]:
    """(start, end) of each line's content, skipping code, tables and markup-only lines."""
    in_fence = False
    prev_blank = True
    in_indented = False
    pos = 0
    for line in text.splitlines(keepends=True):
        start, raw = pos, line.rstrip("\r\n")
        pos += len(line)
        if markdown and _FENCE.match(raw):
            in_fence = not in_fence
            prev_blank = False
            continue
        if in_fence:
            continue
        if not raw.strip():
            prev_blank, in_indented = True, False
            continue
        if markdown and (raw.startswith("\t") or raw.startswith("    ")) and (prev_blank or in_indented):
            in_indented, prev_blank = True, False
            continue
        prev_blank, in_indented = False, False
        stripped = raw.strip()
        if stripped.startswith("|") or stripped.count(" | ") >= 2 or re.fullmatch(r"[\s|:\-=+]+", stripped):
            continue
        lead = _LEAD.match(raw)
        cs = start + (lead.end() if lead else 0)
        yield cs, start + len(raw)


def _sentence_spans(text: str, start: int, end: int) -> Iterable[tuple[int, int]]:
    seg = text[start:end]
    cur = 0
    for m in _BOUNDARY.finditer(seg):
        cut = m.start() + len(m.group(0).rstrip())
        if _ABBREV.search(seg[cur:m.start() + 1]):
            continue
        yield start + cur, start + cut
        cur = m.end()
    if cur < len(seg):
        yield start + cur, end


_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_EMPTY_LINK = re.compile(r"\[\s*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]{1,300}>")


def clean_inline(raw: str) -> str:
    """Markdown/HTML inline markup removed; link text kept."""
    s = _IMG.sub("", raw)
    s = _EMPTY_LINK.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _TAG.sub("", s)
    s = s.replace("`", "").replace("**", "").replace("__", "")
    s = re.sub(r"(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)", "", s)
    return norm(s).strip(" -–—:;")


_HEADING = re.compile(r"[ \t]*#{1,6}[ \t]+(.+)")
_NON_CLAIM_SECTION = re.compile(r"(?i)\b(acknowledg|credits?\b|thanks|thank you|citations?\b|cite\b|licen[cs]e|"
                                r"contributors?\b|sponsors?\b|backers\b)")


def sentences(art: ArtifactDraft) -> list[tuple[int, str, str]]:
    """[(offset, raw quote, clean text)] for every candidate sentence in the artifact."""
    text = art.text or ""
    markdown = art.kind in ("readme", "doc")
    out = []
    skipping = False
    for ls, le in _line_spans(text, markdown):
        line_start = text.rfind("\n", 0, ls) + 1  # spans start after "## " markers; headings are judged on the raw line
        heading = _HEADING.match(text[line_start:le]) if markdown else None
        if heading:
            # Credits, citations and licence sections describe other people's work, not the company's claims.
            skipping = bool(_NON_CLAIM_SECTION.search(heading.group(1)))
            continue
        if skipping:
            continue
        for s, e in _sentence_spans(text, ls, le):
            raw = text[s:e].strip()
            if not raw or len(raw) > 600:
                continue
            off = s + (len(text[s:e]) - len(text[s:e].lstrip()))
            clean = clean_inline(raw)
            if sum(ch.isalpha() for ch in clean) < 12:
                continue
            out.append((off, raw, clean))
    return out


# ── classification ───────────────────────────────────────────────────────────


def classify(text: str) -> ClaimType | None:
    for ctype, pat in CUES:
        if pat.search(text):
            if ctype == ClaimType.TECHNICAL_CAPABILITY and not (
                find_terms(text, ("hardware", "os", "framework", "model", "cloud", "scale"))
                or re.search(r"multi[- ]cloud|any cloud|cloud[- ]agnostic", text, re.I)
            ):
                continue  # "supports" without a concrete target is not a capability claim
            return ctype
    return None


def materiality(clean: str, ctype: ClaimType, source_rank: int) -> float | None:
    """Score a candidate (None = not material: vague, boilerplate, a question, or the wrong length)."""
    words = clean.split()
    if not (MIN_WORDS <= len(words) <= MAX_WORDS) or clean.rstrip().endswith("?") or _BOILERPLATE.search(clean) \
            or _INSTRUCTION.match(clean):
        return None
    terms = find_terms(clean)
    has_num = bool(re.search(r"\d", clean))
    proper = bool(re.search(r"\s[A-Z][A-Za-z0-9]*[A-Z0-9][A-Za-z0-9]*|\s[A-Z][a-z]+[A-Z]", clean))  # vLLM, PyTorch
    if not (has_num or terms or proper or ctype in (ClaimType.OPEN_SOURCE, ClaimType.SECURITY)):
        return None
    score = TYPE_WEIGHT[ctype]
    if _QUANT.search(clean):
        score += 1.5
    score += 0.5 * min(len(terms), 3)
    score += max(0.0, 0.6 - 0.15 * source_rank)
    if _MATURITY.search(clean):
        score += 1.0  # maturity claims are what production buyers rely on
    if _ROADMAP.search(clean):
        score -= 1.0
    return round(score, 3)


def source_rank(art: ArtifactDraft) -> int:
    """Where a claim is most authoritative: homepage > benchmark/docs pages > other pages > README > docs > HN."""
    if art.kind == "web_page":
        kind = (art.data or {}).get("page_kind")
        return 0 if kind == "home" else 1 if kind in ("benchmarks", "docs", "product") else 2
    return {"readme": 3, "doc": 4, "hn_story": 5}.get(art.kind, 6)


def _company_sources(artifacts: Iterable[ArtifactDraft]) -> list[ArtifactDraft]:
    arts = [a for a in artifacts if a.publisher == Publisher.COMPANY and a.kind in SOURCE_KINDS and a.text]
    return sorted(arts, key=lambda a: (source_rank(a), a.key))


def figures(text: str) -> frozenset[str]:
    """Quantitative figures ('3x', '60%') — two claims of one type stating the same figures are one claim."""
    return frozenset(f"{float(n):g}{'x' if u.lower() in ('x', '×') else '%'}" for n, u in _FIGURE.findall(text))


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _containment(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _same_claim(c: ClaimDraft, k: ClaimDraft, toks: set[str], ktoks: set[str]) -> bool:
    """Two candidates are one claim when their wording overlaps strongly, one contains the other, they state the
    same figures, or (same type) they name the same set of concrete targets — e.g. the homepage and the docs both
    listing "MacOS, Linux, Windows, NVIDIA, AMD, Intel and CPU"."""
    if _jaccard(toks, ktoks) >= DUPLICATE_JACCARD:
        return True
    a, b = norm(c.text).lower(), norm(k.text).lower()
    if a in b or b in a:
        return True
    if k.claim_type != c.claim_type:
        return False
    figs = figures(c.text)
    if figs and figures(k.text) == figs:
        return True
    terms = {t.name for t in find_terms(c.text)}
    if len(terms) >= 2 and terms == {t.name for t in find_terms(k.text)}:
        return True
    return _containment(toks, ktoks) >= 0.75 and min(len(toks), len(ktoks)) >= 4


def _company_ev(art: ArtifactDraft, quote: str) -> dict:
    return evidence_item(art, relation=Relation.SUPPORTS, strength=Strength.INDIRECT, matcher="extraction",
                         note="The company states this.", quote=quote)


def candidates(artifacts: Iterable[ArtifactDraft]) -> list[ClaimDraft]:
    out: list[ClaimDraft] = []
    for art in _company_sources(artifacts):
        rank = source_rank(art)
        for off, raw, clean in sentences(art):
            if detect_injection(raw):
                continue
            ctype = classify(clean)
            if ctype is None:
                continue
            score = materiality(clean, ctype, rank)
            if score is None:
                continue
            out.append(ClaimDraft(
                text=clean, claim_type=ctype.value, source_key=art.key, source_url=art.url, source_quote=raw,
                observed_at=art.fetched_at, materiality=score, position=off, source_rank=rank,
                company_evidence=[_company_ev(art, raw)],
            ))
    return out


def merge_and_rank(cands: list[ClaimDraft], *, cap: int = MAX_CLAIMS) -> list[ClaimDraft]:
    """Near-duplicate merge (strongest source wins), per-type and total caps, deterministic order."""
    ordered = sorted(cands, key=lambda c: (-c.materiality, c.source_rank, c.source_key, c.position))
    kept: list[tuple[ClaimDraft, set[str]]] = []
    for c in ordered:
        toks = tokens(c.text)
        dup = next((k for k, kt in kept if _same_claim(c, k, toks, kt)), None)
        if dup is not None:
            if all(e["artifact_key"] != c.source_key or e["quote"] != c.source_quote for e in dup.company_evidence):
                dup.company_evidence.extend(c.company_evidence)
            continue
        kept.append((c, toks))
    per_type: dict[str, int] = {}
    final: list[ClaimDraft] = []
    for c, _ in kept:
        if per_type.get(c.claim_type, 0) >= MAX_PER_TYPE:
            continue
        per_type[c.claim_type] = per_type.get(c.claim_type, 0) + 1
        final.append(c)
        if len(final) >= cap:
            break
    for i, c in enumerate(final, 1):
        c.ordinal = i
    return final


def extract_claims(artifacts: Iterable[ArtifactDraft], *, cap: int = MAX_CLAIMS) -> list[ClaimDraft]:
    """Rule-based extraction over company-published artifacts."""
    return merge_and_rank(candidates(list(artifacts)), cap=cap)


# ── optional LLM-assisted extraction ─────────────────────────────────────────


class LLMClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Id of the <source> block the claim comes from, e.g. 'S2'.")
    quote: str = Field(description="The exact sentence from that source, copied character for character.")
    text: str = Field(description="The claim restated as one self-contained sentence.")
    claim_type: str = Field(description="One of: " + ", ".join(t.value for t in ClaimType))


class LLMClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[LLMClaim]


EXTRACT_INSTRUCTIONS = """\
List the material technical claims the COMPANY makes about its own product in the sources below: performance \
numbers, supported hardware/frameworks/models, architecture, open-source status, security/compliance, scale, \
economics, traction. Skip marketing filler, navigation, legal text and anything not stated by the company. \
For every claim copy the exact sentence from the source into `quote` (verbatim, no edits) and give its source id. \
Do not judge whether claims are true. Return at most 15 claims."""


def extract_with_llm(provider, artifacts: Iterable[ArtifactDraft], *, max_chars: int = 6000
                     ) -> tuple[list[ClaimDraft], dict]:
    """LLM-proposed claims, kept only when their quote is a verbatim substring of the cited artifact.

    Returns (claims, info) where info = {used, model, rejected, cost_usd, error}. A provider without a live
    model (NullProvider) is skipped. Statuses are never taken from the model."""
    info: dict = {"used": False, "model": None, "rejected": 0, "cost_usd": 0.0, "error": None}
    if provider is None or not getattr(provider, "available", False):
        return [], info
    arts = _company_sources(artifacts)[:8]
    if not arts:
        return [], info
    ids = {f"S{i}": a for i, a in enumerate(arts, 1)}
    blocks = "\n\n".join(
        wrap_source(sid, a.url, a.kind, a.text, max_chars, title=a.title, suspicious=bool(a.flags))
        for sid, a in ids.items()
    )
    try:
        from radar.llm.provider import LLMError

        res = provider.structured("diligence.extract", "standard", SYSTEM_GUARD,
                                  f"{EXTRACT_INSTRUCTIONS}\n\n{blocks}", LLMClaims, max_tokens=4096)
    except LLMError as e:
        info["error"] = f"{type(e).__name__}"
        return [], info
    info.update(used=True, model=res.model, cost_usd=res.cost_usd)
    out: list[ClaimDraft] = []
    for c in res.parsed.claims:
        art = ids.get(c.source_id.strip().upper())
        quote = (c.quote or "").strip()
        if (art is None or c.claim_type not in TYPE_ORDER or len(quote) < 12 or quote not in art.text[:max_chars]
                or detect_injection(quote)):
            info["rejected"] += 1
            continue
        clean = clean_inline(quote)
        rank = source_rank(art)
        score = materiality(clean, ClaimType(c.claim_type), rank)
        if score is None:
            info["rejected"] += 1
            continue
        out.append(ClaimDraft(
            text=norm(c.text) or clean, claim_type=c.claim_type, source_key=art.key, source_url=art.url,
            source_quote=quote, observed_at=art.fetched_at, materiality=score, position=art.text.find(quote),
            source_rank=rank, extracted_by="llm", company_evidence=[_company_ev(art, quote)],
        ))
    return out, info
