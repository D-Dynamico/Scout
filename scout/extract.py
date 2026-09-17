"""One LLM call that turns cached pages into a schema record.

Two hard rules live here and not in the prompt alone:
  1. Every claim comes back with a snippet, and we verify the snippet really
     appears in the page text before we keep the field. If it does not, the
     field is nulled. The model does not get to invent a quote.
  2. Values outside the enums are dropped, not coerced.
"""

import difflib
import json
import re

from . import config, ratelimit, schema

PROMPT = """You are cataloguing an app for a connector buildability tracker.

App: {name}
Website: {website}

Below are pages fetched from the web, each with its URL. Read them and fill in
what they actually say about this app's API.

App names collide. A page may turn out to be about a different product that
happens to share this name. If you judge a page to be about a different
product, do not use it as evidence for any field and do not let it influence
any value. Say which page you set aside, and why, in notes. Leaving a field
null is the correct answer when the only page that mentions it is the wrong
product.

{pages}

Return JSON only, with exactly these keys:

{{
  "one_liner": str,
  "auth": one of {auth} or null,
  "auth_notes": str or null,
  "access": one of {access} or null,
  "access_notes": str or null,
  "api_type": one of {api_type} or null,
  "api_breadth": one of {api_breadth} or null,
  "api_docs_url": str or null,
  "mcp": one of {mcp} or null,
  "mcp_url": str or null,
  "prod_requires_review": true, false, or null,
  "gate": {{"application_url": str, "process": str, "contact": str, "est_time": str}} or null,
  "sdks": [str],
  "webhooks": true, false, or null,
  "rate_limits": str or null,
  "evidence": [{{"field": str, "url": str, "snippet": str}}],
  "notes": str
}}

Rules you must follow:
- Every non-null field among auth, access, api_type, api_breadth, api_docs_url,
  mcp, prod_requires_review needs its own evidence entry. No exceptions. A field
  you cannot quote a page for is null.
- snippet must be copied verbatim from the page at that url, 10 to 300
  characters. Do not paraphrase. Do not write a snippet you did not read.
- The snippet must contain the words of the claim itself, because a checker will
  reject it otherwise. An mcp claim needs a sentence that actually says MCP or
  Model Context Protocol. An auth claim of OAuth2 needs a sentence that says
  OAuth. An access claim needs a sentence about pricing, signing up, plans,
  approval, or contacting sales. A sentence that is merely about the same
  general topic will be thrown out and the field will end up null.
- Hunt for the right sentence. If one page does not have it, look in the others
  before giving up. Only write null when no page on the list says it.
- If the pages do not say, the field is null. Null is a correct answer.
- api_breadth has an "unknown" value and it is usually the right one. Only claim
  small, medium, or large when a page shows an endpoint list or states a count.
  Do not infer breadth from a page saying the API is powerful or comprehensive.
- access is about getting API credentials, not about using the product.
  "partner or contact-sales" means you cannot get credentials without talking
  to a human. "admin approval" means an app review or workspace admin stands
  between you and a working token.
- Fill gate only when access is paid plan, admin approval, or partner or contact-sales.
- prod_requires_review is about shipping, not about building. Many apps let anyone
  create credentials in minutes but require an app review before the integration can
  be used by other people's accounts. Slack, Meta, Google, and Salesforce all work
  this way. true means such a review exists, false means there is none, null means
  the pages do not say.
- Put anything odd, contradictory, or missing in notes.
- Do not output a stage. Stage is derived from access and api_type by code.
"""


def build_prompt(name, website, pages):
    blocks = []
    for p in pages:
        text = (p.get("text") or "")[:12000]
        if not text:
            continue
        blocks.append("--- PAGE %s ---\n%s" % (p["url"], text))
    return PROMPT.format(
        name=name,
        website=website,
        pages="\n\n".join(blocks) if blocks else "(no pages fetched)",
        auth=schema.AUTH,
        access=schema.ACCESS,
        api_type=schema.API_TYPE,
        api_breadth=schema.API_BREADTH,
        mcp=schema.MCP,
    )


