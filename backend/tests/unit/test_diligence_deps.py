"""Manifest parsing for every ecosystem (no execution), vendor classification, risk indicators, OSV results."""

from __future__ import annotations

from radar.diligence.deps import (
    analyze_dependencies,
    classify_vendor,
    own_package_names,
    parse_cargo,
    parse_compose,
    parse_dockerfile,
    parse_env_example,
    parse_go_mod,
    parse_package_json,
    parse_pyproject,
    parse_requirements,
    parse_setup_cfg,
    parse_setup_py,
)
from tests.fixtures.diligence.helpers import AS_OF, art, gh_file, package, readme


def _names(deps):
    return [(d["name"], d["pinned_version"], d["dev"], d["kind"]) for d in deps]


def test_requirements():
    deps = parse_requirements("# c\nrequests==2.31.0\nnumpy>=1.26 ; python_version>'3.9'\n-r other.txt\n"
                              "git+https://github.com/x/y.git#egg=yy\nuvicorn[standard]==0.29.0\n", "requirements.txt")
    assert _names(deps) == [("requests", "2.31.0", False, "registry"), ("numpy", None, False, "registry"),
                            ("yy", None, False, "git"), ("uvicorn", "0.29.0", False, "registry")]
    assert parse_requirements("pytest==8.0\n", "requirements-dev.txt")[0]["dev"] is True


def test_pyproject_pep621_and_poetry():
    deps, name = parse_pyproject('[project]\nname = "acme"\ndependencies = ["fastapi>=0.1", "openai==1.30.1"]\n'
                                 '[project.optional-dependencies]\ndev = ["pytest"]\n', "pyproject.toml")
    assert name == "acme"
    assert _names(deps) == [("fastapi", None, False, "registry"), ("openai", "1.30.1", False, "registry"),
                            ("pytest", None, True, "registry")]
    deps, name = parse_pyproject('[tool.poetry]\nname = "acme-p"\n[tool.poetry.dependencies]\npython = "^3.11"\n'
                                 'httpx = "^0.27"\nboto3 = "1.34.0"\nlib = {git = "https://github.com/x/lib"}\n'
                                 '[tool.poetry.group.dev.dependencies]\nruff = "*"\n', "pyproject.toml")
    assert name == "acme-p"
    assert _names(deps) == [("boto3", "1.34.0", False, "registry"), ("httpx", None, False, "registry"),
                            ("lib", None, False, "git"), ("ruff", None, True, "registry")]


def test_setup_py_is_parsed_by_regex_never_executed(tmp_path, monkeypatch):
    marker = tmp_path / "executed"
    src = (f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')\nraise SystemExit(1)\n"
           "setup(name='acme-legacy', install_requires=['torch==2.3.0', \"numpy>=1.26\"])\n")
    deps, name = parse_setup_py(src, "setup.py")
    assert name == "acme-legacy" and _names(deps) == [("torch", "2.3.0", False, "registry"),
                                                      ("numpy", None, False, "registry")]
    assert not marker.exists()


def test_setup_cfg():
    deps, name = parse_setup_cfg("[metadata]\nname = acme\n[options]\ninstall_requires =\n    requests==2.0.0\n"
                                 "    click\n", "setup.cfg")
    assert name == "acme" and _names(deps) == [("requests", "2.0.0", False, "registry"), ("click", None, False, "registry")]


def test_package_json_runtime_and_dev_separately():
    deps, name = parse_package_json('{"name": "@acme/sdk", "dependencies": {"openai": "4.20.0", "zod": "^3.22"},'
                                    ' "devDependencies": {"vitest": "1.0.0"}, "optionalDependencies": '
                                    '{"x": "github:acme/x"}}', "package.json")
    assert name == "@acme/sdk"
    assert _names(deps) == [("openai", "4.20.0", False, "registry"), ("zod", None, False, "registry"),
                            ("vitest", "1.0.0", True, "registry"), ("x", None, False, "git")]
    assert parse_package_json('{"name": "app", "private": true}', "package.json")[1] is None


