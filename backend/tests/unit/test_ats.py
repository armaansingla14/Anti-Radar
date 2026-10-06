"""ATS detection/normalization and role classification."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from radar.providers import ats
from radar.providers.base import domain_ref
from radar.providers.http import MAX_BODY_BYTES, HttpClient
from radar.providers.roles import classify_role, is_technical
from tests.conftest import FIXTURES

ACME = domain_ref("acme-robotics.com")


def _fx(name: str):
    return json.loads((FIXTURES / "ats" / name).read_text())


# ── detection ──
@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://boards.greenhouse.io/acmerobotics/jobs/123", [("greenhouse", "acmerobotics")]),
        ("https://job-boards.greenhouse.io/acmerobotics", [("greenhouse", "acmerobotics")]),
        ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>', [("greenhouse", "acme")]),
        ("https://boards.greenhouse.io/embed/job_board?for=acme&amp;b=x", [("greenhouse", "acme")]),
        ("https://jobs.lever.co/acme-robotics/6ed76ce8/apply", [("lever", "acme-robotics")]),
        ("https://jobs.ashbyhq.com/Acme%20Robotics/abc", [("ashby", "Acme Robotics")]),
        ('{"url":"https://jobs.ashbyhq.com/saronic\\"}', [("ashby", "saronic")]),
        ("https://example.com/careers", []),
        ("https://greenhouse.io/acme", []),
    ],
)
def test_detect_ats(text, expected):
    assert ats.detect_ats(text) == expected


def test_detect_ats_dedupes_case_insensitively_in_order():
    html = (
        '<a href="https://jobs.lever.co/acme">x</a> <a href="https://boards.greenhouse.io/acme">y</a>'
        '<a href="https://jobs.lever.co/ACME/123">z</a>'
    )
    assert ats.detect_ats([html, "https://jobs.ashbyhq.com/acme"]) == [
        ("lever", "acme"), ("greenhouse", "acme"), ("ashby", "acme")
    ]


# ── normalization ──
def test_parse_greenhouse():
    jobs = ats.parse_greenhouse(_fx("greenhouse_jobs.json"), ACME)
    assert len(jobs) == 2  # empty title dropped
    fw, ae = jobs
    assert fw.external_id == "greenhouse:4802172007"
    assert fw.title == "Senior Firmware Engineer, Motor Control"
    assert fw.department == "Hardware"  # from job-board department metadata
    assert fw.location == "Austin, Texas, United States"
    assert fw.posted_at == datetime(2026, 8, 11, 17, 41, 15, tzinfo=UTC)
    assert fw.url.startswith("https://job-boards.greenhouse.io/")
    assert ae.title == "Account Executive" and ae.department == "Sales" and ae.posted_at is None
    assert all(j.company == ACME and j.ats == "greenhouse" for j in jobs)


def test_parse_lever():
    jobs = ats.parse_lever(_fx("lever_postings.json"), ACME)
    assert [j.external_id for j in jobs] == [
        "lever:6ed76ce8-4156-4b60-b120-403538bd66cd", "lever:0b5e2c11-9e8f-4b8e-a7f5-2b36a8f0b001"
    ]
    assert jobs[0].department == "Engineering" and jobs[0].location == "San Francisco, CA"
    assert jobs[0].posted_at == datetime.fromtimestamp(1786469891.368, tz=UTC)
    assert jobs[1].department == "Silicon"  # team used when no department


def test_parse_ashby_skips_unlisted():
    jobs = ats.parse_ashby(_fx("ashby_board.json"), ACME)
    assert [j.title for j in jobs] == ["Research Engineer, Robot Learning", "Controls Engineer"]
    assert jobs[0].department == "Research"
    assert jobs[0].posted_at == datetime(2026, 9, 12, 16, 38, 15, 322000, tzinfo=UTC)


@respx.mock
def test_fetch_jobs_uses_public_endpoints():
    client = HttpClient(max_retries=0)
    respx.get("https://boards-api.greenhouse.io/v1/boards/acmerobotics/jobs", params={"content": "false"}).mock(
        return_value=httpx.Response(200, json=_fx("greenhouse_jobs.json")))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme-robotics").mock(
        return_value=httpx.Response(200, json=_fx("ashby_board.json")))
    lever = respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=_fx("lever_postings.json")))
    assert len(ats.fetch_jobs(client, "greenhouse", "acmerobotics", ACME)) == 2
    assert len(ats.fetch_jobs(client, "ashby", "acme-robotics", ACME)) == 2
    result = ats.fetch_board(client, "lever", "acme", ACME)
    assert len(result.jobs) == 2 and result.complete
    assert lever.calls.last.request.url.params["mode"] == "json"
    assert lever.calls.last.request.url.params["skip"] == "0"


@respx.mock
def test_fetch_jobs_404_raises_not_empty():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/nope").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        ats.fetch_jobs(HttpClient(max_retries=0), "ashby", "nope", ACME)


def test_fetch_jobs_rejects_bad_input():
    with pytest.raises(ValueError):
        ats.fetch_jobs(HttpClient(), "workday", "acme", ACME)
    with pytest.raises(ValueError):
        ats.fetch_jobs(HttpClient(), "lever", "../../admin", ACME)


@respx.mock
def test_truncated_board_salvages_whole_postings_and_flags_incomplete():
    job = _fx("ashby_board.json")["jobs"][0]
    n = 2 * MAX_BODY_BYTES // len(json.dumps(job))  # enough postings to exceed the body cap
    body = json.dumps({"apiVersion": "1", "jobs": [dict(job, id=f"id-{i}") for i in range(n)]})
    respx.get("https://api.ashbyhq.com/posting-api/job-board/big").mock(
        return_value=httpx.Response(200, content=body.encode()[: MAX_BODY_BYTES + 1]))  # as cut by HttpClient
    result = ats.fetch_board(HttpClient(max_retries=0), "ashby", "big", ACME)
    assert not result.complete
    assert 0 < len(result.jobs) < n
    assert len({j.external_id for j in result.jobs}) == len(result.jobs)


def test_salvage_array_drops_partial_element():
    text = '{"jobs": [{"id": 1}, {"id": 2}, {"id": 3, "title": "Tru'
    assert ats.salvage_array(text, "jobs") == [{"id": 1}, {"id": 2}]
    assert ats.salvage_array('[{"a": 1}, {"a"', None) == [{"a": 1}]


# ── role classification ──
@pytest.mark.parametrize(
    "title, department, expected",
    [
        ("Senior Research Scientist", None, "ml_research"),
        ("Machine-Learning Engineer, Perception", None, "ml_research"),  # longest phrase wins over 'perception'
        ("Perception Engineer", None, "robotics_controls"),
        ("Embedded Software Engineer", None, "embedded_firmware"),
        ("FPGA Engineer", None, "embedded_firmware"),
        ("RTL Design Engineer", None, "silicon"),
        ("CUDA Kernel Engineer", None, "gpu_compiler"),
        ("GNC Engineer", None, "rf_defense"),
        ("Power Electronics Engineer", None, "energy_plasma"),
        ("Manufacturing Engineer II", None, "manufacturing"),
        ("Site Reliability Engineer", None, "infra"),
        ("Sales Engineer, Embedded Systems", None, "embedded_firmware"),  # technical beats gtm
        ("Account Executive", None, "gtm"),
        ("Technical Recruiter", None, "gtm"),
        ("Staff Engineer", "Firmware", "embedded_firmware"),  # department fallback
        ("Office Manager", None, None),
        ("Basic Operations Associate", None, None),  # 'asic' must not match inside 'basic'
        ("Salesforce Administrator", None, None),  # 'sales' must not match inside 'salesforce'
        ("Firmware Engineers", None, "embedded_firmware"),
    ],
)
def test_classify_role(thesis, title, department, expected):
    assert classify_role(title, department, thesis) == expected


def test_is_technical():
    assert is_technical("ml_research")
    assert not is_technical("gtm")
    assert not is_technical(None)
