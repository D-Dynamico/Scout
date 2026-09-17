"""Schema, enums, and a strict validator for one app record.

Every enum here mirrors CLAUDE.md exactly. Nothing gets added to these lists
without updating CLAUDE.md first. The validator rejects any value outside the
enums instead of coercing it, because a silently coerced value is a wrong
claim we would not notice.
"""

# --- enums, straight from CLAUDE.md ---------------------------------------

AUTH = ["OAuth2", "API key", "Basic", "Bearer", "custom", "none"]

ACCESS = [
    "free self-serve",
    "free trial",
    "paid plan",
    "admin approval",
    "partner or contact-sales",
    "no public API",
]

API_TYPE = ["REST", "GraphQL", "SDK-only", "CLI-only", "none"]

API_BREADTH = ["small", "medium", "large", "unknown"]

MCP = ["official", "community", "none"]

STAGE = [
    "ready to build",
    "needs sandbox",
    "needs OAuth app review",
    "needs partnership outreach",
    "no viable API",
]

# The 10 categories, taken from the app list. CLAUDE.md says "the 10 given
# categories" without listing them, so this is the list of record. A category
# outside these ten is a hard validation failure, not a new category.
CATEGORIES = [
    "CRM and Sales",
    "Support and Helpdesk",
    "Communications and Messaging",
    "Marketing, Ads, Email and Social",
    "Ecommerce",
    "Data, SEO and Scraping",
    "Developer, Infra and Data platforms",
    "Productivity and Project Management",
    "Finance and Fintech",
    "AI, Research and Media-native",
]

# Gated, defined operationally: a developer cannot get working credentials on
# their own, in under an hour, without paying or waiting on a human. Note this
# hangs off access, not stage. An app can be ungated for development and still
# need review before production, which is what prod_requires_review records.
GATED_ACCESS = ["paid plan", "admin approval", "partner or contact-sales"]

# Fields that are claims about the world and therefore need evidence.
# stage is derived, composio_toolkit comes from the Composio registry, and the
# identity fields come from apps.csv, so none of those need a page snippet.
EVIDENCE_REQUIRED = [
    "auth",
    "access",
    "api_type",
    "api_breadth",
    "api_docs_url",
    "mcp",
    "prod_requires_review",
]

# How much each claim field counts toward confidence. The four that decide the
# stage verdict carry full weight. The rest are useful but do not change what a
# developer has to do next, so a gap there should not read as badly as a gap in
# access. Weighting also means adding a field later cannot dilute the penalties
# on the ones that matter.
FIELD_WEIGHT = {
    "access": 1.0,
    "auth": 1.0,
    "api_type": 1.0,
    "prod_requires_review": 1.0,
    "api_breadth": 0.4,
    "mcp": 0.4,
    "api_docs_url": 0.3,
}

# URL fields are evidenced by the page being what it claims to be. That is
# provenance, which Loop B checks by resolving the URL. Asking a model whether
# a sentence "supports" a URL is close to meaningless, so these never take a
# support penalty.
SUPPORT_EXEMPT = ["api_docs_url", "mcp_url", "gate.application_url"]

# Whether the page a snippet came from belongs to the app itself or to someone
# else. Third party is not disqualifying: an MCP registry or a GitHub repo is a
# legitimate source. It is recorded so a reader can weigh it.
SUBJECT = ["own-domain", "third-party-confirmed"]

TOP_LEVEL_FIELDS = [
    "app_id", "name", "website", "category", "one_liner",
    "auth", "auth_notes",
    "access", "access_notes",
    "api_type", "api_breadth", "api_docs_url",
    "mcp", "mcp_url",
    "stage", "blocker", "prod_requires_review",
    "gate", "extras", "rejected", "evidence", "confidence", "notes",
]


def blank_record(app_id, name, website, category, one_liner=None):
    """An empty record. Every claim field starts null, which is the honest
    default: we have not looked yet."""
    return {
        "app_id": app_id,
        "name": name,
        "website": website,
        "category": category,
        "one_liner": one_liner,
        "auth": None,
        "auth_notes": None,
        "access": None,
        "access_notes": None,
        "api_type": None,
        "api_breadth": None,
        "api_docs_url": None,
        "mcp": None,
        "mcp_url": None,
        "stage": None,
        "blocker": None,
        "prod_requires_review": None,
        "gate": None,
        "rejected": None,
        "extras": {
            "sdks": [],
            "webhooks": None,
            "rate_limits": None,
            "composio_toolkit": None,
        },
        "evidence": [],
        "confidence": 0.0,
        "notes": "",
    }


