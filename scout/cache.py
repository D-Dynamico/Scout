"""Page cache keyed by URL. We never fetch the same URL twice.

One file per URL under cache/, named by a hash of the URL so Windows path
rules and query strings cannot bite us. The URL itself is stored inside the
file, so the cache is readable without a lookup table.
"""

import hashlib
import json
import time

from . import config


def key(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def path(url):
    return config.CACHE / (key(url) + ".json")


def get(url):
    """Return the cached page dict, or None if we have no usable copy.

    A failed fetch is recorded but never served back as a hit. Otherwise one
    bad afternoon poisons the cache forever: the first run stores a 403 with
    empty text, and every later run happily reuses the failure instead of
    trying again with a tool that now works. Successful pages are still only
    ever fetched once, which is what the no-refetch rule is actually for.
    """
    p = path(url)
    if not p.exists():
        return None
    try:
        page = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if page.get("status") != "ok" or not page.get("text"):
        return None
    return page


def put(url, text, source, status="ok", title=None):
    """Store a fetched page. source says which tool got it, which matters when
    we later explain how a claim was found."""
    config.CACHE.mkdir(parents=True, exist_ok=True)
    page = {
        "url": url,
        "title": title,
        "text": text,
        "source": source,
        "status": status,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path(url).write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    return page
