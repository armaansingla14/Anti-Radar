"""URL/domain normalization shared by providers and entity resolution."""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

import tldextract

# Offline extractor: use the bundled public-suffix snapshot, never fetch at runtime.
_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Hosts that are platforms, not companies. A link to these never identifies a company.
PLATFORM_DOMAINS = {
    "github.com", "github.io", "gitlab.com", "bitbucket.org", "huggingface.co", "hf.co",
    "arxiv.org", "openreview.net", "paperswithcode.com", "semanticscholar.org", "doi.org", "acm.org", "ieee.org",
    "x.com", "twitter.com", "t.co", "linkedin.com", "facebook.com", "instagram.com", "threads.net", "bsky.app",
    "youtube.com", "youtu.be", "vimeo.com", "medium.com", "substack.com", "notion.site", "notion.so",
    "google.com", "docs.google.com", "goo.gl", "bit.ly", "ycombinator.com", "news.ycombinator.com",
    "reddit.com", "wikipedia.org", "discord.gg", "discord.com", "slack.com", "readthedocs.io", "readthedocs.org",
    "pypi.org", "npmjs.com", "crates.io", "vercel.app", "netlify.app", "herokuapp.com", "streamlit.app",
    "colab.research.google.com", "kaggle.com", "apple.com", "microsoft.com", "amazon.com", "nytimes.com",
    "techcrunch.com", "bloomberg.com", "wsj.com", "theverge.com", "arstechnica.com", "wired.com",
    "blogspot.com", "wordpress.com", "ghost.io", "dev.to", "hashnode.dev", "itch.io", "gist.github.com",
    "producthunt.com", "apps.apple.com", "play.google.com", "drive.google.com", "dropbox.com",
    # App hosting: a subdomain here identifies a project, never a company domain.
    "fly.dev", "pages.dev", "workers.dev", "onrender.com", "railway.app", "up.railway.app", "replit.app",
    "repl.co", "glitch.me", "surge.sh", "deno.dev", "azurewebsites.net", "cloudfront.net", "appspot.com",
    "web.app", "firebaseapp.com", "gitbook.io", "framer.website", "webflow.io", "carrd.co", "modal.run",
}
EDU_SUFFIXES = (".edu", ".ac.uk", ".ac.jp", ".edu.cn", ".ac.kr")


def registrable_domain(url_or_host: str | None) -> str | None:
    """'https://www.Acme-Robotics.com/careers' -> 'acme-robotics.com'. None if not a domain."""
    if not url_or_host:
        return None
    s = url_or_host.strip()
    if "://" not in s:
        s = "http://" + s
    host = (urlparse(s).hostname or "").lower().rstrip(".")
    if not host or re.fullmatch(r"[\d.]+", host):
        return None
    ext = _extract(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


@lru_cache(maxsize=4096)
def is_platform_domain(domain: str | None) -> bool:
    if not domain:
        return True
    d = domain.lower()
    return d in PLATFORM_DOMAINS or any(d.endswith("." + p) for p in PLATFORM_DOMAINS)


def is_academic_domain(domain: str | None) -> bool:
    return bool(domain) and domain.lower().endswith(EDU_SUFFIXES)


def company_domain(url: str | None) -> str | None:
    """Registrable domain if it could plausibly identify a company (not a platform)."""
    d = registrable_domain(url)
    if not d or is_platform_domain(d):
        return None
    return d


GITHUB_REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)", re.I)
GITHUB_RESERVED = {"orgs", "topics", "features", "sponsors", "marketplace", "about", "settings", "login", "apps",
                   "collections", "trending", "explore", "pricing", "enterprise", "site", "issues", "pulls"}


def github_repos_in(text: str | None) -> list[str]:
    """Extract owner/name repo slugs from free text (deduplicated, order preserved)."""
    if not text:
        return []
    out: list[str] = []
    for owner, name in GITHUB_REPO_RE.findall(text):
        name = re.sub(r"(\.git|[.,;:)\]}>'\"]+)$", "", name)
        if owner.lower() in GITHUB_RESERVED or not name:
            continue
        slug = f"{owner}/{name}"
        if slug.lower() not in {o.lower() for o in out}:
            out.append(slug)
    return out


def slugify(text: str, maxlen: int = 80) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen] or "entity"