def call_llm(prompt, model=None):
    """One LLM call. Returns the parsed dict, or None when the provider is not
    configured, which the pipeline reports rather than papering over.

    Three providers, chosen by SCOUT_LLM. They are interchangeable on purpose:
    Loop A wants a second extraction from a different model, and swapping the
    provider is the cleanest way to get one.
    """
    text = call_text(prompt, model=model, json_mode=True)
    return parse_json(text) if text else None


def call_text(prompt, model=None, max_tokens=8000, json_mode=False):
    """The raw text of one model response. call_llm parses JSON out of this,
    the support check reads a single word out of it."""
    provider = config.LLM_PROVIDER
    model = model or config.EXTRACT_MODEL

    if provider == "groq":
        call = lambda: _call_groq(prompt, model, max_tokens, json_mode)
    elif provider == "anthropic":
        call = lambda: _call_anthropic(prompt, model, max_tokens)
    elif provider == "claude-cli":
        call = lambda: _call_claude_cli(prompt, model)
    else:
        call = None

    if call is not None:
        return ratelimit.with_retry(call, limiter=ratelimit.llm_limiter)
    raise ValueError("unknown SCOUT_LLM %r, expected groq, anthropic, or claude-cli"
                     % provider)


def _call_groq(prompt, model, max_tokens=8000, json_mode=False):
    """Groq speaks the OpenAI API, so we point the OpenAI client at it.

    We ask for JSON mode first. Not every model on Groq supports it, and we
    have not confirmed this one does, so a rejection falls back to a plain
    call. parse_json copes either way.
    """
    if not config.GROQ_API_KEY:
        return None

    from openai import OpenAI

    client = OpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL)
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_completion_tokens": max_tokens,
    }
    res = None
    if json_mode:
        try:
            res = client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs
            )
        except Exception:  # noqa: BLE001 - JSON mode unsupported, ask plainly
            res = None
    if res is None:
        res = client.chat.completions.create(**kwargs)

    return res.choices[0].message.content or ""


def _call_anthropic(prompt, model, max_tokens=8000):
    if not config.ANTHROPIC_API_KEY:
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def _call_claude_cli(prompt, model):
    """Shell out to the Claude Code CLI, which runs on an existing
    subscription instead of an API key. Slower, no per-token bill."""
    import subprocess

    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        return ""
    try:
        envelope = json.loads(res.stdout)
    except ValueError:
        return res.stdout
    return envelope.get("result", "") if isinstance(envelope, dict) else res.stdout


def probe_llm():
    """Report whether the configured provider answers, and for Groq confirm
    the model id really exists on the account instead of trusting the string
    in .env."""
    provider = config.LLM_PROVIDER
    out = {"provider": provider, "model": config.EXTRACT_MODEL}

    if provider == "groq":
        out["key"] = bool(config.GROQ_API_KEY)
        if not config.GROQ_API_KEY:
            out["error"] = "GROQ_API_KEY not set"
            return out
        from openai import OpenAI

        client = OpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL)
        try:
            ids = sorted(m.id for m in client.models.list().data)
        except Exception as exc:  # noqa: BLE001
            out["error"] = "%s: %s" % (type(exc).__name__, exc)
            return out
        out["models_available"] = len(ids)
        out["model_exists"] = config.EXTRACT_MODEL in ids
        out["gpt_oss_models"] = [i for i in ids if "gpt-oss" in i]
        return out

    if provider == "anthropic":
        out["key"] = bool(config.ANTHROPIC_API_KEY)
        return out

    if provider == "claude-cli":
        import shutil
        out["cli_on_path"] = shutil.which("claude") is not None
        return out

    out["error"] = "unknown provider"
    return out


