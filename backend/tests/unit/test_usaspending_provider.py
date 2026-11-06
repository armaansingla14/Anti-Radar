"""USAspending.gov federal awards: exact recipient matching, parsing, POST requests (no network)."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime

import httpx
import pytest
import respx

from radar.providers.base import domain_ref
from radar.providers.http import HttpClient
from radar.providers.usaspending import (
    SEARCH_URL,
    USAspendingProvider,
    normalize_recipient,
    parse_awards,
    request_body,
)
from tests.conftest import FIXTURES

US = FIXTURES / "usaspending"
REF = domain_ref("acme-robotics.com")


def _payload(name: str) -> dict:
    return json.loads((US / name).read_text())


@pytest.mark.parametrize("a,b,same", [
    ("ACME ROBOTICS, INC.", "Acme Robotics, Inc.", True),
    ("Acme Robotics Inc", "ACME ROBOTICS", True),
    ("The Acme Robotics Corporation", "Acme Robotics", True),
    ("ACME ROBOTICS SOLUTIONS LLC", "Acme Robotics, Inc.", False),
    ("ACME ROBOTIC", "Acme Robotics, Inc.", False),
    ("ACME", "Acme Robotics", False),
])
def test_normalize_recipient_exact_only(a, b, same):
    assert (normalize_recipient(a) == normalize_recipient(b)) is same


def test_parse_contracts_keeps_exact_recipient_only():
    signals, rejected, sources = parse_awards(_payload("contracts.json"), "Acme Robotics, Inc.", REF,
                                              award_group="contracts")
    assert rejected == 2  # "ACME ROBOTICS SOLUTIONS LLC" and "ACME ROBOTIC" are different recipients
    (s,) = signals
    assert s.signal_type == "federal_award" and s.source_type == "usaspending" and s.epistemic == "FACT"
    assert s.source_url == "https://www.usaspending.gov/award/CONT_AWD_FA864926P0123_9700_-NONE-_-NONE-"
    assert s.raw_value == 1_850_000.0 and s.event_at == datetime(2026, 8, 3, tzinfo=UTC)
    assert s.title.startswith("Department of Defense: SBIR PHASE II")
    m = s.metadata
    assert m["award_id"] == "FA864926P0123" and m["award_group"] == "contracts" and m["amount"] == 1_850_000.0
    assert m["sub_agency"] == "Department of the Air Force" and m["end_date"] == "2027-08-02"
    assert m["date_basis"] == "start_date"
    assert sources[0]["url"] == s.source_url and sources[0]["source_type"] == "usaspending"
    assert "$1.9M" in sources[0]["snippet"]


def test_parse_grants_skips_rows_without_dates():
    signals, rejected, _ = parse_awards(_payload("grants.json"), "Acme Robotics, Inc.", REF, award_group="grants")
    assert [s.metadata["award_id"] for s in signals] == ["2412345"]
    assert rejected == 1 and signals[0].event_at == datetime(2025, 12, 15, tzinfo=UTC)
    assert "grant" in signals[0].description


def test_request_body_separates_award_groups():
    from datetime import date

    b = request_body("Acme Robotics, Inc.", "contracts", start=date(2024, 10, 6), end=date(2026, 10, 6))
    assert b["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert b["filters"]["recipient_search_text"] == ["Acme Robotics, Inc."]
    assert b["filters"]["time_period"] == [{"start_date": "2024-10-06", "end_date": "2026-10-06"}]
    assert request_body("x", "grants", start=date(2024, 1, 1), end=date(2025, 1, 1))["filters"][
        "award_type_codes"] == ["02", "03", "04", "05"]


@pytest.fixture()
def public_dns(monkeypatch):
    def fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@respx.mock
def test_fetch_awards_posts_both_groups(public_dns):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        name = "contracts.json" if body["filters"]["award_type_codes"][0] == "A" else "grants.json"
        return httpx.Response(200, json=_payload(name))

    route = respx.post(SEARCH_URL).mock(side_effect=handler)
    prov = USAspendingProvider(client=HttpClient(guard_ssrf=True, max_retries=0))
    batch = prov.fetch_awards("Acme Robotics, Inc.", REF)
    assert route.call_count == 2 and [b["page"] for b in bodies] == [1, 1]
    assert batch.provider == "usaspending" and batch.rejected == 3
    assert sorted(s.metadata["award_id"] for s in batch.signals) == ["2412345", "FA864926P0123"]
    assert len(batch.sources) == 2 and not batch.errors


@respx.mock
def test_fetch_awards_records_errors(public_dns):
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(500))
    prov = USAspendingProvider(client=HttpClient(guard_ssrf=True, max_retries=0))
    batch = prov.fetch_awards("Acme Robotics, Inc.", REF)
    assert not batch.signals and len(batch.errors) == 2  # an outage is recorded, never read as "no awards"


def test_fetch_awards_empty_recipient():
    batch = USAspendingProvider(client=HttpClient(guard_ssrf=True, max_retries=0)).fetch_awards("  ", REF)
    assert batch.errors == ["empty recipient name"]
