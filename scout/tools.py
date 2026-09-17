"""The tool layer: search, scrape, and the Composio toolkit registry.

Composio is the tool provider. The orchestration loop lives in pipeline.py and
is plain Python we own, which is what CLAUDE.md asks for.

There is one fallback here. When COMPOSIO_API_KEY is missing, scrape falls
back to a plain HTTP GET so the pipeline can still be exercised end to end.
The fallback is labelled in the cache under source, so a page fetched that way
is never mistaken for a Composio fetch. Search has no fallback: without a key,
search returns nothing and the record keeps its docs hint only.
"""

import json
import re
import threading
import urllib.request

from . import cache, config, ratelimit

USER_ID = "scout"

# Tool slugs, read off the Composio API on 2026-09-16, not guessed. Tried in
# order so a provider outage or a missing connection falls through to the next
# one instead of killing the run.
SEARCH_SLUGS = [
    "COMPOSIO_SEARCH_WEB",
    "EXA_SEARCH",
    "FIRECRAWL_SEARCH",
]
# COMPOSIO_SEARCH_FETCH_URL_CONTENT goes first because composio_search is
# Composio-managed and works with no setup. Firecrawl and Exa both need their
# own API key connected as a connected account, and there are none on this
# account, so they 404 with ActionExecute_ConnectedAccountNotFound. Connect
# Firecrawl and it is worth promoting above the Composio fetcher for quality.
SCRAPE_SLUGS = [
    "COMPOSIO_SEARCH_FETCH_URL_CONTENT",
    "FIRECRAWL_SCRAPE",
    "EXA_GET_CONTENTS_ACTION",
]

# Loop C loads a signup or pricing page in a real browser when confidence is
# low. Not wired yet, recorded here so the slug is not guessed later.
BROWSER_SLUGS = ["BROWSER_TOOL_CREATE_TASK", "BROWSER_TOOL_WATCH_TASK"]

# Which toolkit each tool belongs to. Manual execution rejects "latest" and
# demands a concrete toolkit version, so we look the version up per toolkit
# and cache it for the run.
TOOLKIT_OF = {
    "COMPOSIO_SEARCH": "composio_search",
    "FIRECRAWL": "firecrawl",
    "EXA": "exa",
    "BROWSER_TOOL": "browser_tool",
}

_versions = {}


def toolkit_of(slug):
    for prefix, toolkit in TOOLKIT_OF.items():
        if slug.startswith(prefix + "_"):
            return toolkit
    return None


def toolkit_version(toolkit):
    """The pinned version for a toolkit. Cached, because it is one API call
    and it does not change mid-run."""
    if toolkit in _versions:
        return _versions[toolkit]
    c = composio()
    if c is None:
        return None
    try:
        _versions[toolkit] = c.client.toolkits.retrieve(toolkit).meta.version
    except Exception:  # noqa: BLE001
        _versions[toolkit] = None
    return _versions[toolkit]


def execute(slug, arguments):
    """Run one Composio tool. Raises on failure so the caller can fall through
    to the next provider."""
    c = composio()
    version = toolkit_version(toolkit_of(slug) or "")
    kwargs = {"user_id": USER_ID}
    if version:
        kwargs["version"] = version
    return ratelimit.with_retry(
        lambda: c.tools.execute(slug, arguments, **kwargs),
        limiter=ratelimit.composio_limiter,
    )

_client = None


def composio():
    """The Composio client, or None when we have no key."""
    global _client
    if _client is not None:
        return _client
    if not config.COMPOSIO_API_KEY:
        return None
    from composio import Composio

    _client = Composio(api_key=config.COMPOSIO_API_KEY)
    return _client


# --- probe -----------------------------------------------------------------

def probe():
    """Report what we actually have: toolkits on the account, which search and
    scrape tools resolve, and whether the toolkit registry answers. Prints
    facts, guesses nothing."""
    c = composio()
    if c is None:
        return {
            "key": False,
            "error": "COMPOSIO_API_KEY not set, nothing to probe",
        }

    out = {"key": True, "toolkits": [], "search": [], "scrape": [], "mcp": None}

    try:
        page = c.client.toolkits.list(limit=100)
        items = getattr(page, "items", None) or []
        out["toolkits"] = sorted(getattr(t, "slug", str(t)) for t in items)
    except Exception as exc:  # noqa: BLE001 - we want the message, not a trace
        out["toolkits_error"] = "%s: %s" % (type(exc).__name__, exc)

    for slug in SEARCH_SLUGS + SCRAPE_SLUGS:
        try:
            c.tools.get_raw_composio_tool_by_slug(slug)
            bucket = "search" if slug in SEARCH_SLUGS else "scrape"
            out[bucket].append(slug)
        except Exception:  # noqa: BLE001 - absence is the answer
            pass

    try:
        servers = c.client.mcp.list()
        out["mcp"] = {
            "reachable": True,
            "servers": len(getattr(servers, "items", []) or []),
        }
    except Exception as exc:  # noqa: BLE001
        out["mcp"] = {"reachable": False, "error": "%s: %s" % (type(exc).__name__, exc)}

    return out


