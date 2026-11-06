"""Deterministic copy for events: why it matters (INFERENCE) and impact areas, keyed by subtype."""

from __future__ import annotations

WHY: dict[str, str] = {
    "new_round": "New capital changes runway, hiring capacity and the pace of the roadmap.",
    "acquisition": "An acquisition changes the team, the technology or the market the company addresses.",
    "strategic_investment": "A strategic investor can bring distribution or supply, and sometimes constraints.",
    "government_contract": "A government award is public evidence of a paying customer and may lead to larger programs.",
    "partnership": "A partnership can open distribution or supply; its terms decide whether it is material.",
    "customer_announcement": "A named customer is evidence of adoption, if the deployment is paid and in production.",
    "deployment": "A deployment moves the product from demonstration toward use.",
    "product_launch": "A launch moves the question from feasibility to adoption.",
    "major_release": "A major release can change what users can do, or break what they rely on.",
    "new_feature": "A new capability shows where the product is heading.",
    "pricing_change": "Pricing changes affect adoption and margins.",
    "specialized_hiring_cluster": "Concentrated specialised hiring often precedes a new product line or program.",
    "new_role_class": "Hiring in a new discipline can mean a new product direction.",
    "hiring_acceleration": "Faster technical hiring usually follows new funding or demand.",
    "role_closures": "Closed postings mean roles were filled or withdrawn; the board does not say which.",
    "executive_join": "A new leader changes who owns a function and what it prioritises.",
    "executive_departure": "A leadership departure can change execution in that function.",
    "benchmark_claim": "A performance claim can be tested against public evidence before it is relied on.",
    "research_paper": "A paper shows the technical approach in the open, and can support or contradict claims.",
    "open_source_release": "An open-source release with traction builds a developer base and shows the code.",
    "repository_release": "A notable release shows shipping cadence on public code.",
    "repo_traction": "Accelerating repository attention can lead adoption.",
    "security_incident": "A security risk signal needs a factual account of scope and remediation.",
    "outage": "An outage risk signal can affect customers' trust and contracts.",
    "lawsuit": "A legal risk signal can create exposure or distraction.",
    "recall": "A recall risk signal affects deployed products and customers.",
    "repository_archived": "An archived repository can mean a discontinued project.",
    "regulatory_milestone": "Regulatory steps can unlock or delay commercial timelines.",
    "repositioning": "A new self-description can mean a change of target market or product focus.",
    "market_entry": "Entering a new market changes the competitive set and the go-to-market.",
    "public_attention": "Public attention can lead adoption or hiring, but is not adoption itself.",
}
AREAS: dict[str, list[str]] = {
    "new_round": ["capital", "runway"], "acquisition": ["capital", "strategy"],
    "strategic_investment": ["capital", "partners"], "government_contract": ["revenue", "government"],
    "partnership": ["partners", "distribution"], "customer_announcement": ["customers", "revenue"],
    "deployment": ["customers"], "product_launch": ["product"], "major_release": ["product", "technology"],
    "new_feature": ["product"], "pricing_change": ["pricing", "revenue"],
    "specialized_hiring_cluster": ["talent", "roadmap"], "new_role_class": ["talent", "roadmap"],
    "hiring_acceleration": ["talent"], "role_closures": ["talent"], "executive_join": ["leadership"],
    "executive_departure": ["leadership"], "benchmark_claim": ["technology", "claims"],
    "research_paper": ["technology"], "open_source_release": ["technology", "community"],
    "repository_release": ["technology"], "repo_traction": ["community"], "security_incident": ["risk", "security"],
    "outage": ["risk", "reliability"], "lawsuit": ["risk", "legal"], "recall": ["risk", "product"],
    "repository_archived": ["risk", "technology"], "regulatory_milestone": ["regulatory"],
    "repositioning": ["strategy", "market"], "market_entry": ["strategy", "market"],
    "public_attention": ["attention"],
}
NOISE_WHY = "Routine change: counted in the funnel, not surfaced."


def why_it_matters(subtype: str, materiality: str) -> str:
    if materiality == "NOISE":
        return NOISE_WHY
    return WHY.get(subtype, "A typed public change about the company.")


def impact_areas(subtype: str) -> list[str]:
    return list(AREAS.get(subtype, []))