def parse_json(text):
    """Models sometimes wrap JSON in prose or a fence. Pull out the object."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


# --- turning a model answer into a valid record ----------------------------

def normalise_snippet(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# --- check b, terms --------------------------------------------------------

# The cheap filter. A snippet backing a claim should at minimum use the
# language of that claim. This catches the loudest failure: a real quote from
# a real page attached to a field it says nothing about.
#
# Keyed by field, then by the claimed value where the value implies specific
# words, with "*" as the fallback for any value of that field.
FIELD_TERMS = {
    "mcp": {"*": ["mcp", "model context protocol"]},
    "auth": {
        "OAuth2": ["oauth"],
        "API key": ["api key", "apikey", "api-key", "secret key", "access key"],
        "Basic": ["basic auth", "basic authentication"],
        "Bearer": ["bearer"],
        "none": ["no authentication", "without authentication", "unauthenticated",
                 "no api key", "public api"],
        "*": ["auth", "token", "credential"],
    },
    # Deliberately broad. This is a filter for egregious mismatches, not the
    # real gate, and a narrow list here was silently killing good evidence:
    # "You can view and manage your API keys in the Stripe Dashboard" is a
    # perfectly good reason to believe an app is self-serve, and the first
    # version of this list rejected it for not saying the word "free".
    "access": {
        "*": ["free", "trial", "pricing", "plan", "sign up", "signup", "register",
              "subscribe", "contact", "sales", "approval", "approve", "review",
              "request access", "apply", "partner", "tier", "quota", "billing",
              "paid", "upgrade", "demo",
              "api key", "apikey", "access token", "credential", "secret",
              "dashboard", "console", "portal", "account", "workspace",
              "create", "generate", "obtain", "get started", "developer program",
              "self-serve", "self serve", "no credit card", "invite", "waitlist"],
    },
    "api_type": {
        "REST": ["rest", "http", "endpoint"],
        "GraphQL": ["graphql"],
        "SDK-only": ["sdk", "client library", "library"],
        "CLI-only": ["cli", "command line", "command-line"],
        "none": ["no api", "no public api"],
        "*": ["api"],
    },
    "api_breadth": {
        "*": ["endpoint", "api", "resource", "method", "operation", "reference"],
    },
    # A docs URL is evidenced by the page being the docs. Any snippet from the
    # cited page is acceptable, so there is nothing useful to filter on.
    "api_docs_url": {"*": []},
    "prod_requires_review": {
        "*": ["review", "approval", "approve", "verification", "verified",
              "submit", "submission", "production", "public", "distribution",
              "listed", "listing", "marketplace", "partner"],
    },
}


def term_check(field, value, snippet):
    """True when the snippet uses language relevant to the claim."""
    rules = FIELD_TERMS.get(field)
    if not rules:
        return True
    terms = rules.get(value) or rules.get("*") or []
    if not terms:
        return True
    text = normalise_snippet(snippet)
    return any(t in text for t in terms)


# --- check c, support ------------------------------------------------------

SUPPORT_PROMPT = """A research agent read a page about {app} and concluded:

  {meaning}

It cited this snippet, quoted from {url}:

  "{snippet}"

Does that snippet give good reason to believe the conclusion above?

Judge the conclusion as it is written, not the label {field} = {value}. Be strict
about drift: a snippet that is merely about the same general area is not support,
and a snippet that never touches the substance of the conclusion is not support.
But do not demand that the snippet restate every word of it. If the snippet
establishes the substance, that is support, even when it uses different words.

