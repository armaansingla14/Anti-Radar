"""Public-artifact security review: masked secrets, insecure defaults, supply chain, Dockerfile, advisories."""

from __future__ import annotations

import json

from radar.diligence.security import mask_secret, security_review
from tests.fixtures.diligence.helpers import art, gh_file, readme, tree

AWS_KEY = "AKIA" + "Z7TESTVALUEABCDE"  # built at runtime: no credential-shaped literal in the source tree
SK_KEY = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


def test_secrets_are_reported_masked_and_never_printed():
    env = gh_file(".env.example", f"AWS_ACCESS_KEY_ID={AWS_KEY}\nOPENAI_API_KEY={SK_KEY}\n"
                                  "STRIPE_KEY=your-key-here\nAPI_TOKEN=Zq8vX2mN4pL7rT1wY6uK3hB9\n")
    out = security_review([env])
    dump = json.dumps(out)
    assert AWS_KEY not in dump and SK_KEY not in dump and "Zq8vX2mN4pL7rT1wY6uK3hB9" not in dump
    secrets = [f for f in out["findings"] if f["kind"] == "secret"]
    assert len(secrets) == 3  # the placeholder is not reported
    first = next(f for f in secrets if "AWS" in f["title"])
    assert first["severity"] == "HIGH" and first["material_question"] is True
    assert first["title"].startswith("Possible secret pattern (AWS access key id) in .env.example line 1 — verify")
    assert "AKIA…[masked]" in first["evidence"]["quote"] and first["evidence"]["line"] == 1


def test_mask_secret_reveals_at_most_four_characters():
    assert mask_secret("AKIAABCDEFGHIJKLMNOP") == "AKIA…[masked]"
    assert mask_secret("short") == "…[masked]"


def test_insecure_defaults():
    compose = gh_file("docker-compose.yml", "services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: changeme\n"
                                            "      DEBUG: 'true'\n    command: serve --host 0.0.0.0\n")
    out = security_review([compose])
    titles = [f["title"] for f in out["findings"]]
    assert any("Default password" in t for t in titles)
    assert any("Debug mode" in t for t in titles)
    assert any("0.0.0.0" in t for t in titles)
    assert not any("changeme" in f["evidence"]["quote"] for f in out["findings"])


def test_dockerfile_root_latest_and_add_url():
    df = gh_file("Dockerfile", "FROM rocm/pytorch:latest\nADD https://example.org/x.tgz /x\nRUN chmod 777 /x\n")
    titles = [f["title"] for f in security_review([df])["findings"]]
    assert any("runs as root" in t for t in titles)
    assert any("Unpinned base image rocm/pytorch:latest" in t for t in titles)
    assert any("ADD from a URL" in t for t in titles)
    assert any("chmod 777" in t for t in titles)
    ok = gh_file("Dockerfile", "FROM python:3.12-slim\nUSER app\n")
    assert not [f for f in security_review([ok])["findings"] if f["kind"] == "dockerfile"]


def test_supply_chain_patterns_in_readme():
    rd = readme("```\ncurl -fsSL https://get.acme.example/install.sh | sudo bash\n```\n"
                "model = load(x, trust_remote_code=True)\nstate = torch.load(path)\nsafe = torch.load(p, weights_only=True)\n")
    titles = [f["title"] for f in security_review([rd])["findings"]]
    assert any("pipe a remote script" in t for t in titles)
    assert any("trust_remote_code" in t for t in titles)
    assert sum("torch.load" in t for t in titles) == 1


def test_security_md_and_advisories():
    adv = art("advisories:acme-fixture/engine", "advisories", "code", "", {"full_name": "acme-fixture/engine",
              "advisories": [{"ghsa_id": "GHSA-aaaa-bbbb-cccc", "severity": "critical", "summary": "RCE in loader",
                              "html_url": "https://github.com/acme-fixture/engine/security/advisories/GHSA-aaaa-bbbb-cccc"}]})
    out = security_review([tree(["src/main.rs"]), adv])
    kinds = {f["kind"]: f for f in out["findings"]}
    assert kinds["advisory"]["severity"] == "HIGH" and kinds["advisory"]["material_question"] is True
    assert "policy" in kinds and out["posture"]["security_md"] is False
    assert out["findings"][0]["id"] == "SEC-1" and out["findings"][0]["severity"] == "HIGH"
    assert security_review([tree(["SECURITY.md"])])["posture"]["security_md"] is True
    assert security_review([])["reviewed"] is False
