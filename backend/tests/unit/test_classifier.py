"""Deterministic thesis keyword classifier."""

from __future__ import annotations

from radar.thesis.classifier import category_priors, classify_text


def _keys(matches) -> list[str]:
    return [m.key for m in matches]


def test_tactile_robotics_text_ranks_tactile_first(thesis):
    m = classify_text([
        ("GelSight-style tactile sensors and haptic skin for dexterous robot hands", 1.5),
        ("tactile-sensing ; robotics ; manipulation", 2.0),
    ], thesis)
    assert m[0].key == "robotics.tactile"
    assert m[0].theme == "robotics"
    assert {"tactile", "gelsight", "haptic"} <= set(m[0].matched)
    assert "robotics.manipulation" in _keys(m)
    assert all(0 < x.score <= 1 for x in m)


def test_semiconductor_text(thesis):
    m = classify_text([("An open-source RISC-V core written in SystemVerilog with an FPGA flow", 1.5)], thesis)
    assert m[0].key == "semiconductors.eda_hardware"
    assert {"risc-v", "systemverilog", "fpga"} <= set(m[0].matched)


def test_no_substring_false_positives(thesis):
    # 'ai' in 'maintain', 'mes' in 'times', 'ide' in 'side', 'gan' in 'organ', 'sic' in 'music'
    m = classify_text([("We maintain music organ tools; sometimes side projects take longer", 2.0)], thesis)
    assert m == []


def test_plurals_and_separator_variants(thesis):
    assert "robotics.actuation" in _keys(classify_text([("custom actuators and servos", 1.5)], thesis))
    assert "energy.grid" in _keys(classify_text([("grid-scale batteries", 1.5)], thesis))
    assert "robotics.robot_foundation_models" in _keys(
        classify_text([("a vision language action model", 1.5)], thesis))


def test_ambiguous_context_is_suppressed(thesis):
    assert "energy.fusion" not in _keys(classify_text([("camera-lidar sensor fusion for SLAM", 2.0)], thesis))
    assert "energy.fusion" in _keys(classify_text([("compact fusion power plant", 2.0)], thesis))
    assert "ai.foundation_models" not in _keys(classify_text([("power transformer monitoring", 2.0)], thesis))


def test_score_saturates_and_threshold(thesis):
    weak = classify_text([("throughput", 0.5)], thesis)
    assert weak == []  # a single generic word in low-weight text is not a thesis match
    strong = classify_text([("tokamak stellarator plasma confinement fusion", 2.0)] * 3, thesis)
    top = strong[0]
    assert top.key == "energy.fusion" and 0.95 < top.score <= 1.0


def test_arxiv_category_priors(thesis):
    assert category_priors(["physics.plasm-ph"]) == {"energy.fusion": 0.8}
    m = classify_text([("Edge transport in magnetized plasmas", 1.0)], thesis, priors=category_priors(["physics.plasm-ph"]))
    assert m and m[0].key == "energy.fusion" and "prior:energy.fusion" in m[0].matched
    # theme priors only reinforce subcategories that matched by keyword
    m2 = classify_text([("A new grasping policy", 1.0)], thesis, priors=category_priors(["cs.RO"]))
    assert _keys(m2) == ["robotics.manipulation"]
    assert classify_text([("A new method", 1.0)], thesis, priors=category_priors(["cs.RO"])) == []
