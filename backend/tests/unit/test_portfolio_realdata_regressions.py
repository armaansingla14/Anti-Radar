"""Regressions from the hand audit of the first live portfolio monitoring run (Nov 6, 2026).

Titles below are the real titles from that run; each test pins the corrected classification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from radar.portfolio import materiality, normalize
from radar.portfolio.attention import event_action
from radar.portfolio.types import CompanyContext, RawChange, SourceRef

CID = uuid.UUID(int=7)
T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)


def raw(signal_type, title, *, company="Modal", url="https://modal.com/blog/x", st="website", publisher="company",
        meta=None, description=None, raw_value=None) -> RawChange:
    fam = {"website": "web", "hackernews": "community", "github": "github"}[st]
    return RawChange(company_id=CID, kind="signal", signal_type=signal_type, title=title, event_at=T0,
                     description=description, raw_value=raw_value,
                     source=SourceRef(url=url, title=title, source_type=st, family=fam, publisher=publisher),
                     meta={"company_name": company, **(meta or {})})


def classify(r: RawChange):
    c = normalize.normalize_one(r)
    ctx = CompanyContext(company_id=CID, name=r.meta["company_name"])
    return c, materiality.classify(c, ctx, [c])


def test_third_party_show_hn_is_not_the_companys_launch():
    """#1: a Show HN that only mentions the company's hardware is third-party discussion."""
    r = raw("hn_show", "Show HN: Pi-Loop (Prototype) – A CLI for Reference Intake loop on DGX Spark",
            company="NVIDIA Research Projects", st="hackernews", publisher="third_party",
            url="https://news.ycombinator.com/item?id=50040782",
            meta={"points": 1, "link_source": "story_text",
                  "link": "https://www.nvidia.com/en-us/products/workstations/dgx-spark/"})
    c, (level, rule, _) = classify(r)
    assert (c.subtype, level) == ("low_engagement_discussion", "NOISE")
    big = raw("hn_show", "Show HN: something on DGX Spark", st="hackernews", publisher="third_party",
              meta={"points": 300, "link_source": "story_text"})
    assert classify(big)[0].subtype == "public_attention"
    own = raw("hn_launch", "Launch HN: Modal (YC S21) – serverless GPUs", st="hackernews", publisher="third_party",
              meta={"points": 300, "link_source": "url", "link": "https://modal.com/"})
    assert classify(own)[0].subtype == "product_launch"


@pytest.mark.parametrize("title,company", [
    ("DeepSeek V4.1 Flash 1017 is now available on Shared Endpoints", "Modal"),
    ("Muse Spark Open is now available on Modal", "Modal"),
    ("NVIDIA Nemotron 3.5 Super VL is now available on Modal", "Modal"),
    ("Step 5 Preview is now available on Modal", "Modal"),
    ("Reflection Beam is now available on Modal", "Modal"),
    ("Meta's Muse Spark open-weight release Is Now in Limited Preview on GroqCloud", "Groq"),
])
def test_model_catalog_additions_are_noise(title, company):
    """#2: '<third-party model> is now available on <platform>' is a catalog addition, not a launch."""
    c, (level, rule, _) = classify(raw("company_post", title, company=company))
    assert (c.subtype, level, rule) == ("catalog_addition", "NOISE", "M10")


def test_catalog_additions_do_not_block_launch_novelty():
    cat = normalize.normalize_one(raw("company_post", "Muse Spark Open is now available on Modal"))
    launch = normalize.normalize_one(raw("company_post", "Introducing our Training API"))
    ctx = CompanyContext(company_id=CID, name="Modal")
    assert materiality.classify(launch, ctx, [cat, launch])[0] == "MATERIAL"
    own = normalize.normalize_one(raw("company_post", "Modal Sandboxes are now available on AWS Marketplace"))
    assert own.subtype != "catalog_addition"


@pytest.mark.parametrize("title,url,company", [
    ("Launching a trillion-parameter model at Sakana AI", "https://modal.com/blog/sakana-case-study", "Modal"),
    ("How TELUS Digital scaled voice support with ElevenLabs", "https://elevenlabs.io/blog/telus-digital", "ElevenLabs"),
    ("Angle Health cuts call handling time with ElevenLabs", "https://elevenlabs.io/blog/angle-health", "ElevenLabs"),
    ("KPN", "https://elevenlabs.io/customer-stories/kpn", "ElevenLabs"),
])
def test_customer_stories_are_counted_not_launched(title, url, company):
    """#3: case studies are customer stories (NOISE, counted in the customer_stories proxy)."""
    c, (level, _, _) = classify(raw("company_post", title, company=company, url=url))
    assert (c.subtype, level) == ("customer_story", "NOISE")
    assert normalize.is_customer_story(title, url, company)