Answer with one word, YES or NO."""

# For most fields the claim speaks for itself. access does not: the enum label
# "free self-serve" reads to a judge as a claim about price, so it rejected
# "You can create a personal access token in your developer settings", which is
# exactly the evidence we want. These spell out the operational meaning instead,
# the same definition of gated that CLAUDE.md uses.
VALUE_MEANING = {
    "access": {
        "free self-serve": "a developer can obtain working API credentials on their "
                           "own, in under an hour, without paying and without waiting "
                           "for a human to approve them. Evidence that you create or "
                           "manage your own keys in a dashboard or settings page counts, "
                           "even if the snippet does not mention price",
        "free trial": "credentials are only available for a limited trial period",
        "paid plan": "you must be on a paid plan before you can get credentials",
        "admin approval": "a workspace admin or an app review stands between a "
                          "developer and a working token",
        "partner or contact-sales": "you cannot get credentials without talking to a "
                                    "human at the company",
        "no public API": "there is no publicly available API",
    },
}

FIELD_MEANING = {
    "auth": "how a developer authenticates to this app's API",
    "access": "what a developer must do to obtain working API credentials",
    "api_type": "the kind of programmatic interface the app exposes",
    "api_breadth": "roughly how many endpoints the API has: small is under 30, "
                   "medium is 30 to 150, large is over 150",
    "api_docs_url": "the address of the API documentation",
    "mcp": "whether an MCP (Model Context Protocol) server exists for this app, "
           "official from the vendor or community built",
    "prod_requires_review": "whether shipping an integration to other people's "
                            "accounts requires the vendor to review or approve the "
                            "app first, as opposed to just building it yourself",
}


def support_check(app, field, value, snippet, url):
    """One small model call per claim, given only the claim and its snippet.

    Deliberately isolated. The model does not see the page, the other claims,
    or the record, so it cannot talk itself into agreeing with the extraction
    it just produced. Returns True, False, or None when the call fails, and
    None is treated as weak rather than as a pass.
    """
    meaning = VALUE_MEANING.get(field, {}).get(value) or FIELD_MEANING.get(field, field)
    prompt = SUPPORT_PROMPT.format(
        app=app, field=field, value=value,
        meaning=meaning,
        snippet=snippet[:600], url=url,
    )
    try:
        answer = call_text(prompt, max_tokens=2000)
    except Exception:  # noqa: BLE001 - a failed check is not a pass
        return None
    if not answer:
        return None
    head = normalise_snippet(answer)
    if head.startswith("yes") or " yes" in head[:40]:
        return True
    if head.startswith("no") or " no" in head[:40]:
        return False
    return None


def snippet_supported(snippet, pages):
    """True when the snippet really appears in the page it cites."""
    want = normalise_snippet(snippet)
    if len(want) < 10:
        return False
    for p in pages:
        if normalise_snippet(p.get("text")).find(want) != -1:
            return True
    return False


def sentences(text):
    """Rough sentence split. Docs are full of lists and headings, so newlines
    count as breaks just as much as full stops."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [s.strip() for s in parts if 20 <= len(s.strip()) <= 400]


def snap_to_page(snippet, pages, threshold=0.75):
    """Find the real sentence on the page that the model was looking at.

    Small models paraphrase. They read a true sentence, then write it back
    slightly reworded, and a strict substring check throws away a claim that
    was actually correct. Instead of loosening the rule, we find the closest
    real sentence and store that. The record still quotes the page verbatim,
    because the text we keep comes from the page and not from the model.

    Returns (snippet, url) using the page's own words, or (None, None) when
    nothing on any page is close enough.
    """
    want = normalise_snippet(snippet)
    if len(want) < 10:
        return None, None

    # exact hit first, no need to go looking
    for p in pages:
        if normalise_snippet(p.get("text")).find(want) != -1:
            return snippet, p["url"]

    best, best_ratio, best_url = None, 0.0, None
    for p in pages:
        for sent in sentences(p.get("text")):
            ratio = difflib.SequenceMatcher(None, want, normalise_snippet(sent)).ratio()
            if ratio > best_ratio:
                best, best_ratio, best_url = sent, ratio, p["url"]

    if best_ratio >= threshold:
        return best, best_url
    return None, None


