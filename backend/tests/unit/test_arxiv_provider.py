"""arXiv provider parsing against a real recorded cs.RO Atom response (60 entries, 2026-10-03..05)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest

from radar.providers.arxiv import ArxivProvider, enrich, extract_project_urls, parse_feed
from radar.providers.base import arxiv_author_ref, paper_ref, repo_ref
from tests.conftest import FIXTURES

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
XML = (FIXTURES / "arxiv" / "cs_ro_recent.xml").read_text()


def _entries(xml: str) -> list[str]:
    return re.findall(r"<entry>.*?</entry>", xml, re.DOTALL)


def _with_entries(entries: list[str]) -> str:
    head = XML[: XML.index("<entry>")]
    return head + "\n".join(entries) + "\n</feed>"


@pytest.fixture(scope="module")
def batch():
    return enrich(parse_feed(XML, window_days=21, now=NOW), window_days=21, now=NOW)


def test_parses_all_papers_and_strips_versions(batch):
    assert len(batch.papers) == 60
    assert batch.rejected == 0 and batch.errors == []
    p = next(p for p in batch.papers if p.arxiv_id == "2610.06850")
    assert p.ref == paper_ref("2610.06850v1")
    assert p.url == "https://arxiv.org/abs/2610.06850"
    assert (
        p.title == "InterMimicGen: Scaling Humanoid Loco-Manipulation through Self-Evolving Motion Imitation"
    )
    assert p.primary_category == "cs.RO"
    assert p.published_at == datetime(2026, 10, 5, 17, 59, 50, tzinfo=UTC)
    assert all(not re.search(r"v\d+$", p.arxiv_id) for p in batch.papers)
    assert all("  " not in p.title and "\n" not in p.title for p in batch.papers)


def test_code_links_from_comment_and_latex_href(batch):
    by_id = {p.arxiv_id: p for p in batch.papers}
    # Comment: "Project page: https://affordcraft.github.io Code: https://github.com/AffordCraft/AffordCraft"
    assert by_id["2610.06643"].code_urls == ["https://github.com/AffordCraft/AffordCraft"]
    assert by_id["2610.06643"].attrs["project_urls"] == ["https://affordcraft.github.io"]
    # \href{https://github.com/ModelTC/LightX2V/tree/main/examples/realtimewam}{link} -> repo slug only
    assert by_id["2610.06617"].code_urls == ["https://github.com/ModelTC/LightX2V"]
    # Project page only, no code.
    assert by_id["2610.06850"].code_urls == []
    assert by_id["2610.06850"].attrs["project_urls"] == ["https://sirui-xu.github.io/InterMimicGen"]


def test_repo_stubs_relations_and_signals(batch):
    rref = repo_ref("AffordCraft/AffordCraft")
    repo = next(r for r in batch.repositories if r.ref == rref)
    assert repo.attrs == {"stub": True, "discovered_via": "arxiv"} and repo.owner == "AffordCraft"
    pref = paper_ref("2610.06643")
    assert any(r.kind == "code_for" and r.src == pref and r.dst == rref for r in batch.relations)
    pwc = [s for s in batch.signals if s.signal_type == "paper_with_code" and s.entity == rref]
    assert len(pwc) == 1 and pref in pwc[0].related and "2610.06643" in pwc[0].description
    pub = {s.entity.key: s for s in batch.signals if s.signal_type == "paper_published"}
    assert len(pub) == 60
    assert pub[pref.key].normalized_value == 0.6 and rref in pub[pref.key].related
    assert pub[paper_ref("2610.06850").key].normalized_value == 0.4


def test_authors_affiliations_and_positions(batch):
    p = next(p for p in batch.papers if p.arxiv_id == "2610.06400")
    assert p.authors[0] == {
        "name": "Álvaro Díez",
        "affiliation": "Department of Computer Science and Artificial Intelligence, University of Alicante",
    }
    person = next(x for x in batch.people if x.ref == arxiv_author_ref("Álvaro Díez"))
    assert person.ref.key == "arxiv_author:alvaro diez"
    assert person.current_company.endswith("University of Alicante")
    assert person.attrs["arxiv_affiliation"] == person.current_company
    rel = [r for r in batch.relations if r.kind == "author" and r.dst == p.ref]
    assert [r.attrs["position"] for r in rel] == list(range(len(p.authors)))
    assert len({x.ref.key for x in batch.people}) == len(batch.people)  # one record per author


def test_author_recurrence_is_deterministic(batch):
    rec = {s.entity.key: s for s in batch.signals if s.signal_type == "author_recurrence"}
    s = rec["arxiv_author:alvaro diez"]
    assert s.metadata["paper_ids"] == ["2610.06327", "2610.06400"]
    assert s.title == "Álvaro Díez: 2 papers in 1 day"
    assert s.entity.type == "person" and s.confidence < 1
    assert {r.key for r in s.related} == {"arxiv:2610.06327", "arxiv:2610.06400"}
    again = enrich(parse_feed(XML, window_days=21, now=NOW), window_days=21, now=NOW)
    assert [x.model_dump() for x in again.signals if x.signal_type == "author_recurrence"] == [
        x.model_dump() for x in batch.signals if x.signal_type == "author_recurrence"
    ]
    # Single-paper authors never recur.
    assert "arxiv_author:sirui xu" not in rec


def test_recurrence_respects_window():
    later = datetime(2026, 10, 30, tzinfo=UTC)  # all fixture papers are > 21 days old by then
    b = enrich(parse_feed(XML, now=later), window_days=21, now=later)
    assert len(b.papers) == 60
    assert not [s for s in b.signals if s.signal_type == "author_recurrence"]


def test_window_drops_old_papers():
    assert parse_feed(XML, window_days=1, now=datetime(2026, 10, 6, 6, tzinfo=UTC)).papers
    assert parse_feed(XML, window_days=1, now=datetime(2026, 10, 9, tzinfo=UTC)).papers == []


def test_duplicates_skipped_and_malformed_rejected():
    entries = _entries(XML)[:3]
    broken_id = re.sub(r"<id>[^<]+</id>", "<id>http://arxiv.org/abs/not-an-id</id>", entries[1], count=1)
    no_title = re.sub(r"<title>.*?</title>", "<title> </title>", entries[2], count=1, flags=re.DOTALL)
    b = parse_feed(_with_entries([entries[0], entries[0], broken_id, no_title]), now=NOW)
    assert [p.arxiv_id for p in b.papers] == ["2610.06850"]
    assert b.rejected == 2


def test_api_error_entry_reported():
    err = (
        "<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_1234</id><title>Error</title>"
        "<summary>incorrect id format for 1234</summary><updated>2026-10-06T00:00:00-04:00</updated></entry>"
    )
    b = parse_feed(_with_entries([err]), now=NOW)
    assert b.papers == [] and b.errors == ["arxiv error: incorrect id format for 1234"]


def test_extract_project_urls_filters_github_and_arxiv():
    text = (
        r"Code: https://github.com/a/b. Page \url{https://proj.example.org/x}, see https://arxiv.org/abs/1."
    )
    assert extract_project_urls(text) == ["https://proj.example.org/x"]


class _StubClient:
    def __init__(self, body: str, status: int = 200):
        self.body, self.status, self.calls = body, status, []

    def get(self, url, params=None):
        self.calls.append(params)
        return httpx.Response(self.status, text=self.body, request=httpx.Request("GET", url))


def test_fetch_recent_dedupes_cross_listed_papers_across_categories(monkeypatch):
    monkeypatch.setattr("radar.providers.arxiv.clock.now", lambda: NOW)
    client = _StubClient(XML)
    b = ArxivProvider(client=client).fetch_recent(["cs.RO", "cs.AI"], max_results=60)
    assert [c["search_query"] for c in client.calls] == ["cat:cs.RO", "cat:cs.AI"]
    assert client.calls[0]["sortBy"] == "submittedDate" and client.calls[0]["max_results"] == 60
    assert len(b.papers) == 60
    assert len([s for s in b.signals if s.signal_type == "paper_published"]) == 60


def test_http_failure_becomes_batch_error():
    b = ArxivProvider(client=_StubClient("", status=503)).search("tactile sensing")
    assert b.papers == [] and b.errors and "503" in b.errors[0]


def test_fetch_ids_strips_versions_and_rejects_garbage():
    client = _StubClient(_with_entries(_entries(XML)[:1]))
    b = ArxivProvider(client=client).fetch_ids(["2610.06850v1", "2610.06850", "../etc"])
    assert client.calls[0]["id_list"] == "2610.06850"
    assert b.rejected == 1 and [p.arxiv_id for p in b.papers] == ["2610.06850"]