def test_pricing_guides_are_not_pricing_changes():
    """#4"""
    c, (level, _, _) = classify(raw("company_post", "AI voice agent pricing: What it costs for your business",
                                    company="ElevenLabs"))
    assert (c.subtype, level) == ("guide", "NOISE")
    for t in ("New pricing for teams", "Price cut: GPUs now cost 30% less", "Pricing update for Sandboxes"):
        assert classify(raw("company_post", t))[0].subtype == "pricing_change", t
    assert classify(raw("company_post", "A Practical Guide to Security in November 2026"))[0].subtype == "guide"


def test_api_beta_is_a_product_beta():
    """#5"""
    r = raw("hn_discussion", "Physical Intelligence API Beta: Frontier Models for Every Robot",
            company="Physical Intelligence", st="hackernews", publisher="third_party",
            meta={"points": 53, "link_source": "url", "link": "https://www.pi.website/blog/api-beta"})
    c, (level, rule, _) = classify(r)
    assert (c.event_type, c.subtype, level, rule) == ("PRODUCT", "product_beta", "WATCH", "M4")


@pytest.mark.parametrize("repo,tag,stars,flagship,expect", [
    ("deepgram/deepgram-js-sdk", "v5.18.0", 900, False, "routine_release"),
    ("deepgram/deepgram-python-sdk", "v7.17.0", 1200, True, "routine_release"),  # an SDK's minor tracks the API
    ("elevenlabs/elevenlabs-python", "v2.76.0", 3000, False, "routine_release"),
    ("runpod/runpodctl", "v3.1.0", 600, True, "routine_release"),
    ("thinking-machines-lab/tinker-cookbook", "v0.6.0", 2000, True, "routine_release"),
    ("stereolabs/zed-sdk", "5.5.0", 1000, True, "routine_release"),
    ("OpenHands/OpenHands", "v1.35.0", 60000, True, "repository_release"),  # flagship minor >= 1,000 stars
    ("runpod/runpod-python", "v2.0.0", 300, False, "repository_release"),  # major of a repo >= 200 stars
    ("acme/tiny", "v2.0.0", 40, True, "routine_release"),
])
def test_routine_sdk_releases_are_noise(repo, tag, stars, flagship, expect):
    """#6"""
    r = raw("release_published", f"{repo}: Released {tag}", st="github", url=f"https://github.com/{repo}",
            meta={"tag": tag, "repo": repo, "repo_stars": stars, "repo_flagship": flagship})
    c, (level, _, _) = classify(r)
    assert c.subtype == expect
    assert level == ("NOISE" if expect == "routine_release" else "WATCH")


def test_proposed_award_is_watch_never_material():
    """#7: a letter of intent for a proposed award is not a contract."""
    r = raw("company_post", "How an $85M federal award would help CFS, fusion energy, and U.S. chip industry",
            company="Commonwealth Fusion Systems", url="https://blog.cfs.energy/how-an-85m-federal-award",
            description="The Department of Commerce and CFS have signed a letter of intent for $85 million in "
                        "proposed federal incentives.")
    c, (level, rule, reason) = classify(r)
    assert (c.subtype, level, rule) == ("proposed_award", "WATCH", "M3") and "not finalized" in reason
    assert c.meta["amount"] == 85e6
    h = raw("company_post", "Helion Signs Letter of Intent For $175 Million CHIPS Award to Advance U.S. High "
                            "Voltage Semiconductor R&D", company="Helion")
    assert classify(h)[0].subtype == "proposed_award"
    real = raw("company_post", "Acme awarded $2M Air Force contract", company="Acme")
    assert classify(real)[0].subtype == "government_contract"