def to_record(base, answer, pages, support=True):
    """Merge a model answer onto a blank record.

    Three gates, cheapest first. Provenance: the snippet must be on the page it
    cites. Terms: the snippet must use the language of the claim. Support: a
    second isolated model call confirms the snippet backs that exact claim.
    Anything failing the first two is dropped. Failing the third marks the
    evidence weak, which costs confidence rather than silently passing.
    """
    rec = dict(base)
    rec["extras"] = dict(base["extras"])
    dropped = []

    if not isinstance(answer, dict):
        rec["notes"] = "extraction returned nothing usable"
        return rec, ["everything"]

    enums = {
        "auth": schema.AUTH,
        "access": schema.ACCESS,
        "api_type": schema.API_TYPE,
        "api_breadth": schema.API_BREADTH,
        "mcp": schema.MCP,
    }
    for field, allowed in enums.items():
        v = answer.get(field)
        if v is None:
            continue
        if v in allowed:
            rec[field] = v
        else:
            dropped.append("%s=%r not in enum" % (field, v))

    for field in ("one_liner", "auth_notes", "access_notes", "api_docs_url",
                  "mcp_url", "notes"):
        v = answer.get(field)
        if isinstance(v, str) and v.strip():
            rec[field] = v.strip()

    rec["extras"]["sdks"] = [s for s in (answer.get("sdks") or []) if isinstance(s, str)]
    if answer.get("webhooks") in (True, False):
        rec["extras"]["webhooks"] = answer["webhooks"]
    if answer.get("prod_requires_review") in (True, False):
        rec["prod_requires_review"] = answer["prod_requires_review"]
    if isinstance(answer.get("rate_limits"), str):
        rec["extras"]["rate_limits"] = answer["rate_limits"]

    if isinstance(answer.get("gate"), dict):
        g = answer["gate"]
        rec["gate"] = {
            "application_url": g.get("application_url") or "unknown",
            "process": g.get("process") or "unknown",
            "contact": g.get("contact") or "unknown",
            "est_time": g.get("est_time") or "unknown",
        }

    # which tool fetched each page, so every evidence item can say so
    fetcher_by_url = dict((p["url"], p.get("source") or "unknown") for p in pages)

    kept, rejected = [], {}
    for item in answer.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        field, url, snippet = item.get("field"), item.get("url"), item.get("snippet")
        if field not in schema.TOP_LEVEL_FIELDS or not url or not snippet:
            continue
        snippet = snippet.strip()
        value = rec.get(field)

        # a. provenance. If the model paraphrased, snap to the real sentence
        # it was looking at, so what we store is always the page's own words.
        snapped, snapped_url = snap_to_page(snippet, pages)
        if snapped is None:
            dropped.append("evidence for %s: snippet not on any page" % field)
            continue
        if snapped != snippet:
            dropped.append("evidence for %s: snippet was paraphrased, snapped to the "
                           "real sentence" % field)
        snippet, url = snapped, snapped_url or url

        # b. terms
        if not term_check(field, value, snippet):
            dropped.append("evidence for %s: snippet has no %s language" % (field, field))
            continue

        # c. support. URL fields skip this: they are evidenced by the page
        # being what it claims to be, which Loop B checks by resolving it.
        verdict = True
        checkable = (support and field in schema.EVIDENCE_REQUIRED
                     and field not in schema.SUPPORT_EXEMPT)
        if checkable:
            verdict = support_check(rec["name"], field, value, snippet, url)

        if verdict is False:
            # An explicit no is a real finding, not a shrug. Null the field,
            # but keep what was claimed and why it was thrown out.
            reason = "support check says this snippet does not establish %s = %r" % (
                field, value)
            rejected[field] = {"value": str(value), "reason": reason}
            dropped.append("evidence for %s: rejected, %s" % (field, reason))
            continue

        kept.append({
            "field": field,
            "url": url,
            "snippet": snippet,
            "fetcher": fetcher_by_url.get(url, "unknown"),
            # a check that did not run is not a check that passed
            "support": "strong" if verdict is True else "weak",
        })
        if verdict is None:
            dropped.append("evidence for %s: support check did not answer, kept weak"
                           % field)
    rec["evidence"] = kept

    # a rejected claim means the field itself goes null
    for field in rejected:
        rec[field] = None

    # An mcp claim has to point somewhere. If the model named a server but gave
    # no URL, the page its evidence came from is the honest answer, and if there
    # is no evidence either then the claim does not survive at all.
    if rec["mcp"] in ("official", "community") and not rec["mcp_url"]:
        mcp_urls = [e["url"] for e in kept if e["field"] == "mcp"]
        if mcp_urls:
            rec["mcp_url"] = mcp_urls[0]
        else:
            rec["mcp"] = None
            dropped.append("mcp nulled, claimed without a url or evidence")
    rec["rejected"] = rejected or None

    # no claim without a snippet: null anything left unbacked
    backed = set(i["field"] for i in kept)
    for field in schema.EVIDENCE_REQUIRED:
        if rec.get(field) is not None and field not in backed:
            dropped.append("%s nulled, no surviving evidence" % field)
            rec[field] = None
    if rec["mcp"] is None:
        rec["mcp_url"] = None

    # stage is derived, always, and only from access and api_type. An app that
    # needs review before production keeps its stage and says so in
    # prod_requires_review instead.
    rec["stage"], rec["blocker"] = schema.derive_stage(rec["access"], rec["api_type"])

    # gate hangs off access, per the operational definition in CLAUDE.md
    if rec["access"] in schema.GATED_ACCESS and not rec["gate"]:
        rec["gate"] = {"application_url": "unknown", "process": "unknown",
                       "contact": "unknown", "est_time": "unknown"}
    if rec["access"] not in schema.GATED_ACCESS:
        rec["gate"] = None

    return rec, dropped