# --- stage derivation ------------------------------------------------------

def derive_stage(access, api_type):
    """Stage is derived, never guessed. Rules are checked in this order, and
    the order matters: an app can match more than one rule, and the first
    match is the thing that actually blocks you from building.

    Returns (stage, blocker). Both are None when the inputs are null.
    """
    # 1. No programmatic surface at all beats every other consideration, and it
    # does not need the access question answered. If there is nothing to call,
    # how you would have signed up is irrelevant. Mermaid CLI is the case that
    # caught this: api_type CLI-only with access unknown was leaving the stage
    # blank when the answer was already certain.
    if access == "no public API" or api_type == "none":
        return "no viable API", "no public API"
    if api_type == "CLI-only":
        return "no viable API", "CLI only, nothing to call"

    if access is None or api_type is None:
        return None, None

    # 2. A partnership gate blocks you before any signup question.
    if access == "partner or contact-sales":
        return "needs partnership outreach", "partner or contact-sales"

    # 3. Review gates.
    # An app that is self-serve to build on but needs review before production
    # does not land here. It stays buildable and the review is recorded in
    # prod_requires_review, which is the distinction the column exists for.
    if access == "admin approval":
        return "needs OAuth app review", "admin approval required"

    # 4. Money or a clock between you and credentials.
    if access == "paid plan":
        return "needs sandbox", "paid plan required for credentials"
    if access == "free trial":
        return "needs sandbox", "trial only, no free tier for credentials"

    # 5. Free self-serve. Documented REST or GraphQL means go.
    if access == "free self-serve":
        if api_type in ("REST", "GraphQL"):
            return "ready to build", None
        # SDK-only is the leftover case CLAUDE.md does not name. An SDK is a
        # real programmatic surface, so it is buildable, and the awkwardness
        # goes in blocker rather than being hidden.
        return "ready to build", "SDK-only surface"

    return None, None


# --- validation ------------------------------------------------------------

def _enum(errors, field, value, allowed, nullable=True):
    if value is None:
        if not nullable:
            errors.append("%s: must not be null" % field)
        return
    if value not in allowed:
        errors.append("%s: %r is not one of %s" % (field, value, allowed))