def test_marketplace_backdoor_is_a_capped_risk_signal():
    """#8"""
    r = raw("hn_discussion", "Backdoor in Claude Code for VS Code extension on the Cursor Marketplace",
            company="Cursor", st="hackernews", publisher="third_party",
            meta={"points": 1011, "link_source": "story_text",
                  "link": "https://cursor.com/blog/claude-code-extension-security-notice"})
    c, (level, rule, reason) = classify(r)
    assert (c.event_type, c.subtype, level, rule) == ("RISK", "marketplace_security", "WATCH", "M1")
    assert "risk signal" in reason and "not a flaw" in reason and "crisis" not in reason
    for t in ("Malware found in our build pipeline", "Supply-chain attack on our npm package"):
        assert classify(raw("company_post", t))[0].event_type == "RISK", t


def test_missed_product_changes():
    """#9"""
    c, (level, _, _) = classify(raw("company_post", "ElevenAgents integrates natively with leading contact center "
                                                    "platforms", company="ElevenLabs"))
    assert (c.subtype, level) == ("integration", "WATCH")
    c, (level, _, _) = classify(raw("company_post", "Sunsetting Modal Notebooks, effective today"))
    assert (c.subtype, level) == ("product_sunset", "WATCH")
    c, (level, _, _) = classify(raw("company_post", "NVIDIA Vera CPUs double throughput for Modal Sandboxes"))
    assert (c.event_type, c.subtype, level) == ("TECHNICAL", "performance_claim", "WATCH")
    assert event_action(materiality=level, event_type=c.event_type, subtype=c.subtype) == "RUN_DILIGENCE"
    own = classify(raw("company_post", "Cerebras Deploys Trillion-Parameter AI Models", company="Cerebras"))[0]
    assert own.subtype == "capability_announcement"
    cust = classify(raw("company_post", "Example Health deploys Cerebras for clinical notes", company="Cerebras"))[0]
    assert cust.subtype == "customer_announcement"


def test_security_product_launch_is_not_a_risk_signal():
    """#13 (found on the re-run): a security *product* is not a security incident."""
    c, (level, _, _) = classify(raw("company_post", "Devin Security: Find and fix your most dangerous vulnerabilities",
                                    company="Cognition", url="https://cognition.com/blog/devin-security"))
    assert c.event_type != "RISK" and level != "MATERIAL"
    for t in ("Security incident affecting the fleet API", "We patched a critical vulnerability in the agent runtime",
              "Vulnerability disclosed in our SDK (CVE-2026-1234)"):
        assert classify(raw("company_post", t))[0].event_type == "RISK", t


def test_leadership_named_is_talent_not_integration():
    """#14 (found on the re-run)."""
    c = classify(raw("company_post", "Alex Cheung Named VP of Systems Engineering, Integration & Testing (SEIT)",
                     company="Zap Energy"))[0]
    assert (c.event_type, c.subtype) == ("TALENT", "executive_join")
    ctx = CompanyContext(company_id=CID, name="Zap Energy")
    assert materiality.classify(c, ctx, [c])[:2] == ("WATCH", "M5")  # a VP is not a C-level change
    cto = normalize.normalize_one(raw("company_post", "Jane Doe joins Zap Energy as CTO", company="Zap Energy"))
    assert materiality.classify(cto, ctx, [cto])[:2] == ("MATERIAL", "M5")


def test_language_named_client_libraries_are_sdks():
    r = raw("release_published", "elevenlabs/elevenlabs-python: Released v2.76.0", st="github",
            meta={"tag": "v2.76.0", "repo": "elevenlabs/elevenlabs-python", "repo_stars": 3000, "repo_flagship": True})
    assert classify(r)[0].subtype == "routine_release"


def test_head_of_hire_is_talent_watch_not_a_launch():
    """#15: 'Factory Announces Connor Maloney as Head of Federal' was typed as a product launch."""
    c, (level, rule, _) = classify(raw("company_post", "Factory Announces Connor Maloney as Head of Federal",
                                       company="Factory"))
    assert (c.event_type, c.subtype, level, rule) == ("TALENT", "executive_join", "WATCH", "M5")
    assert classify(raw("company_post", "Factory announces Droids 2.0", company="Factory"))[0].subtype == \
        "product_launch"