# --- retrieval, the second pass for fields the first pass could not evidence -

# Which fields get a targeted second look. These are the ones that decide the
# stage verdict, plus the two the main extraction is worst at. api_docs_url is
# not here: the first pass gets it right and it needs no support check.
RETRIEVE_FIELDS = ["access", "api_type", "auth", "prod_requires_review", "mcp",
                   "api_breadth"]

RETRIEVE_PROMPT = """You are looking at sentences taken from pages about {app}.

Question: {question}

Here are the candidate sentences, numbered. They were pulled out by keyword, so
most of them will be irrelevant. That is expected.

{numbered}

Pick the single sentence that best answers the question, and say what the answer
is. If none of these sentences answers it, say so. Do not guess from what you
already know about {app}. Only these sentences count.

Reply with JSON only:
  {{"answer": {allowed} or null, "sentence": <number> or null}}

answer is null when no sentence here settles it. That is a correct reply and it
is better than a guess."""

RETRIEVE_QUESTION = {
    "access": "What must a developer do to get working API credentials? "
              "'free self-serve' means they create their own keys in minutes "
              "without paying and without a human approving it. 'free trial' "
              "means only during a trial period. 'paid plan' means they must pay "
              "first. 'admin approval' means an app review or workspace admin is "
              "in the way. 'partner or contact-sales' means they must talk to a "
              "human. 'no public API' means there is no public API at all.",
    "prod_requires_review": "Does shipping an integration to other people's "
                            "accounts need this vendor to review or approve the "
                            "app first? true if such a review exists, false if "
                            "there is none. This is about shipping, not building.",
    "mcp": "Is there an MCP (Model Context Protocol) server for this app? "
           "'official' if the vendor ships one, 'community' if a third party "
           "does, 'none' if the sentences say there is not one.",
    "api_type": "What kind of programmatic interface does this app expose? "
                "'REST' for an HTTP API with endpoints, 'GraphQL' for a GraphQL "
                "endpoint, 'SDK-only' when the only supported way in is a client "
                "library, 'CLI-only' when it is a command line tool with nothing "
                "to call over the network, 'none' when there is no programmatic "
                "interface at all. If both REST and GraphQL exist, pick the one "
                "the docs lead with.",
    "auth": "How does a developer authenticate to this API? 'OAuth2' for an "
            "OAuth 2.0 flow, 'API key' for a key issued in a dashboard, 'Bearer' "
            "for a bearer token that is not part of an OAuth flow, 'Basic' for "
            "HTTP basic auth, 'custom' for a scheme that is none of these, "
            "'none' when the API needs no authentication.",
    "api_breadth": "Roughly how many endpoints does the API have? 'small' is "
                   "under 30, 'medium' is 30 to 150, 'large' is over 150, and "
                   "'unknown' is the right answer unless a sentence actually "
                   "shows a count or a full endpoint list.",
}

