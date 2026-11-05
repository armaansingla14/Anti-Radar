"""Dependency and external-service analysis from fetched manifests — parsed, never executed.

Parsers use regex, ``tomllib`` and ``json`` only. ``setup.py`` is read with a regex over the
``install_requires=[...]`` literal; it is never imported or run. Risk indicators come from registry metadata
(``pkg:*`` artifacts) and OSV results (``osv:batch``); a dependency without an exact pin is never checked
against OSV and is reported as "not checked (unpinned)".
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime
from typing import Any

from radar.diligence.types import ArtifactDraft, days_between, parse_dt, parse_version

UNMAINTAINED_DAYS = 730  # no release for more than two years

# package-name prefix/exact -> (vendor, category)
VENDORS: tuple[tuple[str, str, str], ...] = (
    ("openai", "OpenAI", "llm_provider"),
    ("anthropic", "Anthropic", "llm_provider"),
    ("@anthropic-ai/", "Anthropic", "llm_provider"),
    ("google-genai", "Google", "llm_provider"),
    ("google-generativeai", "Google", "llm_provider"),
    ("@google/genai", "Google", "llm_provider"),
    ("@google/generative-ai", "Google", "llm_provider"),
    ("vertexai", "Google", "llm_provider"),
    ("cohere", "Cohere", "llm_provider"),
    ("mistralai", "Mistral", "llm_provider"),
    ("@mistralai/", "Mistral", "llm_provider"),
    ("groq", "Groq", "llm_provider"),
    ("together", "Together", "llm_provider"),
    ("replicate", "Replicate", "llm_provider"),
    ("litellm", "LiteLLM", "model_router"),
    ("huggingface-hub", "Hugging Face", "model_hub"),
    ("huggingface_hub", "Hugging Face", "model_hub"),
    ("transformers", "Hugging Face", "model_runtime"),
    ("vllm", "vLLM", "model_runtime"),
    ("torch", "PyTorch", "model_runtime"),
    ("langchain", "LangChain", "agent_framework"),
    ("@langchain/", "LangChain", "agent_framework"),
    ("langgraph", "LangChain", "agent_framework"),
    ("llama-index", "LlamaIndex", "agent_framework"),
    ("llama_index", "LlamaIndex", "agent_framework"),
    ("crewai", "CrewAI", "agent_framework"),
    ("autogen", "AutoGen", "agent_framework"),
    ("pyautogen", "AutoGen", "agent_framework"),
    ("mcp", "MCP", "agent_framework"),
    ("@modelcontextprotocol/", "MCP", "agent_framework"),
    ("boto3", "AWS", "cloud"),
    ("botocore", "AWS", "cloud"),
    ("aws-sdk", "AWS", "cloud"),
    ("@aws-sdk/", "AWS", "cloud"),
    ("sagemaker", "AWS", "cloud"),
    ("google-cloud-", "Google Cloud", "cloud"),
    ("@google-cloud/", "Google Cloud", "cloud"),
    ("azure-", "Azure", "cloud"),
    ("@azure/", "Azure", "cloud"),
    ("stripe", "Stripe", "payments"),
    ("supabase", "Supabase", "backend_service"),
    ("@supabase/", "Supabase", "backend_service"),
    ("firebase", "Firebase", "backend_service"),
    ("firebase-admin", "Firebase", "backend_service"),
    ("sentry-sdk", "Sentry", "observability"),
    ("@sentry/", "Sentry", "observability"),
    ("ddtrace", "Datadog", "observability"),
    ("datadog", "Datadog", "observability"),
    ("opentelemetry-", "OpenTelemetry", "observability"),
    ("@opentelemetry/", "OpenTelemetry", "observability"),
    ("prometheus-client", "Prometheus", "observability"),
    ("prometheus_client", "Prometheus", "observability"),
    ("langsmith", "LangSmith", "llm_observability"),
    ("langfuse", "Langfuse", "llm_observability"),
    ("wandb", "Weights & Biases", "llm_observability"),
    ("mlflow", "MLflow", "llm_observability"),
    ("lm-eval", "lm-eval", "evaluation"),
    ("lm_eval", "lm-eval", "evaluation"),
    ("ragas", "Ragas", "evaluation"),
    ("deepeval", "DeepEval", "evaluation"),
    ("promptfoo", "promptfoo", "evaluation"),
    ("psycopg", "PostgreSQL", "database"),
    ("psycopg2", "PostgreSQL", "database"),
    ("asyncpg", "PostgreSQL", "database"),
    ("pg", "PostgreSQL", "database"),
    ("redis", "Redis", "database"),
    ("ioredis", "Redis", "database"),
    ("pymongo", "MongoDB", "database"),
    ("mongodb", "MongoDB", "database"),
    ("kafka-python", "Kafka", "queue"),
    ("confluent-kafka", "Kafka", "queue"),
    ("kafkajs", "Kafka", "queue"),
    ("pinecone", "Pinecone", "vector_db"),
    ("pinecone-client", "Pinecone", "vector_db"),
    ("qdrant-client", "Qdrant", "vector_db"),
    ("weaviate-client", "Weaviate", "vector_db"),
    ("chromadb", "Chroma", "vector_db"),
)

ENV_VENDORS: tuple[tuple[str, str, str], ...] = (
    ("OPENAI_", "OpenAI", "llm_provider"), ("ANTHROPIC_", "Anthropic", "llm_provider"),
    ("GEMINI_", "Google", "llm_provider"), ("GOOGLE_API_KEY", "Google", "llm_provider"),
    ("COHERE_", "Cohere", "llm_provider"), ("MISTRAL_", "Mistral", "llm_provider"),
    ("GROQ_", "Groq", "llm_provider"), ("TOGETHER_", "Together", "llm_provider"),
    ("HF_TOKEN", "Hugging Face", "model_hub"), ("HUGGING", "Hugging Face", "model_hub"),
    ("AWS_", "AWS", "cloud"), ("GCP_", "Google Cloud", "cloud"), ("GOOGLE_CLOUD", "Google Cloud", "cloud"),
    ("AZURE_", "Azure", "cloud"), ("STRIPE_", "Stripe", "payments"), ("SUPABASE_", "Supabase", "backend_service"),
    ("FIREBASE_", "Firebase", "backend_service"), ("SENTRY_", "Sentry", "observability"),
    ("DD_", "Datadog", "observability"), ("OTEL_", "OpenTelemetry", "observability"),
    ("LANGSMITH_", "LangSmith", "llm_observability"), ("LANGCHAIN_", "LangChain", "agent_framework"),
    ("LANGFUSE_", "Langfuse", "llm_observability"), ("WANDB_", "Weights & Biases", "llm_observability"),
    ("DATABASE_URL", "PostgreSQL", "database"), ("POSTGRES", "PostgreSQL", "database"),
    ("REDIS_", "Redis", "database"), ("PINECONE_", "Pinecone", "vector_db"),
)

COMPOSE_SERVICES = {
    "postgres": "PostgreSQL", "pgvector": "PostgreSQL", "timescale": "PostgreSQL", "mysql": "MySQL",
    "mariadb": "MySQL", "redis": "Redis", "valkey": "Redis", "kafka": "Kafka", "redpanda": "Kafka",
    "rabbitmq": "RabbitMQ", "mongo": "MongoDB", "elasticsearch": "Elasticsearch", "opensearch": "OpenSearch",
    "minio": "MinIO", "clickhouse": "ClickHouse", "qdrant": "Qdrant", "weaviate": "Weaviate", "milvus": "Milvus",
    "nats": "NATS", "temporal": "Temporal", "grafana": "Grafana", "prometheus": "Prometheus", "jaeger": "Jaeger",
}

ECOSYSTEM_OSV = {"pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "go": "Go"}


def classify_vendor(name: str) -> tuple[str, str] | None:
    n = name.lower()
    for key, vendor, cat in VENDORS:
        if key.endswith(("-", "/")):
            if n.startswith(key):
                return vendor, cat
        elif n == key:
            return vendor, cat
    return None


def _dep(eco: str, name: str, spec: str, path: str, *, dev: bool = False, kind: str = "registry",
         pinned: str | None = None) -> dict[str, Any]:
    return {"ecosystem": eco, "name": name.strip(), "spec": spec.strip()[:120], "pinned_version": pinned,
            "dev": dev, "source_path": path, "kind": kind}


# ── Python ───────────────────────────────────────────────────────────────────

_PY_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _py_requirement(line: str, path: str, *, dev: bool = False) -> dict[str, Any] | None:
    line = line.split(" #", 1)[0].strip()
    if not line or line.startswith(("#", "-r", "-c", "--")):
        return None
    if line.startswith(("-e ", "git+", "hg+", "svn+")) or "git+" in line:
        m = re.search(r"#egg=([A-Za-z0-9._-]+)", line) or re.search(r"/([A-Za-z0-9._-]+?)(?:\.git)?(?:@|$)", line)
        return _dep("pypi", m.group(1) if m else line[:60], line, path, dev=dev, kind="git")
    if re.match(r"^https?://", line):
        return _dep("pypi", line.rsplit("/", 1)[-1][:60], line, path, dev=dev, kind="url")
    if " @ " in line:
        name, ref = line.split(" @ ", 1)
        return _dep("pypi", name, ref, path, dev=dev, kind="git" if "git" in ref else "url")
    m = _PY_REQ.match(line.split(";", 1)[0])
    if not m:
        return None
    spec = (m.group(3) or "").strip()
    pin = re.fullmatch(r"===?\s*([0-9][A-Za-z0-9.+!-]*)", spec)
    return _dep("pypi", m.group(1), spec, path, dev=dev, pinned=pin.group(1) if pin and "*" not in spec else None)


def parse_requirements(text: str, path: str) -> list[dict[str, Any]]:
    dev = bool(re.search(r"dev|test|lint|doc", path.rsplit("/", 1)[-1], re.I))
    return [d for line in text.splitlines() if (d := _py_requirement(line, path, dev=dev))]


DEV_GROUPS = ("dev", "test", "tests", "testing", "lint", "docs", "doc", "typing")


def parse_pyproject(text: str, path: str) -> tuple[list[dict[str, Any]], str | None]:
    """(dependencies, own package name). PEP 621 and Poetry."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return [], None
    out: list[dict[str, Any]] = []
    proj = data.get("project") or {}
    for req in proj.get("dependencies") or []:
        if isinstance(req, str) and (d := _py_requirement(req, path)):
            out.append(d)
    for group, reqs in sorted((proj.get("optional-dependencies") or {}).items()):
        for req in reqs or []:
            if isinstance(req, str) and (d := _py_requirement(req, path, dev=group.lower() in DEV_GROUPS)):
                out.append(d)
    for group, reqs in sorted((data.get("dependency-groups") or {}).items()):
        for req in reqs or []:
            if isinstance(req, str) and (d := _py_requirement(req, path, dev=group.lower() in DEV_GROUPS)):
                out.append(d)
    poetry = (data.get("tool") or {}).get("poetry") or {}
    sections = [(poetry.get("dependencies") or {}, False), (poetry.get("dev-dependencies") or {}, True)]
    sections += [((g or {}).get("dependencies") or {}, name.lower() in DEV_GROUPS)
                 for name, g in sorted((poetry.get("group") or {}).items())]
    for deps, dev in sections:
        for name, spec in sorted(deps.items()):
            if name.lower() == "python":
                continue
            if isinstance(spec, dict):
                if "git" in spec:
                    out.append(_dep("pypi", name, str(spec.get("git")), path, dev=dev, kind="git"))
                    continue
                if "path" in spec or "url" in spec:
                    out.append(_dep("pypi", name, str(spec.get("path") or spec.get("url")), path, dev=dev,
                                    kind="path" if "path" in spec else "url"))
                    continue
                spec = str(spec.get("version") or "")
            spec = str(spec)
            pin = re.fullmatch(r"=?=?\s*(\d[A-Za-z0-9.+-]*)", spec.strip())
            out.append(_dep("pypi", name, spec, path, dev=dev, pinned=pin.group(1) if pin else None))
    own = proj.get("name") or poetry.get("name")
    return out, str(own) if own else None


