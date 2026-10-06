"""Manual X signal ingest: an analyst pastes a post URL (and optionally its text).

Text comes from X's public oEmbed endpoint when available; otherwise from the analyst's
pasted text, which is labelled `provenance='user_provided'` with reduced confidence.
With neither, nothing is produced — posts are never fabricated.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

from dateutil import parser as dateparser
from selectolax.lexbor import LexborHTMLParser

from radar.providers.ats import IDENTITY_ENCODING
from radar.providers.base import EntityRef, ProviderBatch
from radar.providers.http import HttpClient
from radar.providers.x.base import XPost, XUser, post_url, posts_to_batch, snowflake_time, x_person_ref
from radar.thesis.loader import Thesis, get_thesis

OEMBED = "https://publish.twitter.com/oembed?url={url}&omit_script=true"
ALLOWED_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com"}
_STATUS_PATH = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d{1,25})/?$")
_RESERVED_HANDLES = {"i", "home", "intent", "share", "search", "explore", "settings"}
PASTED_CONFIDENCE = 0.6
MAX_PASTED_CHARS = 4000


class InvalidPostURL(ValueError):
    pass


def parse_post_url(url: str) -> tuple[str, str]:
    """Strictly validate an X post URL → (handle, status_id). Raises InvalidPostURL."""
    p = urlparse((url or "").strip())
    if p.scheme != "https":
        raise InvalidPostURL("post URL must use https")
    if p.username or p.password or p.port is not None:
        raise InvalidPostURL("credentials or ports are not allowed in post URLs")
    if (p.hostname or "") not in ALLOWED_HOSTS or p.netloc.lower() != p.hostname:
        raise InvalidPostURL("host must be x.com, twitter.com or mobile.twitter.com")
    m = _STATUS_PATH.match(p.path)
    if not m or m.group(1).lower() in _RESERVED_HANDLES:
        raise InvalidPostURL("expected https://x.com/<handle>/status/<id>")
    return m.group(1), m.group(2)


def parse_oembed(data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract author handle/name, post text and display date from an oEmbed response."""
    author_url = str(data.get("author_url") or "")
    m = re.match(r"^https://(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?$", author_url)
    html = data.get("html")
    if not m or not isinstance(html, str):
        return None
    tree = LexborHTMLParser(html)
    para = tree.css_first("blockquote p")
    if para is None:
        return None
    text = htmllib.unescape(para.text(separator=" ")).strip()
    text = re.sub(r"[ \t]+", " ", text)
    anchors = tree.css("blockquote > a")
    shown_date: datetime | None = None
    if anchors:
        try:
            shown_date = dateparser.parse(anchors[-1].text().strip()).replace(tzinfo=UTC)
        except (ValueError, OverflowError):
            shown_date = None
    return {
        "handle": m.group(1),
        "author_name": htmllib.unescape(str(data.get("author_name") or "")) or None,
        "text": text,
        "shown_date": shown_date,
    }


class ManualXSignalProvider:
    name = "x_manual"

    def __init__(self, client: HttpClient | None = None, thesis: Thesis | None = None) -> None:
        self.client = client or HttpClient(min_interval=1.0, max_retries=1, timeout=10.0, headers=IDENTITY_ENCODING)
        self.thesis = thesis or get_thesis()

    def enabled(self) -> tuple[bool, str]:
        return True, "manual post URLs via X's public oEmbed endpoint (or analyst-pasted text)"

    def _oembed(self, handle: str, status_id: str) -> dict[str, Any] | None:
        target = f"https://twitter.com/{handle}/status/{status_id}"
        try:
            resp = self.client.get(OEMBED.format(url=quote(target, safe="")))
            if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
                return None
            return parse_oembed(resp.json())
        except Exception:  # noqa: BLE001 — any failure falls back to pasted text
            return None

    def ingest(
        self, url: str, pasted_text: str | None = None, *, entity_ref: EntityRef | None = None
    ) -> tuple[ProviderBatch, dict[str, Any]]:
        """Turn one post URL into signals. Returns (batch, info); info['error'] set when nothing was produced."""
        try:
            url_handle, status_id = parse_post_url(url)
        except InvalidPostURL as e:
            return ProviderBatch(provider=self.name, errors=[str(e)]), {"error": str(e)}

        info: dict[str, Any] = {"status_id": status_id, "url_handle": url_handle}
        embed = self._oembed(url_handle, status_id)
        pasted = (pasted_text or "").strip()[:MAX_PASTED_CHARS]
        created = snowflake_time(status_id)

        if embed and embed["text"]:
            handle, text, confidence = embed["handle"], embed["text"], 1.0
            meta = {"ingest": "manual", "provenance": "x_oembed", "handle_verified": True}
            user = XUser(username=handle, name=embed["author_name"])
            created = created or embed["shown_date"]
            info.update(text_source="oembed", author_name=embed["author_name"])
            if handle.lower() != url_handle.lower():
                info["handle_corrected_from"] = url_handle
        elif pasted:
            handle, text, confidence = url_handle, pasted, PASTED_CONFIDENCE
            meta = {"ingest": "manual", "provenance": "user_provided", "handle_verified": False}
            user = XUser(username=handle)
            info["text_source"] = "user_provided"
        else:
            err = "could not retrieve the post via oEmbed and no text was provided"
            return ProviderBatch(provider=self.name, errors=[err]), {**info, "error": err}

        if created is None:
            err = "post date could not be determined"
            return ProviderBatch(provider=self.name, errors=[err]), {**info, "error": err}
        canonical = post_url(handle, status_id)
        post = XPost(id=status_id, author_username=handle, text=text, created_at=created, url=canonical)
        ref = entity_ref or x_person_ref(handle)
        batch = posts_to_batch([post], user, ref, "manual", thesis=self.thesis, confidence=confidence,
                               extra_metadata=meta)
        batch.provider = self.name
        for person in batch.people:
            person.attrs.update(provenance=meta["provenance"], handle_verified=meta["handle_verified"])
        info.update(handle=handle, canonical_url=canonical, entity_key=ref.key,
                    signal_type=batch.signals[0].signal_type)
        return batch, info