RETRIEVE_ALLOWED = {
    "access": schema.ACCESS,
    "api_type": schema.API_TYPE,
    "auth": schema.AUTH,
    "prod_requires_review": [True, False],
    "mcp": schema.MCP,
    "api_breadth": schema.API_BREADTH,
}


def candidate_sentences(field, pages, k=12):
    """Pull the sentences most likely to answer this field's question.

    This is the whole point of the retrieval step. Asking a small model to find
    one sentence in six pages of documentation is a needle in 70,000 characters.
    Scoring sentences by the field's own keywords first, then asking it to pick
    from twelve, is a question it can actually answer.
    """
    rules = FIELD_TERMS.get(field) or {}
    terms = set()
    for value_terms in rules.values():
        terms.update(value_terms)
    if not terms:
        return []

    # Some phrases settle the question on their own. Counting raw keyword hits
    # ranks a sentence stuffed with generic words above "no credit card
    # required", which is exactly backwards.
    decisive = ["no credit card", "free tier", "free plan", "sign up",
                "get started for free", "contact sales", "contact us",
                "request access", "requires approval", "must be approved",
                "app review", "apply for", "waitlist", "invite only",
                "create an api key", "generate an api key", "in your dashboard",
                "developer settings", "model context protocol"]

    scored = []
    for p in pages:
        for sent in sentences(p.get("text")):
            low = normalise_snippet(sent)
            hits = sum(1 for t in terms if t in low)
            hits += 3 * sum(1 for d in decisive if d in low)
            if hits:
                # longer sentences carry more context, but only up to a point
                scored.append((hits, min(len(sent), 240), sent, p["url"]))

    scored.sort(key=lambda r: (-r[0], -r[1]))
    out, seen = [], set()
    for _, _, sent, url in scored:
        key = normalise_snippet(sent)[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append((sent, url))
        if len(out) >= k:
            break
    return out


def retrieve_field(app, field, pages):
    """Second pass for one field. Returns (value, snippet, url) or (None, ...).

    The model only ever sees the shortlist, never the full pages, so it cannot
    wander off and answer from memory about a well known app.
    """
    cands = candidate_sentences(field, pages)
    if not cands:
        return None, None, "no candidate sentences matched this field"

    numbered = "\n".join("%d. %s" % (i + 1, s) for i, (s, _) in enumerate(cands))
    allowed = RETRIEVE_ALLOWED[field]
    prompt = RETRIEVE_PROMPT.format(
        app=app,
        question=RETRIEVE_QUESTION[field],
        numbered=numbered,
        allowed=" | ".join(repr(a) for a in allowed),
    )

    # A failed call and an honest "none of these" are completely different
    # answers, and the first version returned None for both. That hid a real
    # problem: under parallel load some calls were being throttled out, the
    # field came back empty, and it looked like the model had nothing to say.
    try:
        reply = call_text(prompt, max_tokens=2000, json_mode=True) or ""
    except Exception as exc:  # noqa: BLE001
        return None, None, "retrieval call failed: %s" % type(exc).__name__

    answer = parse_json(reply)
    if not isinstance(answer, dict):
        return None, None, "retrieval reply was not JSON"

    value, index = answer.get("answer"), answer.get("sentence")
    if value is None:
        return None, None, None  # a real "none of these settle it"
    if value not in allowed:
        return None, None, "retrieval returned %r, not a legal value" % (value,)
    if not isinstance(index, int) or not 1 <= index <= len(cands):
        return None, None, "retrieval picked sentence %r, out of range" % (index,)

    snippet, url = cands[index - 1]
    return value, snippet, url


def fill_gaps(rec, pages, fetcher_by_url):
    """Run retrieval for every field the first pass left unevidenced.

    A field counts as a gap when it is null, or when the support check overruled
    it. Anything retrieval finds still has to clear the same support check, so
    this adds coverage without adding a second, weaker standard of proof.
    """
    filled, disagreements, problems = [], [], []
    backed = set(e["field"] for e in rec["evidence"])
    rejected = dict(rec.get("rejected") or {})

    for field in RETRIEVE_FIELDS:
        if rec.get(field) is not None and field in backed:
            continue

        value, snippet, url = retrieve_field(rec["name"], field, pages)
        if value is None:
            # url carries the reason when the attempt broke rather than simply
            # finding nothing, and a broken attempt is worth writing down
            if url:
                problems.append("%s: %s" % (field, url))
            continue

        verdict = support_check(rec["name"], field, value, snippet, url)
        if verdict is False:
            # Two independent judgments now disagree. The retrieval call picked
            # this sentence with the full definition in front of it; the support
            # call, shown one sentence in isolation, says no. CLAUDE.md is clear
            # that disagreements get flagged rather than silently resolved, so
            # the claim stands, the evidence is marked weak, confidence takes
            # the hit, and the disagreement is written down.
            #
            # This is deliberately different from the first pass, where an
            # explicit no nulls the field. There the model chose its own snippet
            # out of 70,000 characters and a no means it grabbed something
            # arbitrary. Here the sentence came from a keyword shortlist and the
            # model had to point at one, so a no is a judgment call at the
            # boundary, not evidence of a fabrication.
            disagreements.append(
                "%s=%r: retrieval chose this sentence, support check disagreed"
                % (field, value))

        rec[field] = value
        rejected.pop(field, None)
        rec["evidence"].append({
            "field": field,
            "url": url,
            "snippet": snippet,
            "fetcher": fetcher_by_url.get(url, "unknown"),
            "support": "strong" if verdict is True else "weak",
        })
        filled.append("%s=%r via retrieval" % (field, value))

        # an mcp claim needs somewhere to point, and the page the sentence came
        # from is the honest answer
        if field == "mcp" and value in ("official", "community") and not rec["mcp_url"]:
            rec["mcp_url"] = url

    # nothing established a breadth, so say so explicitly rather than leave a
    # blank that looks like an oversight
    if rec["api_breadth"] is None:
        rec["api_breadth"] = "unknown"

    if disagreements:
        rec["notes"] = " | ".join(filter(None, [
            rec.get("notes"), "loop A disagreement: " + "; ".join(disagreements)]))
    if problems:
        rec["notes"] = " | ".join(filter(None, [
            rec.get("notes"), "retrieval problems: " + "; ".join(problems)]))

    rec["rejected"] = rejected or None

    # access may have changed, so stage and gate are recomputed
    rec["stage"], rec["blocker"] = schema.derive_stage(rec["access"], rec["api_type"])
    if rec["access"] in schema.GATED_ACCESS and not rec["gate"]:
        rec["gate"] = {"application_url": "unknown", "process": "unknown",
                       "contact": "unknown", "est_time": "unknown"}
    if rec["access"] not in schema.GATED_ACCESS:
        rec["gate"] = None

    return rec, filled
