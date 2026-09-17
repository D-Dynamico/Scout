"""Is this page about the right app?

This started life inside Loop E, which runs after extraction. That was too
late. Sherlock is four different products: an OSINT CLI, CloudFerro's Sherlock
AI, sherlocks.ai and usesherlock.ai. Search on the bare word returned five
pages about the wrong three, the extractor read CloudFerro's API key docs and
wrote access='free self-serve', and Loop E could only null the field
afterwards. Nulling is not the same as never having been wrong.

So the check lives here, on its own, and both callers use it:

  pipeline  runs it before extraction, so the wrong product's pages never
            reach the model at all
  Loop E    runs it after, as the safety net, and should now catch almost
            nothing

Two layers, cheapest first. The domain check is free, because apps.csv already
says which site belongs to the app. Only genuinely third-party pages cost a
model call, and third party is not disqualifying: MCP registries and GitHub are
legitimate sources.
"""

import re
import threading
from urllib.parse import urlparse

from . import extract

SUBJECT_PROMPT = """Below is text from the page at {url}.

The app we are cataloguing is {app}, and its own home is {website}. This page
is served from {page_host}, which is not that home.

Is this page primarily about {app}, the product that lives at {website}?

Answer NO if it is mainly about a different product that happens to share a
name, or about some other company's tool that merely mentions {app} in passing.
A company selling its own product under this name from its own domain is a
different product, however similar the name looks.
Answer YES if the page is about {app}, including a third party page that
documents an integration, wrapper, or MCP server for {app}.

--- PAGE TEXT ---
{text}
--- END ---

One word, YES or NO."""

_subject_cache = {}
_subject_lock = threading.Lock()


# On a code host the domain means nothing: github.com/sherlock-project/sherlock
# and github.com/someone-else/sherlock-mcp are different projects. What counts
# there is the owner and repo, so these hosts are compared by path.
CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "raw.githubusercontent.com")

# Hosts that carry many unrelated projects. On these the host means nothing and
# the project path is the real identity, so subject verdicts are keyed by path
# rather than by host. Mermaid CLI is why: npmjs.com/package/@mermaid-js/mermaid-cli
# is its own package and npmjs.com/package/@mermaidchart/cli is a different
# product, and a host level verdict let the second one condemn the first.
MULTI_PROJECT_HOSTS = CODE_HOSTS + (
    "npmjs.com", "pypi.org", "docs.rs", "crates.io", "packagist.org",
    "rubygems.org", "apis.io", "mintlify.app", "readme.io", "pkg.go.dev",
    "sourceforge.net", "gitee.com", "huggingface.co",
)

# Host labels that identify a code host or a doc host rather than a product, so
# they are never treated as the app's distinguishing word.
GENERIC_LABELS = {
    "com", "org", "net", "io", "ai", "co", "dev", "app",
    "www", "docs", "developer", "developers", "api", "help", "support",
    "github", "gitlab", "npmjs", "pypi", "readthedocs", "readme",
}


def host_of(url):
    return (urlparse(url or "").hostname or "").lower().replace("www.", "")


def repo_path(url):
    parts = [p for p in (urlparse(url or "").path or "").split("/") if p]
    return "/".join(parts[:2]).lower() if len(parts) >= 2 else ""


def is_code_host(host):
    return any(host == h or host.endswith("." + h) for h in CODE_HOSTS)


def domains_for(app):
    """The hosts we already know belong to this app, from apps.csv."""
    out = set()
    for u in (app.get("website"), app.get("docs_hint")):
        if u and u.startswith("http"):
            h = host_of(u)
            if h:
                out.add(h)
    return out


def primary_domain(app):
    """The one host to pin a first-party search to. The website wins over the
    docs hint, because a docs hint is often on a third-party docs platform and
    site: on readme.io would search every customer readme.io has."""
    for u in (app.get("website"), app.get("docs_hint")):
        h = host_of(u)
        if h and not is_code_host(h):
            return h
    return ""


def disambiguator(app):
    """A word that tells search which product we mean.

    Bare app names collide. Front, Close, Grain, Plain, Linear, Sherlock are
    all ordinary English words before they are companies, and searching the
    bare name returns the wrong product. The app's own URL usually carries a
    more specific string than its name: a repo owner like sherlock-project, or
    a domain label that is not simply the name again. When the URL adds
    nothing, the category is the fallback, because it is the only other thing
    apps.csv knows about the app.
    """
    flat = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    name_flat = flat(app.get("name"))

    for u in (app.get("website"), app.get("docs_hint")):
        if not u or not u.startswith("http"):
            continue
        host = host_of(u)
        if is_code_host(host):
            owner = repo_path(u).split("/")[0]
            if owner and flat(owner) != name_flat:
                return owner
        else:
            label = host.split(".")[0]
            if label and label not in GENERIC_LABELS and flat(label) != name_flat:
                return label

    return app.get("category") or ""