# --- search ----------------------------------------------------------------

def search(query, limit=5):
    """Return a list of {title, url, snippet}. Empty list when we have no
    search tool, which is an honest answer rather than a guess."""
    c = composio()
    if c is None:
        return []

    for slug in SEARCH_SLUGS:
        try:
            res = execute(slug, {"query": query})
        except Exception:  # noqa: BLE001 - try the next provider
            continue
        hits = _search_hits(res)
        if hits:
            return hits[:limit]
    return []


def _search_hits(res):
    """Pull {title, url, snippet} out of whatever shape the provider returned.
    Providers disagree on keys, so we walk the payload instead of hard coding
    one shape."""
    data = res.get("data") if isinstance(res, dict) else getattr(res, "data", None)
    hits = []

    def walk(node):
        if isinstance(node, dict):
            url = node.get("url") or node.get("link")
            if url and isinstance(url, str) and url.startswith("http"):
                hits.append({
                    "title": node.get("title") or node.get("name") or "",
                    "url": url,
                    "snippet": node.get("snippet") or node.get("description")
                    or node.get("content") or "",
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    seen, unique = set(), []
    for h in hits:
        if h["url"] not in seen:
            seen.add(h["url"])
            unique.append(h)
    return unique


# --- scrape ----------------------------------------------------------------

def scrape(url):
    """Fetch a page as text. Cached forever, keyed by URL."""
    hit = cache.get(url)
    if hit is not None:
        return hit

    c = composio()
    if c is not None:
        for slug in SCRAPE_SLUGS:
            try:
                res = execute(slug, {"url": url})
            except Exception:  # noqa: BLE001 - try the next provider
                continue
            text = _scrape_text(res)
            if text:
                return cache.put(url, text, source=slug)

    return _scrape_http(url)


def _scrape_text(res):
    data = res.get("data") if isinstance(res, dict) else getattr(res, "data", None)
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k in ("markdown", "content", "text", "html"):
                v = node.get(k)
                if isinstance(v, str) and len(v) > 200:
                    found.append(v)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return max(found, key=len) if found else None


def _scrape_http(url):
    """Fallback fetch. Marked as http in the cache so it is always obvious
    which pages did not come through Composio."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; scout-research/0.1)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return cache.put(url, "", source="http", status="error: %s" % exc)

    return cache.put(url, html_to_text(raw), source="http", title=_title(raw))


def _title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def html_to_text(html):
    """Good enough text extraction. We only need prose to quote snippets from,
    not a faithful render."""
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


# --- toolkit registry ------------------------------------------------------

_registry = None
_registry_lock = threading.Lock()


def composio_toolkit_slugs():
    """Every toolkit slug Composio ships, fetched once per process. Returns
    None when we cannot reach the registry, which is different from an empty
    set and keeps extras.composio_toolkit null instead of false."""
    global _registry
    if _registry is not None:
        return _registry

    c = composio()
    if c is None:
        return None

    # workers share this, and paginating 1500 toolkits sixteen times over is
    # pure waste, so the first thread in does the work and the rest wait
    with _registry_lock:
        if _registry is not None:
            return _registry
        return _build_registry(c)


def _build_registry(c):
    global _registry
    slugs, cursor = set(), None
    try:
        while True:
            page = c.client.toolkits.list(limit=100, cursor=cursor) if cursor \
                else c.client.toolkits.list(limit=100)
            items = getattr(page, "items", None) or []
            for t in items:
                slug = getattr(t, "slug", None)
                if slug:
                    slugs.add(slug.lower())
            cursor = getattr(page, "next_cursor", None)
            if not cursor or not items:
                break
    except Exception:  # noqa: BLE001
        return None

    _registry = slugs
    return _registry


def has_toolkit(app_name):
    """True or false when we can see the registry, None when we cannot."""
    slugs = composio_toolkit_slugs()
    if slugs is None:
        return None
    norm = re.sub(r"[^a-z0-9]", "", app_name.lower())
    return any(re.sub(r"[^a-z0-9]", "", s) == norm for s in slugs)