_SETUP_LIST = re.compile(r"install_requires\s*=\s*\[(.*?)\]", re.S)
_QUOTED = re.compile(r"""["']([^"'\n]+)["']""")


def parse_setup_py(text: str, path: str) -> tuple[list[dict[str, Any]], str | None]:
    """Regex over the literal ``install_requires=[...]`` list — the file is never executed or imported."""
    out: list[dict[str, Any]] = []
    m = _SETUP_LIST.search(text)
    if m:
        for q in _QUOTED.findall(m.group(1)):
            if d := _py_requirement(q, path):
                out.append(d)
    name = re.search(r"""\bname\s*=\s*["']([A-Za-z0-9._-]+)["']""", text)
    return out, name.group(1) if name else None


def parse_setup_cfg(text: str, path: str) -> tuple[list[dict[str, Any]], str | None]:
    out: list[dict[str, Any]] = []
    m = re.search(r"^install_requires\s*=\s*\n((?:[ \t]+.+\n?)+)", text, re.M)
    if m:
        for line in m.group(1).splitlines():
            if d := _py_requirement(line.strip(), path):
                out.append(d)
    name = re.search(r"^name\s*=\s*([A-Za-z0-9._-]+)\s*$", text, re.M)
    return out, name.group(1) if name else None


# ── JavaScript / Rust / Go ───────────────────────────────────────────────────