def is_own_domain(host, app, url=None):
    """Loose on purpose. docs.slack.dev is Slack's, pipedrive.readme.io is
    Pipedrive's, and neither shares a registrable domain with the app's website.
    A name token in the host is the signal that actually works."""
    if not host:
        return False

    if host in CODE_HOSTS:
        own = repo_path(app.get("website") or "") or repo_path(app.get("docs_hint") or "")
        if own and url:
            return repo_path(url) == own
        return False

    known = domains_for(app)
    for k in known:
        if host == k or host.endswith("." + k) or k.endswith("." + host):
            return True
    # Tokens come only from domains we already know are the app's. The app's
    # own name is deliberately not a source. sherlock.cloudferro.com has the
    # label "sherlock" and belongs to CloudFerro, so trusting the name would
    # hand a competitor's subdomain the same standing as the app's own docs.
    # A name is a word anyone can use in a hostname. A domain from apps.csv is
    # a fact about who owns what.
    tokens = []
    for k in known:
        tokens += [t for t in re.split(r"[^a-z0-9]+", k) if len(t) > 3
                   and t not in GENERIC_LABELS]

    # Label equality, not substring. Substring is how sherlocks.ai passed as
    # Sherlock's own domain and never reached the subject check at all: the
    # string "sherlock" is inside "sherlocks", so a different company's
    # lookalike domain was trusted outright. A host label either is the app's
    # word or it is not. docs.slack.dev still passes on the label "slack",
    # which is the case this looseness exists for.
    labels = [l for l in host.replace("-", "").split(".") if l]
    return any(t in labels for t in tokens)


def page_is_about(app, url, text):
    """One small call, cached per url, so a page shared by several evidence
    items is only ever judged once. Returns True, False, or None when the call
    could not run, because a check that did not run is not a check that failed.
    """
    key = (app["app_id"], url)
    with _subject_lock:
        if key in _subject_cache:
            return _subject_cache[key]

    prompt = SUBJECT_PROMPT.format(
        url=url, app=app["name"], website=app.get("website") or "unknown",
        page_host=host_of(url) or "an unknown host",
        text=(text or "")[:4000])

    # Asked twice, and a NO from either call wins.
    #
    # Temperature 0 is not determinism. usesherlock.ai/pricing and
    # sherlocks.ai/pricing both answered NO on one call and YES on the next,
    # and a single coin flip decided whether a different company's pricing
    # page became evidence for Sherlock's access model. The asymmetry is
    # deliberate and matches the rest of the design: letting the wrong
    # product's page through costs a reader more than dropping a page that was
    # probably fine, and every other page for this app is still there.
    votes = []
    for _ in range(2):
        try:
            answer = extract.call_text(prompt, max_tokens=1500) or ""
        except Exception:  # noqa: BLE001 - a check that could not run is not a fail
            answer = ""
        low = extract.normalise_snippet(answer)
        votes.append(True if low.startswith("yes")
                     else False if low.startswith("no") else None)
        if votes[-1] is False:
            break

    verdict = (False if False in votes
               else True if True in votes
               else None)

    with _subject_lock:
        _subject_cache[key] = verdict
    return verdict


def subject_key(url):
    """What a verdict applies to. Usually the host, but on a shared host a
    verdict is only about that one project."""
    h = host_of(url)
    if any(h == m or h.endswith("." + m) for m in MULTI_PROJECT_HOSTS):
        path = "/".join([s for s in (urlparse(url).path or "").split("/") if s][:3])
        return "%s/%s" % (h, path.lower())
    return h


def judge(app, pages, verdicts=None):
    """Decide, per host, whether that host is about this app.

    Verdicts aggregate per host rather than per page, and one NO condemns the
    host. Sherlock is why: the checker said no to one docs.cloudferro.com page
    and yes to another, and the yes let a different product's REST API through
    as evidence for auth. Pages on the app's own domain never reach the model.

    verdicts is an optional dict to accumulate into across several calls.
    """
    if verdicts is None:
        verdicts = {}
    for p in pages:
        url = p["url"] if isinstance(p, dict) else p
        if is_own_domain(host_of(url), app, url):
            continue
        key = subject_key(url)
        text = p.get("text") if isinstance(p, dict) else None
        v = page_is_about(app, url, text)
        if v is False:
            verdicts[key] = False
        elif key not in verdicts:
            # None means the check could not run, which is not a failure
            verdicts[key] = True
    return verdicts


def filter_pages(app, pages, verdicts=None):
    """Drop pages that are about a different product, before extraction.

    Returns (kept, dropped, verdicts). Pass the verdicts dict back in on the
    next call so a host condemned by one page stays condemned for the rest,
    which is what makes this behave like Loop E rather than like a per-page
    coin flip.
    """
    # No domain in apps.csv means no anchor, and without an anchor this check
    # has no authority. Paygent Connect is the case: its website is literally
    # "unknown", so every page counted as third party, every page went to the
    # model, and a check biased towards NO threw away eight of nine pages
    # including what were probably its real docs. A filter that cannot tell
    # first party from third party should not be deciding anything, so it
    # stands down and leaves the pages for Loop E.
    if not domains_for(app):
        return list(pages), [], (verdicts or {})

    verdicts = judge(app, pages, verdicts)
    kept, dropped = [], []
    for p in pages:
        if is_own_domain(host_of(p["url"]), app, p["url"]):
            kept.append(p)
        elif verdicts.get(subject_key(p["url"])) is False:
            dropped.append(p["url"])
        else:
            kept.append(p)
    return kept, dropped, verdicts


def demote_unofficial_mcp(rec, app):
    """"Official" is a claim about who publishes the server, not about quality.

    A repo that is not the app's own repo cannot publish the app's official
    MCP server, however well it works. Sherlock is the case: the extractor read
    a stranger's fork and a third party's FastMCP wrapper and called both
    official, which would tell a connector team the vendor supports this when
    nobody does. No model call, just who owns the page.

    Returns a note when it changed something, otherwise None.
    """
    if rec.get("mcp") != "official":
        return None
    backed_by_own = any(
        is_own_domain(host_of(e["url"]), app, e["url"])
        for e in (rec.get("evidence") or [])
        if e.get("field") in ("mcp", "mcp_url")
    )
    if backed_by_own:
        return None
    rec["mcp"] = "community"
    return ("mcp downgraded from official to community, its only evidence is a "
            "page the app does not own")