def test_cargo_and_go_mod():
    deps, name = parse_cargo('[package]\nname = "acme-core"\n[dependencies]\ntokio = "1.37"\nserde = { version = "=1.0.200" }\n'
                             'hip = { git = "https://github.com/x/hip" }\n[dev-dependencies]\ncriterion = "0.5"\n',
                             "Cargo.toml")
    assert name == "acme-core"
    assert _names(deps) == [("hip", None, False, "git"), ("serde", "1.0.200", False, "registry"),
                            ("tokio", None, False, "registry"), ("criterion", None, True, "registry")]
    deps, mod = parse_go_mod("module github.com/acme/x\n\ngo 1.22\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n"
                             "\tgolang.org/x/net v0.20.0 // indirect\n)\nrequire github.com/aws/aws-sdk-go v1.50.0\n",
                             "go.mod")
    assert mod == "github.com/acme/x"
    assert _names(deps) == [("github.com/gin-gonic/gin", "1.9.1", False, "registry"),
                            ("golang.org/x/net", "0.20.0", True, "registry"),
                            ("github.com/aws/aws-sdk-go", "1.50.0", False, "registry")]


def test_dockerfile_compose_env():
    d = parse_dockerfile("FROM python:3.12 AS build\nRUN pip wheel .\nFROM build\nADD https://x/y.tgz /y\n"
                         "FROM nvidia/cuda:latest\nUSER app\n", "Dockerfile")
    assert [f["latest"] for f in d["from"]] == [False, False, True]  # "FROM build" is a stage, not an image
    assert d["add_urls"] and d["user"] == "app" and d["runs_as_root"] is False
    assert parse_dockerfile("FROM ubuntu\nRUN make\n", "Dockerfile")["runs_as_root"] is True
    services = parse_compose("services:\n  db:\n    image: postgres:16\n  q:\n    image: 'redis:7'\n", "docker-compose.yml")
    assert [s["service"] for s in services] == ["PostgreSQL", "Redis"]
    assert parse_env_example("OPENAI_API_KEY=\nexport AWS_REGION=us-east-1\n# X=1\n", ".env.example") == [
        "AWS_REGION", "OPENAI_API_KEY"]


def test_vendor_classification():
    assert classify_vendor("openai") == ("OpenAI", "llm_provider")
    assert classify_vendor("@aws-sdk/client-s3") == ("AWS", "cloud")
    assert classify_vendor("google-cloud-storage") == ("Google Cloud", "cloud")
    assert classify_vendor("opentelemetry-sdk") == ("OpenTelemetry", "observability")
    assert classify_vendor("left-pad") is None


def test_analysis_risks_osv_and_unpinned_not_checked():
    arts = [
        gh_file("requirements.txt", "requests==2.19.0\nboto3\nold-lib==0.1\n"),
        gh_file("pyproject.toml", '[project]\nname = "acme"\ndependencies = ["openai==1.0.0"]\n'),
        package("old-lib", ["dependency"], "0.9.0", "2022-01-01T00:00:00+00:00"),
        package("openai", ["dependency"], "2.1.0", "2026-09-01T00:00:00+00:00"),
        art("osv:batch", "osv", "registry", "", {"results": [
            {"ecosystem": "pypi", "name": "requests", "version": "2.19.0",
             "vulns": [{"id": "GHSA-x84v-xcm2-53pg", "aliases": ["CVE-2018-18074"], "summary": "creds",
                        "database_specific": {"severity": "HIGH"}}]},
            {"ecosystem": "pypi", "name": "openai", "version": "1.0.0", "vulns": []}]}),
    ]
    d = analyze_dependencies(arts, AS_OF)
    ids = {(r["id"], r.get("dependency")) for r in d["risks"]}
    assert ("vulnerability", "requests") in ids and ("unmaintained", "old-lib") in ids
    assert ("outdated_major", "openai") in ids
    vuln = d["vulnerabilities"][0]
    assert vuln["id"] == "GHSA-x84v-xcm2-53pg" and vuln["severity_level"] == "HIGH"
    assert d["not_checked_unpinned"] == ["boto3"]
    assert d["llm_providers"] == ["OpenAI"] and d["clouds"] == ["AWS"]
    assert d["risks"][0]["severity"] == "HIGH"


def test_no_vulnerability_without_osv_id():
    arts = [gh_file("requirements.txt", "requests==2.19.0\n"),
            art("osv:batch", "osv", "registry", "", {"results": [
                {"ecosystem": "pypi", "name": "requests", "version": "2.19.0", "vulns": [{"summary": "no id"}]}]})]
    assert analyze_dependencies(arts, AS_OF)["vulnerabilities"] == []


def test_own_package_names_from_manifests_and_readme():
    arts = [gh_file("pyproject.toml", '[project]\nname = "acme"\n'),
            readme("```\npip install -U acme-extra\nnpm install @acme/sdk\n```")]
    assert own_package_names(arts) == [("pypi", "acme"), ("pypi", "acme-extra"), ("npm", "@acme/sdk")]