def parse_package_json(text: str, path: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        data = json.loads(text)
    except ValueError:
        return [], None
    if not isinstance(data, dict):
        return [], None
    out: list[dict[str, Any]] = []
    for section, dev in (("dependencies", False), ("devDependencies", True), ("optionalDependencies", False)):
        for name, spec in sorted((data.get(section) or {}).items()):
            spec = str(spec)
            if spec.startswith(("git", "github:", "http")) or re.match(r"^[\w-]+/[\w.-]+(#.*)?$", spec):
                out.append(_dep("npm", name, spec, path, dev=dev, kind="git" if not spec.startswith("http") else "url"))
            elif spec.startswith(("file:", "link:", "workspace:")):
                out.append(_dep("npm", name, spec, path, dev=dev, kind="path"))
            else:
                pin = re.fullmatch(r"=?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)", spec.strip())
                out.append(_dep("npm", name, spec, path, dev=dev, pinned=pin.group(1) if pin else None))
    own = data.get("name") if not data.get("private") else None
    return out, str(own) if own else None


def parse_cargo(text: str, path: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return [], None
    out: list[dict[str, Any]] = []
    for section, dev in (("dependencies", False), ("dev-dependencies", True), ("build-dependencies", True)):
        for name, spec in sorted((data.get(section) or {}).items()):
            if isinstance(spec, dict):
                if "git" in spec:
                    out.append(_dep("cargo", name, str(spec["git"]), path, dev=dev, kind="git"))
                    continue
                if "path" in spec and "version" not in spec:
                    out.append(_dep("cargo", name, str(spec["path"]), path, dev=dev, kind="path"))
                    continue
                spec = str(spec.get("version") or "")
            spec = str(spec)
            pin = re.fullmatch(r"=\s*(\d+\.\d+\.\d+)", spec.strip())
            out.append(_dep("cargo", name, spec, path, dev=dev, pinned=pin.group(1) if pin else None))
    own = (data.get("package") or {}).get("name")
    return out, str(own) if own else None


def parse_go_mod(text: str, path: str) -> tuple[list[dict[str, Any]], str | None]:
    out: list[dict[str, Any]] = []
    block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("require ("):
            block = True
            continue
        if block and line == ")":
            block = False
            continue
        m = re.match(r"^(?:require\s+)?([\w.\-~/]+\.[\w.\-~/]+)\s+(v[\w.\-+]+)(\s*//\s*indirect)?", line)
        if (block or line.startswith("require ")) and m:
            out.append(_dep("go", m.group(1), m.group(2), path, dev=bool(m.group(3)), pinned=m.group(2).lstrip("v")))
    mod = re.search(r"^module\s+(\S+)", text, re.M)
    return out, mod.group(1) if mod else None


# ── containers / env ─────────────────────────────────────────────────────────


def parse_dockerfile(text: str, path: str) -> dict[str, Any]:
    froms, add_urls, users, stages = [], [], [], set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        m = re.match(r"(?i)^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line)
        if m:
            image = m.group(1)
            tag = image.split("@", 1)[0].rsplit("/", 1)[-1]
            stage_ref = image.lower() in stages  # FROM <earlier build stage>, not a registry image
            froms.append({"image": image, "line": n, "stage_ref": stage_ref,
                          "latest": not stage_ref and (":" not in tag or tag.endswith(":latest")),
                          "pinned_digest": "@sha256:" in image})
            if m.group(2):
                stages.add(m.group(2).lower())
            users = []  # USER applies per stage: only the final stage's matters
        if re.match(r"(?i)^ADD\s+https?://", line):
            add_urls.append({"line": n, "quote": line[:200]})
        u = re.match(r"(?i)^USER\s+(\S+)", line)
        if u:
            users.append(u.group(1))
    runs_as_root = not users or users[-1] in ("root", "0", "0:0")
    return {"path": path, "from": froms, "add_urls": add_urls, "user": users[-1] if users else None,
            "runs_as_root": runs_as_root}


def parse_compose(text: str, path: str) -> list[dict[str, Any]]:
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*image:\s*['\"]?([^\s'\"#]+)", raw)
        if m:
            image = m.group(1)
            base = image.split("/")[-1].split(":")[0].lower()
            svc = next((v for k, v in COMPOSE_SERVICES.items() if base.startswith(k)), None)
            out.append({"path": path, "line": n, "image": image, "service": svc})
    return out


def parse_env_example(text: str, path: str) -> list[str]:
    return sorted({m.group(1) for m in re.finditer(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,60})\s*=", text, re.M)})


# ── dispatch ─────────────────────────────────────────────────────────────────


def manifest_kind(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1].lower()
    if re.fullmatch(r"requirements[\w.-]*\.(txt|in)", name):
        return "requirements"
    if name == "pyproject.toml":
        return "pyproject"
    if name == "setup.py":
        return "setup_py"
    if name == "setup.cfg":
        return "setup_cfg"
    if name == "package.json":
        return "package_json"
    if name == "cargo.toml":
        return "cargo"
    if name == "go.mod":
        return "go_mod"
    if name.startswith("dockerfile") or name.endswith(".dockerfile"):
        return "dockerfile"
    if re.fullmatch(r"(docker-)?compose[\w.-]*\.ya?ml", name):
        return "compose"
    if re.fullmatch(r"\.?env[\w.-]*\.(example|sample|template)|\.env\.example", name):
        return "env_example"
    return None


def file_artifacts(artifacts: list[ArtifactDraft]) -> list[ArtifactDraft]:
    return sorted([a for a in artifacts if a.kind == "file"], key=lambda a: a.key)


def parse_manifests(artifacts: list[ArtifactDraft]) -> dict[str, Any]:
    """Every dependency, own package name, container and env fact from fetched files."""
    deps: list[dict[str, Any]] = []
    own: list[dict[str, str]] = []
    dockerfiles: list[dict[str, Any]] = []
    compose: list[dict[str, Any]] = []
    env_vars: list[dict[str, Any]] = []
    manifests: list[dict[str, str]] = []
    for art in file_artifacts(artifacts):
        path = str((art.data or {}).get("path") or art.key.split(":", 2)[-1])
        kind = manifest_kind(path)
        if kind is None:
            continue
        manifests.append({"path": path, "kind": kind, "url": art.url, "artifact_key": art.key})
        text = art.text or ""
        name = None
        if kind == "requirements":
            found = parse_requirements(text, path)
        elif kind in ("pyproject", "setup_py", "setup_cfg", "package_json", "cargo", "go_mod"):
            parser = {"pyproject": parse_pyproject, "setup_py": parse_setup_py, "setup_cfg": parse_setup_cfg,
                      "package_json": parse_package_json, "cargo": parse_cargo, "go_mod": parse_go_mod}[kind]
            found, name = parser(text, path)
        elif kind == "dockerfile":
            dockerfiles.append({**parse_dockerfile(text, path), "url": art.url, "artifact_key": art.key})
            continue
        elif kind == "compose":
            compose.extend({**c, "url": art.url, "artifact_key": art.key} for c in parse_compose(text, path))
            continue
        else:
            env_vars.extend({"name": v, "path": path, "url": art.url, "artifact_key": art.key}
                            for v in parse_env_example(text, path))
            continue
        for d in found:
            d["url"], d["artifact_key"] = art.url, art.key
        deps.extend(found)
        if name:
            eco = {"pyproject": "pypi", "setup_py": "pypi", "setup_cfg": "pypi", "package_json": "npm",
                   "cargo": "cargo", "go_mod": "go"}[kind]
            own.append({"ecosystem": eco, "name": name, "path": path})
    return {"dependencies": deps, "own_packages": own, "dockerfiles": dockerfiles, "compose": compose,
            "env_vars": env_vars, "manifests": manifests}


def external_services(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Vendors / services the code depends on, with the evidence (manifest path, package or env var)."""
    found: dict[tuple[str, str], list[dict[str, str]]] = {}
    for d in parsed["dependencies"]:
        hit = classify_vendor(d["name"])
        if hit:
            found.setdefault(hit, []).append({"path": d["source_path"], "item": d["name"], "url": d.get("url", ""),
                                              "dev": d["dev"]})
    for e in parsed["env_vars"]:
        for prefix, vendor, cat in ENV_VENDORS:
            if e["name"].startswith(prefix):
                found.setdefault((vendor, cat), []).append({"path": e["path"], "item": e["name"],
                                                            "url": e.get("url", ""), "dev": False})
                break
    for c in parsed["compose"]:
        if c["service"]:
            cat = "database" if c["service"] not in ("Kafka", "RabbitMQ", "NATS", "Temporal") else "queue"
            if c["service"] in ("Grafana", "Prometheus", "Jaeger"):
                cat = "observability"
            found.setdefault((c["service"], cat), []).append({"path": c["path"], "item": c["image"],
                                                              "url": c.get("url", ""), "dev": False})
    out = []
    for (vendor, cat), ev in sorted(found.items()):
        uniq = {(e["path"], e["item"]): e for e in ev}
        runtime = any(not e["dev"] for e in uniq.values())
        out.append({"vendor": vendor, "category": cat, "runtime": runtime,
                    "evidence": [uniq[k] for k in sorted(uniq)][:8]})
    return out


def _registry_index(artifacts: list[ArtifactDraft]) -> dict[tuple[str, str], ArtifactDraft]:
    out = {}
    for a in artifacts:
        if a.kind == "package":
            out[(str(a.data.get("ecosystem")), str(a.data.get("name", "")).lower())] = a
    return out


def analyze_dependencies(artifacts: list[ArtifactDraft], as_of: datetime) -> dict[str, Any]:
    """Parsed dependencies, external services, registry-based risk indicators and OSV results."""
    parsed = parse_manifests(artifacts)
    deps = parsed["dependencies"]
    registry = _registry_index(artifacts)
    risks: list[dict[str, Any]] = []
    runtime = [d for d in deps if not d["dev"]]
    for d in sorted(deps, key=lambda d: (d["ecosystem"], d["name"].lower(), d["source_path"])):
        reg = registry.get((d["ecosystem"], d["name"].lower()))
        if reg is None:
            continue
        meta = reg.data or {}
        latest, latest_at = meta.get("latest_version"), parse_dt(meta.get("latest_release_at"))
        d["latest_version"], d["latest_release_at"] = latest, meta.get("latest_release_at")
        base = {"dependency": d["name"], "ecosystem": d["ecosystem"], "dev": d["dev"], "url": reg.url,
                "artifact_key": reg.key}
        if meta.get("deprecated"):
            risks.append({**base, "id": "deprecated", "severity": "MEDIUM" if not d["dev"] else "LOW",
                          "title": f"{d['name']} is marked deprecated on its registry",
                          "detail": str(meta.get("deprecated"))[:200]})
        age = days_between(latest_at, as_of)
        if age is not None and age > UNMAINTAINED_DAYS:
            risks.append({**base, "id": "unmaintained", "severity": "LOW",
                          "title": f"{d['name']}: no release since {latest_at.date().isoformat()}",
                          "detail": f"Latest release {latest} is {int(age)} days old (threshold {UNMAINTAINED_DAYS})."})
        pv, lv = parse_version(d.get("pinned_version")), parse_version(latest)
        if pv and lv and lv[0] > pv[0]:
            risks.append({**base, "id": "outdated_major", "severity": "LOW",
                          "title": f"{d['name']} pinned to {d['pinned_version']}; latest is {latest}",
                          "detail": "A newer major version exists."})
    for d in deps:
        if d["kind"] in ("git", "url"):
            risks.append({"id": "git_install", "severity": "LOW", "dependency": d["name"], "ecosystem": d["ecosystem"],
                          "dev": d["dev"], "url": d.get("url", ""), "artifact_key": d.get("artifact_key", ""),
                          "title": f"{d['name']} is installed from a {d['kind'].upper()} reference ({d['source_path']})",
                          "detail": "Not resolved through a registry; builds depend on that reference staying available."})

    vulns, checked = _osv(artifacts)
    for v in vulns:
        sev = v.get("severity_level") or "UNKNOWN"
        risks.append({"id": "vulnerability", "severity": "HIGH" if sev in ("HIGH", "CRITICAL") else "MEDIUM",
                      "dependency": v["name"], "ecosystem": v["ecosystem"], "dev": False, "url": v["url"],
                      "artifact_key": "osv:batch",
                      "title": f"{v['name']} {v['version']} is affected by {v['id']}",
                      "detail": (v.get("summary") or "")[:200]})

    services = external_services(parsed)
    vendor_runtime = [s for s in services if s["runtime"]]
    llm = sorted({s["vendor"] for s in vendor_runtime if s["category"] == "llm_provider"})
    clouds = sorted({s["vendor"] for s in vendor_runtime if s["category"] == "cloud"})
    unpinned = sorted({d["name"] for d in runtime if d["kind"] == "registry" and not d["pinned_version"]
                       and d["ecosystem"] in ECOSYSTEM_OSV})
    by_eco: dict[str, int] = {}
    for d in deps:
        by_eco[d["ecosystem"]] = by_eco.get(d["ecosystem"], 0) + 1
    risks.sort(key=lambda r: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(r["severity"], 3), r["id"], r["dependency"]))
    return {
        "manifests": parsed["manifests"],
        "dependencies": sorted(deps, key=lambda d: (d["ecosystem"], d["dev"], d["name"].lower()))[:200],
        "counts": {"total": len(deps), "runtime": len(runtime), "dev": len(deps) - len(runtime),
                   "pinned": sum(1 for d in deps if d["pinned_version"]), "by_ecosystem": dict(sorted(by_eco.items()))},
        "own_packages": parsed["own_packages"],
        "external_services": services,
        "llm_providers": llm,
        "clouds": clouds,
        "dockerfiles": parsed["dockerfiles"],
        "compose_services": parsed["compose"],
        "env_vars": sorted({e["name"] for e in parsed["env_vars"]}),
        "risks": risks,
        "vulnerabilities": vulns,
        "osv_checked": checked,
        "not_checked_unpinned": unpinned,
        "parsed": bool(parsed["manifests"]),
    }


def _severity_level(v: dict[str, Any]) -> str | None:
    sev = (v.get("database_specific") or {}).get("severity")
    if isinstance(sev, str) and sev.upper() in ("LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL"):
        return {"MODERATE": "MEDIUM"}.get(sev.upper(), sev.upper())
    return None


def _osv(artifacts: list[ArtifactDraft]) -> tuple[list[dict[str, Any]], int]:
    """Vulnerabilities from the OSV artifact — each one carries an OSV id; nothing is inferred."""
    art = next((a for a in artifacts if a.key == "osv:batch"), None)
    if art is None:
        return [], 0
    out = []
    results = (art.data or {}).get("results") or []
    for r in results:
        for v in r.get("vulns") or []:
            vid = str(v.get("id") or "")
            if not vid:
                continue
            out.append({"id": vid, "aliases": sorted(v.get("aliases") or []), "summary": v.get("summary") or "",
                        "severity_level": _severity_level(v), "name": r.get("name"), "ecosystem": r.get("ecosystem"),
                        "version": r.get("version"), "url": f"https://osv.dev/vulnerability/{vid}"})
    out.sort(key=lambda v: (v["name"] or "", v["id"]))
    return out, len(results)


def own_package_names(artifacts: list[ArtifactDraft]) -> list[tuple[str, str]]:
    """(ecosystem, name) of the company's own packages: manifest names plus README install lines."""
    parsed = parse_manifests(artifacts)
    out: list[tuple[str, str]] = [(p["ecosystem"], p["name"]) for p in parsed["own_packages"]
                                  if p["ecosystem"] in ("pypi", "npm")]
    for a in artifacts:
        if a.kind != "readme":
            continue
        for m in re.finditer(r"\b(?:pip3?|uv pip|uv) (?:install|add)\s+(?:-U\s+|--upgrade\s+|-q\s+)*([A-Za-z0-9][A-Za-z0-9._-]{0,80})",
                             a.text or ""):
            out.append(("pypi", m.group(1)))
        for m in re.finditer(r"\b(?:npm (?:install|i)|yarn add|pnpm add)\s+(?:-[gSD]\s+|--save(?:-dev)?\s+)*"
                             r"((?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]{0,80})", a.text or ""):
            out.append(("npm", m.group(1)))
    seen: set[tuple[str, str]] = set()
    uniq = []
    for eco, name in out:
        key = (eco, name.lower())
        if key not in seen and name.lower() not in ("-r", "requirements", "git", "pip", "uv", "."):
            seen.add(key)
            uniq.append((eco, name))
    return uniq
