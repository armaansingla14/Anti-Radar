"""USAspending.gov federal awards (V4 Portfolio Pulse).

The official public API of the U.S. Treasury (no key). Queried only for configured portfolio companies and their
comps, by the recipient name written in ``config/anti_fund_portfolio.yaml``.

``recipient_search_text`` is a *search*: it returns every recipient whose name contains the text. Rows are kept only
when the recipient name equals the configured name after normalization (case, punctuation, legal suffixes such as
INC / LLC / CORP). Never fuzzy: "ACME ROBOTICS SOLUTIONS LLC" is not "Acme Robotics, Inc.".

Contracts (award types A-D) and grants (02-05) are separate award groups in the API and are queried separately.
Each kept row becomes a FACT ``federal_award`` signal dated by the award's start date.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from radar import clock
from radar.providers.base import EntityRef, NormalizedSignal, ProviderBatch
from radar.providers.http import HttpClient, content_hash

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AWARD_URL = "https://www.usaspending.gov/award/{}"
SOURCE = "usaspending"
SIGNAL_TYPE = "federal_award"
AWARD_GROUPS: dict[str, list[str]] = {"contracts": ["A", "B", "C", "D"], "grants": ["02", "03", "04", "05"]}
FIELDS = ["Award ID", "Recipient Name", "Start Date", "End Date", "Award Amount", "Awarding Agency",
          "Awarding Sub Agency", "Description", "generated_internal_id", "Last Modified Date"]
PAGE_LIMIT = 100
MAX_PAGES = 3
DEFAULT_DAYS = 730

_LEGAL_SUFFIXES = ("LLC", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "LTD", "LIMITED", "LP", "PBC", "PLLC")


def normalize_recipient(name: str | None) -> str:
    """'The Acme Robotics, Inc.' -> 'ACME ROBOTICS'. Upper case, punctuation removed, spaces collapsed, a leading
    'THE' and trailing legal suffixes stripped."""
    s = re.sub(r"[^A-Z0-9& ]+", " ", (name or "").upper().replace("&", " & "))
    tokens = s.split()
    if len(tokens) > 1 and tokens[0] == "THE":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _date(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
        except ValueError:
            return None
    if len(s) <= 10:
        d = datetime.combine(d.date(), datetime.min.time())
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _amount(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _money(x: float | None) -> str:
    if x is None:
        return "amount not reported"
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f}M"
    return f"${x:,.0f}"


def _clean(v: Any, limit: int | None = None) -> str | None:
    if not isinstance(v, str):
        return None
    s = re.sub(r"\s+", " ", v).strip()
    return (s[:limit] if limit else s) or None


def parse_awards(payload: dict[str, Any], recipient: str, ref: EntityRef, *, award_group: str
                 ) -> tuple[list[NormalizedSignal], int, list[dict[str, Any]]]:
    """(signals, rejected near-name rows, sources) for one API page. Only exact (normalized) recipient matches."""
    want = normalize_recipient(recipient)
    signals: list[NormalizedSignal] = []
    sources: list[dict[str, Any]] = []
    rejected = 0
    kind = "contract" if award_group == "contracts" else "grant"
    for row in (payload or {}).get("results") or []:
        if not isinstance(row, dict):
            continue
        name = _clean(row.get("Recipient Name"))
        if not want or normalize_recipient(name) != want:
            rejected += 1
            continue
        gid = _clean(row.get("generated_internal_id"))
        award_id = _clean(row.get("Award ID"))
        start, modified = _date(row.get("Start Date")), _date(row.get("Last Modified Date"))
        when = start or modified
        if not gid or when is None:
            rejected += 1
            continue
        end = _date(row.get("End Date"))
        amount = _amount(row.get("Award Amount"))
        agency = _clean(row.get("Awarding Agency"))
        sub = _clean(row.get("Awarding Sub Agency"))
        desc = _clean(row.get("Description"))
        title = f"{agency}: {desc[:140]}" if agency and desc else f"Federal {kind} award {award_id or gid}"
        url = AWARD_URL.format(gid)
        signals.append(NormalizedSignal(
            entity=ref,
            signal_type=SIGNAL_TYPE,
            source_type=SOURCE,
            source_url=url,
            title=title,
            description=f"Federal {kind} to {name} from {agency or 'an unnamed agency'}"
                        f"{f' ({sub})' if sub and sub != agency else ''}: {_money(amount)}."
                        + (f" {desc[:300]}" if desc else ""),
            raw_value=amount,
            event_at=when,
            confidence=1.0,
            epistemic="FACT",
            metadata={"award_id": award_id, "award_group": award_group, "amount": amount, "agency": agency,
                      "sub_agency": sub, "start_date": start.date().isoformat() if start else None,
                      "end_date": end.date().isoformat() if end else None,
                      "recipient_name": name, "generated_internal_id": gid,
                      "date_basis": "start_date" if start else "last_modified_date"},
        ))
        snippet = f"{agency or 'Federal agency'} · {_money(amount)} · {desc or ''}".strip(" ·")
        sources.append({"url": url, "source_type": SOURCE, "title": title, "snippet": snippet[:300],
                        "content_hash": content_hash(f"{gid}|{amount}|{when.isoformat()}|{desc or ''}")})
    return signals, rejected, sources


def request_body(recipient: str, award_group: str, *, start: date, end: date, page: int = 1) -> dict[str, Any]:
    return {
        "filters": {
            "recipient_search_text": [recipient],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
            "award_type_codes": AWARD_GROUPS[award_group],
        },
        "fields": FIELDS,
        "page": page,
        "limit": PAGE_LIMIT,
        "sort": "Award Amount",
        "order": "desc",
    }


class USAspendingProvider:
    name = SOURCE

    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(guard_ssrf=True, min_interval=0.5, timeout=30.0, max_retries=2)

    def enabled(self) -> tuple[bool, str]:
        return True, "USAspending.gov public API (no key); exact recipient-name matches only"

    def fetch_awards(self, recipient: str, ref: EntityRef, *, days: int = DEFAULT_DAYS) -> ProviderBatch:
        """Contracts and grants for ``recipient`` over the last ``days`` (data clock)."""
        batch = ProviderBatch(provider=SOURCE)
        recipient = (recipient or "").strip()
        if not normalize_recipient(recipient):
            batch.errors.append("empty recipient name")
            return batch
        end = clock.today()
        start = end - timedelta(days=days)
        seen: set[str] = set()
        for group in AWARD_GROUPS:
            for page in range(1, MAX_PAGES + 1):
                try:
                    data = self.client.post_json(SEARCH_URL, request_body(recipient, group, start=start, end=end,
                                                                          page=page))
                except Exception as e:  # noqa: BLE001 — recorded; an outage is not "no awards"
                    batch.errors.append(f"usaspending {group} page {page} for {recipient!r}: {type(e).__name__}")
                    break
                if not isinstance(data, dict):
                    batch.errors.append(f"usaspending {group} page {page}: unexpected response")
                    break
                signals, rejected, sources = parse_awards(data, recipient, ref, award_group=group)
                batch.rejected += rejected
                for sig, src in zip(signals, sources, strict=True):
                    if sig.source_url in seen:
                        continue
                    seen.add(sig.source_url or "")
                    batch.signals.append(sig)
                    batch.sources.append(src)
                if not ((data.get("page_metadata") or {}).get("hasNext")):
                    break
        return batch