def test_programs_are_not_launches_and_do_not_count_for_novelty():
    """#16: a residency/fellowship 'launch' is a program (NOISE) and never makes a later product launch a repeat."""
    prog = normalize.normalize_one(raw("company_post", "National Design Studio and Cognition Launch the American "
                                                       "Forge Residency", company="Cognition"))
    assert (prog.subtype, prog.event_type) == ("program", "NOISE")
    launch = normalize.normalize_one(raw("company_post", "Introducing SWE-2 Flash", company="Cognition"))
    ctx = CompanyContext(company_id=CID, name="Cognition")
    assert materiality.classify(launch, ctx, [prog, launch])[:2] == ("MATERIAL", "M4")
    # The rule stays honest: a real product launch in the previous 90 days still makes it a repeat.
    earlier = normalize.normalize_one(raw("company_post", "Introducing SWE-2: Pushing the Pareto Frontier",
                                          company="Cognition"))
    earlier.event_at = launch.event_at.replace(month=9)
    assert materiality.classify(launch, ctx, [prog, earlier, launch])[:2] == ("WATCH", "M4")
    assert classify(raw("company_post", "Anduril selected for Army program of record", company="Anduril"))[0] \
        .subtype != "program"


def test_a_claim_extracted_from_the_same_post_does_not_make_it_material():
    """#17: after the portfolio -> diligence handoff, the diligence run extracted the post itself as a claim; the
    event then 'bore on' its own claim and became MATERIAL by M7. Circular evidence is ignored."""
    url = "https://modal.com/blog/nvidia-vera-sandboxes"
    c = normalize.normalize_one(raw("company_post", "NVIDIA Vera CPUs double throughput for Modal Sandboxes", url=url))
    same = CompanyContext(company_id=CID, name="Modal", diligence_claims=[
        {"id": "1", "text": "NVIDIA Vera CPUs double throughput for Modal Sandboxes", "source_url": url}])
    assert materiality.classify(c, same, [c])[:2] == ("WATCH", "M7")
    other = CompanyContext(company_id=CID, name="Modal", diligence_claims=[
        {"id": "2", "text": "Modal Sandboxes on NVIDIA Vera CPUs reach double throughput", "source_url":
         "https://modal.com/docs/sandboxes"}])
    assert materiality.classify(c, other, [c])[:2] == ("MATERIAL", "M7")
    # Two generic shared words (besides the company name) are not "bearing on" a claim.
    weak = CompanyContext(company_id=CID, name="Modal", diligence_claims=[
        {"id": "3", "text": "Modal powers GPU and CPU sandboxes for millions of runs",
         "source_url": "https://modal.com/blog/snorkel"}])
    assert materiality.classify(c, weak, [c])[:2] == ("WATCH", "M7")


def test_an_organisation_joining_the_company_is_a_strategic_event_not_noise():
    """'Corridor is Joining Cognition' (cognition.com/blog, Oct 28: 'Today, Corridor is joining Cognition…') was NOISE."""
    got = normalize.classify_title("Corridor is Joining Cognition", company_name="Cognition")
    assert got == ("STRATEGIC", "company_joining", "N19")
    assert normalize.classify_title("Welcoming Dioxus to Cognition", company_name="Cognition")[1] == "company_joining"
    # a person joining in a role is a leadership change, never an organisation joining
    hire = normalize.classify_title("Jane Doe is joining Cognition as Chief Technology Officer", company_name="Cognition")
    assert hire is None or hire[1] != "company_joining"
    # the company joining something else is not this rule
    other = normalize.classify_title("Cognition is joining the Agentic AI Foundation", company_name="Cognition")
    assert other is None or other[1] != "company_joining"


def test_merged_event_lists_the_company_source_first():
    """'Factory Acquires HumanLayer' merged an HN story with factory.com/news/humanlayer but listed HN first."""
    from radar.portfolio.dedup import combine
    from radar.portfolio.types import Candidate

    cid = uuid.uuid4()
    at = datetime(2026, 11, 4, tzinfo=UTC)
    hn = SourceRef(url="https://news.ycombinator.com/item?id=1", title="Factory Acquires HumanLayer",
                   source_type="hackernews", family="community", publisher="third_party")
    own = SourceRef(url="https://factory.com/news/humanlayer", title="Factory Acquires HumanLayer",
                    source_type="website", family="web", publisher="company")
    a = Candidate(company_id=cid, event_type="FINANCING", subtype="acquisition", title="Factory Acquires HumanLayer",
                  event_at=at, sources=[hn], epistemic="FACT")
    b = Candidate(company_id=cid, event_type="FINANCING", subtype="acquisition", title="Factory Acquires HumanLayer",
                  event_at=at, sources=[own], epistemic="FACT")
    merged = combine([a, b])
    assert merged.sources[0].url == "https://factory.com/news/humanlayer"
