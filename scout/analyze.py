"""Crosstabs for the page. Writes data/patterns.json.

This file counts things. It does not write the headline claims: those are five
to seven sentences a human writes after looking at these numbers, because a
sentence generated from a crosstab reads like a sentence generated from a
crosstab.
"""

import collections
import json

from . import config, schema


def load_verified():
    recs = []
    for p in sorted(config.VERIFIED.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(r, dict) and "app_id" in r:
            recs.append(r)
    return sorted(recs, key=lambda r: r["app_id"])


def _count(recs, field, null_label="not determined"):
    return dict(collections.Counter(
        str(r.get(field)) if r.get(field) is not None else null_label
        for r in recs))


def _by_category(recs, field, null_label="not determined"):
    out = {}
    for cat in schema.CATEGORIES:
        rows = [r for r in recs if r["category"] == cat]
        out[cat] = _count(rows, field, null_label)
    return out


# Which apps a reader would recognise. Used only for the 2x2, and hand written
# rather than inferred, because "well known" is a judgment and pretending a
# metric decided it would be dishonest.
WELL_KNOWN = {
    "Salesforce", "HubSpot", "Pipedrive", "Zoho CRM", "Zendesk", "Intercom",
    "Freshdesk", "Slack", "Twilio", "Discord", "Telegram", "WhatsApp Business",
    "Google Ads", "Meta Ads", "LinkedIn Ads", "Mailchimp", "Klaviyo",
    "Pinterest", "Threads (Meta)", "SendGrid", "Shopify", "WooCommerce",
    "BigCommerce", "Magento (Adobe Commerce)", "Squarespace",
    "Amazon Selling Partner", "Ahrefs", "Apify", "Bright Data", "GitHub",
    "Vercel", "Netlify", "Cloudflare", "Supabase", "Snowflake", "MongoDB Atlas",
    "Datadog", "Sentry", "Notion", "Airtable", "Linear", "Jira", "Asana",
    "Monday.com", "ClickUp", "Smartsheet", "Stripe", "Plaid", "Binance",
    "QuickBooks", "Xero", "Brex", "Ramp", "PitchBook", "NotebookLM", "Otter AI",
    "Devin", "Salesforce Commerce Cloud", "Front", "Help Scout", "Neo4j",
}

EASY_STAGES = {"ready to build"}


def quadrant(rec):
    """Ease of build against whether it is worth building. Ease comes from
    stage, worth comes from name recognition plus API surface."""
    easy = rec.get("stage") in EASY_STAGES
    worth = rec["name"] in WELL_KNOWN or rec.get("api_breadth") in ("medium", "large")
    if easy and worth:
        return "build now"
    if easy and not worth:
        return "easy, niche"
    if not easy and worth:
        return "worth the paperwork"
    return "park it"


def analyze():
    recs = load_verified()
    if not recs:
        raise FileNotFoundError("no verified records, run scout verify --all first")

    stage_counts = _count(recs, "stage")
    blockers = collections.Counter(
        r["blocker"] for r in recs if r.get("blocker"))

    gated = [r for r in recs if r.get("access") in schema.GATED_ACCESS]
    outreach = {}
    for r in gated:
        outreach.setdefault(r["access"], []).append({
            "app_id": r["app_id"], "name": r["name"], "category": r["category"],
            "stage": r["stage"], "gate": r.get("gate"),
            "confidence": r["confidence"],
        })

    quads = collections.Counter(quadrant(r) for r in recs)
    quad_members = {}
    for r in recs:
        quad_members.setdefault(quadrant(r), []).append(r["name"])

    mcp_by_cat = {}
    for cat in schema.CATEGORIES:
        rows = [r for r in recs if r["category"] == cat]
        have = sum(1 for r in rows if r.get("mcp") in ("official", "community"))
        mcp_by_cat[cat] = {"with_mcp": have, "total": len(rows)}

    # how much of this we could not settle, kept next to the findings rather
    # than in a footnote
    unresolved = {
        "stage_not_determined": sum(1 for r in recs if not r.get("stage")),
        "rows_with_loop_a_disagreement": sum(
            1 for r in recs if "loop A:" in (r.get("notes") or "")),
        "rows_with_a_rejected_claim": sum(1 for r in recs if r.get("rejected")),
        "rows_below_confidence_0_7": sum(1 for r in recs if r["confidence"] < 0.7),
        "weak_evidence_items": sum(
            1 for r in recs for e in r["evidence"] if e.get("support") == "weak"),
        "total_evidence_items": sum(len(r["evidence"]) for r in recs),
    }

    patterns = {
        "n": len(recs),
        "auth": _count(recs, "auth"),
        "auth_by_category": _by_category(recs, "auth"),
        "access": _count(recs, "access"),
        "access_by_category": _by_category(recs, "access"),
        "stage": stage_counts,
        "stage_by_category": _by_category(recs, "stage"),
        "api_type": _count(recs, "api_type"),
        "api_breadth": _count(recs, "api_breadth"),
        "mcp": _count(recs, "mcp"),
        "mcp_by_category": mcp_by_cat,
        "prod_requires_review": _count(recs, "prod_requires_review"),
        "blockers": dict(blockers.most_common()),
        "quadrant": dict(quads),
        "quadrant_members": quad_members,
        "outreach_queue": outreach,
        "composio_toolkit": {
            "already_shipped": sum(
                1 for r in recs if r["extras"].get("composio_toolkit")),
            "not_shipped": sum(
                1 for r in recs if r["extras"].get("composio_toolkit") is False),
        },
        "confidence": {
            "median": sorted(r["confidence"] for r in recs)[len(recs) // 2],
            "min": min(r["confidence"] for r in recs),
            "max": max(r["confidence"] for r in recs),
        },
        "unresolved": unresolved,
    }

    (config.DATA / "patterns.json").write_text(
        json.dumps(patterns, indent=2, ensure_ascii=False), encoding="utf-8")
    return patterns