def validate(record):
    """Return a list of problems. Empty list means the record is clean.

    We never repair a record here. A record that fails validation does not get
    written, so a bad value cannot reach data/raw/.
    """
    e = []

    unknown = [k for k in record if k not in TOP_LEVEL_FIELDS]
    if unknown:
        e.append("unknown fields: %s" % unknown)
    missing = [k for k in TOP_LEVEL_FIELDS if k not in record]
    if missing:
        e.append("missing fields: %s" % missing)
        return e

    if not isinstance(record["app_id"], int) or not 1 <= record["app_id"] <= 100:
        e.append("app_id: %r must be an int 1 to 100" % (record["app_id"],))
    for f in ("name", "website"):
        if not isinstance(record[f], str) or not record[f].strip():
            e.append("%s: must be a non-empty string" % f)

    if CATEGORIES:
        _enum(e, "category", record["category"], CATEGORIES, nullable=False)
    elif not record["category"]:
        e.append("category: must not be null")

    _enum(e, "auth", record["auth"], AUTH)
    _enum(e, "access", record["access"], ACCESS)
    _enum(e, "api_type", record["api_type"], API_TYPE)
    _enum(e, "api_breadth", record["api_breadth"], API_BREADTH)
    _enum(e, "mcp", record["mcp"], MCP)
    _enum(e, "stage", record["stage"], STAGE)

    # stage must match what the rules say, so nobody can hand-write one
    want, _ = derive_stage(record["access"], record["api_type"])
    if want is not None and record["stage"] != want:
        e.append(
            "stage: %r does not follow from access=%r api_type=%r (rules say %r)"
            % (record["stage"], record["access"], record["api_type"], want)
        )

    if record["mcp"] in ("official", "community") and not record["mcp_url"]:
        e.append("mcp_url: required when mcp is official or community")
    if record["mcp"] == "none" and record["mcp_url"]:
        e.append("mcp_url: must be null when mcp is none")

    if record["prod_requires_review"] not in (True, False, None):
        e.append("prod_requires_review: must be true, false, or null")

    # gate
    if record["access"] in GATED_ACCESS:
        g = record["gate"]
        if not isinstance(g, dict):
            e.append("gate: required when access is %r" % record["access"])
        else:
            for k in ("application_url", "process", "contact", "est_time"):
                if k not in g:
                    e.append("gate.%s: missing" % k)

    # extras
    x = record["extras"]
    if not isinstance(x, dict):
        e.append("extras: must be an object")
    else:
        for k in ("sdks", "webhooks", "rate_limits", "composio_toolkit"):
            if k not in x:
                e.append("extras.%s: missing" % k)
        if "sdks" in x and not isinstance(x["sdks"], list):
            e.append("extras.sdks: must be a list")
        if x.get("webhooks") not in (True, False, None):
            e.append("extras.webhooks: must be true, false, or null")
        if x.get("composio_toolkit") not in (True, False, None):
            e.append("extras.composio_toolkit: must be true, false, or null")

    rj = record["rejected"]
    if rj is not None:
        if not isinstance(rj, dict):
            e.append("rejected: must be an object keyed by field name or null")
        else:
            for field, item in rj.items():
                if field not in TOP_LEVEL_FIELDS:
                    e.append("rejected.%s: not a schema field" % field)
                if not isinstance(item, dict):
                    e.append("rejected.%s: must be {value, reason}" % field)
                    continue
                for k in ("value", "reason"):
                    if not item.get(k):
                        e.append("rejected.%s.%s: missing" % (field, k))
                # api_breadth is allowed to sit at "unknown" next to a
                # rejection. The rejected claim was a specific size, and what we
                # are left with really is unknown, so both facts are true and
                # both are worth keeping.
                settled = record.get(field)
                if settled is not None and not (field == "api_breadth"
                                                and settled == "unknown"):
                    e.append("rejected.%s: field must be null when a claim was rejected"
                             % field)

    # evidence
    ev = record["evidence"]
    if not isinstance(ev, list):
        e.append("evidence: must be a list")
        return e
    for i, item in enumerate(ev):
        if not isinstance(item, dict):
            e.append("evidence[%d]: must be an object" % i)
            continue
        for k in ("field", "url", "snippet", "fetcher", "support"):
            if not item.get(k):
                e.append("evidence[%d].%s: missing or empty" % (i, k))
        if item.get("support") and item["support"] not in ("strong", "weak"):
            e.append("evidence[%d].support: %r must be strong or weak"
                     % (i, item["support"]))
        # subject is optional: records written before Loop E existed do not
        # carry it, and a missing value is honest about that. When it is there
        # it has to be one of the two.
        if item.get("subject") and item["subject"] not in SUBJECT:
            e.append("evidence[%d].subject: %r must be one of %s"
                     % (i, item["subject"], SUBJECT))
        if item.get("field") and item["field"] not in TOP_LEVEL_FIELDS:
            e.append("evidence[%d].field: %r is not a schema field" % (i, item["field"]))

    # the core rule: no claim without a snippet.
    # api_breadth "unknown" is the one exception, and it is not really an
    # exception: "unknown" asserts nothing about the world, it records that the
    # pages did not say. It reads better in the table than a blank cell, which
    # looks like we forgot to look.
    backed = set(item.get("field") for item in ev if isinstance(item, dict))
    for f in EVIDENCE_REQUIRED:
        if f == "api_breadth" and record.get(f) == "unknown":
            continue
        if record.get(f) is not None and f not in backed:
            e.append("%s: has a value but no evidence entry" % f)

    c = record["confidence"]
    if not isinstance(c, (int, float)) or not 0.0 <= c <= 1.0:
        e.append("confidence: %r must be a float 0 to 1" % (c,))

    return e


def validate_or_raise(record):
    problems = validate(record)
    if problems:
        raise ValueError(
            "record for app %s failed validation:\n  - %s"
            % (record.get("app_id"), "\n  - ".join(problems))
        )
    return record
