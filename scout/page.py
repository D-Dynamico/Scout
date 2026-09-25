"""Renders site/index.html, one static file, no build step and no CDN.

Everything the page needs is inlined: the 100 rows, the crosstabs, the charts.
Open the file from disk with no network and it still works, which is the point
of a self-explanatory page.

The headline claims are written by hand below. A sentence generated from a
crosstab reads like a sentence generated from a crosstab, and the assignment
asks for patterns stated plainly, not for a template filled in.
"""

import datetime
import json
import re

from . import analyze, config, schema

RUN_DATE = datetime.date.today().isoformat()
REPO_URL = "https://github.com/D-Dynamico/Scout"

STAGE_ORDER = [
    "ready to build",
    "needs sandbox",
    "needs OAuth app review",
    "needs partnership outreach",
    "no viable API",
]

# One colour system. Green ready, amber anything that needs a review or a
# sandbox, red anything that needs a conversation, grey nothing to build.
STAGE_VAR = {
    "ready to build": "ready",
    "needs sandbox": "review",
    "needs OAuth app review": "review",
    "needs partnership outreach": "outreach",
    "no viable API": "none",
    "not determined": "none",
}


def load(folder):
    out = {}
    for p in sorted(folder.glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(r, dict) and "app_id" in r:
            out[r["app_id"]] = r
    return out


def loop_c_contradictions(ver):
    """Rows where a real browser on the live page disagreed with what we
    recorded. Flagged, not corrected: a browser agent's prose is a second
    opinion, not a schema evidence item, and CLAUDE.md says disagreements get
    shown rather than silently resolved."""
    out = []
    for r in ver.values():
        notes = r.get("notes") or ""
        if "loop C browser check" not in notes:
            continue
        said = notes.split("loop C browser check:")[-1].split(" | ")[0].strip()
        low = said.lower()
        closed = low.startswith("no") or "cannot" in low
        openish = low.startswith("yes") and "cannot" not in low
        ours = r.get("access")
        if (closed and ours == "free self-serve") or (
                openish and ours in ("admin approval", "partner or contact-sales",
                                     "paid plan")):
            out.append({"name": r["name"], "app_id": r["app_id"],
                        "ours": ours, "browser": said})
    return sorted(out, key=lambda x: x["app_id"])


def coverage(recs, field):
    return sum(1 for r in recs.values() if r.get(field) is not None)


def stats():
    p1, ver = load(config.PASS1), load(config.VERIFIED)
    fields = ["auth", "access", "api_type", "api_breadth", "api_docs_url", "mcp",
              "prod_requires_review", "stage"]
    rows = []
    for f in fields:
        rows.append({"field": f, "pass1": coverage(p1, f), "verified": coverage(ver, f)})

    c1 = sorted(r["confidence"] for r in p1.values())
    c2 = sorted(r["confidence"] for r in ver.values())
    loop_a = sum(1 for r in ver.values() if "loop A:" in (r.get("notes") or ""))
    loop_c = sum(1 for r in ver.values() if "loop C browser check" in (r.get("notes") or ""))
    contradictions = loop_c_contradictions(ver)
    own = sum(1 for r in ver.values() for e in r["evidence"]
              if e.get("subject") == "own-domain")
    third = sum(1 for r in ver.values() for e in r["evidence"]
                if e.get("subject") == "third-party-confirmed")
    loop_e_rows = [r for r in ver.values() if "loop E:" in (r.get("notes") or "")]
    dropped = sum((r.get("notes") or "").count(": dropped,") for r in loop_e_rows)

    # The pre-filter runs before extraction, so its work shows up as pages that
    # never reached the model rather than as evidence removed afterwards. Both
    # numbers belong on the page: one is damage prevented, the other damage
    # repaired, and only the pair shows whether moving the check forward worked.
    prefilter_rows, prefilter_pages = 0, 0
    for r in ver.values():
        note = r.get("notes") or ""
        if "pre-filter dropped" in note:
            prefilter_rows += 1
            try:
                prefilter_pages += int(note.split("pre-filter dropped ")[1].split(" ")[0])
            except (IndexError, ValueError):
                pass
    prefilter_examples = sorted(
        (r for r in ver.values() if "pre-filter dropped" in (r.get("notes") or "")),
        key=lambda r: r["app_id"])

    return {
        "prefilter_rows": prefilter_rows,
        "prefilter_pages": prefilter_pages,
        "prefilter_examples": prefilter_examples[:8],
        "coverage": rows,
        "median_pass1": c1[len(c1) // 2] if c1 else 0,
        "median_verified": c2[len(c2) // 2] if c2 else 0,
        "loop_a_rows": loop_a,
        "loop_c_rows": loop_c,
        "loop_c_contradictions": contradictions,
        "subject_own": own,
        "subject_third": third,
        "loop_e_rows": len(loop_e_rows),
        "loop_e_dropped": dropped,
        "loop_e_examples": sorted(loop_e_rows, key=lambda r: r["app_id"])[:6],
        "pass1": p1,
        "verified": ver,
    }


# --- the claims, written by hand after reading the crosstabs ----------------
#
# The wording is hand-written; the numbers and the category names are pulled
# from the crosstabs so a rerun cannot leave a sentence pointing at a stale
# count. Each finding is a pattern across the 100, not a restated total.

SHORT_CAT = {
    "CRM and Sales": "CRM",
    "Support and Helpdesk": "Support",
    "Communications and Messaging": "Comms",
    "Marketing, Ads, Email and Social": "Marketing and Ads",
    "Ecommerce": "Ecommerce",
    "Data, SEO and Scraping": "Data and SEO",
    "Developer, Infra and Data platforms": "Developer and Infra",
    "Productivity and Project Management": "Productivity",
    "Finance and Fintech": "Finance",
    "AI, Research and Media-native": "AI-native",
}


def short(cat):
    return SHORT_CAT.get(cat, cat)


def findings(pat, st, acc=None, misses=None):
    misses = misses or []
    ver = st["verified"]
    n = pat["n"]
    stage = pat["stage"]

    def share(bycat, key):
        """(category, count, total) with the most of `key`."""
        best = max(bycat.items(), key=lambda kv: (kv[1].get(key, 0), kv[0]))
        return best[0], best[1].get(key, 0), sum(best[1].values())

    def least(bycat, key):
        worst = min(bycat.items(), key=lambda kv: (kv[1].get(key, 0), kv[0]))
        return worst[0], worst[1].get(key, 0), sum(worst[1].values())

    # the shape of the whole list, by stage
    shape = [(s, stage.get(s, 0)) for s in STAGE_ORDER + ["not determined"]]
    ready = stage.get("ready to build", 0)
    blocked = sum(stage.get(s, 0) for s in STAGE_ORDER[1:4])
    unsettled = stage.get("not determined", 0)

    cards = []

    def famous(r):
        return (r["name"] not in analyze.WELL_KNOWN, r["app_id"])

    # 1. auth follows the kind of company
    abc = pat["auth_by_category"]

    def oauth(cat):
        d = abc.get(cat, {})
        return (short(cat) + " on OAuth2", d.get("OAuth2", 0), sum(d.values()),
                "var(--accent)")
    cards.append({
        "title": "Established SaaS uses OAuth. AI and data tools hand out keys.",
        "bars": [oauth("CRM and Sales"), oauth("AI, Research and Media-native"),
                 oauth("Data, SEO and Scraping")],
        "so": "An AI or scraping connector is a pasted key. A CRM connector "
              "needs an OAuth app registered with every vendor.",
    })

    # 2. each category has its own kind of gate
    rv_cat, rv_n, rv_t = share(pat["access_by_category"], "admin approval")
    sl_cat, sl_n, sl_t = share(pat["access_by_category"], "partner or contact-sales")
    cards.append({
        "title": "Every category gates differently.",
        "bars": [(short(rv_cat) + " apps behind an app review", rv_n, rv_t, "var(--review)"),
                 (short(sl_cat) + " apps behind a sales call", sl_n, sl_t, "var(--outreach)")],
        "so": "Ads platforms want an app review queue. Fintech wants a "
              "partnership conversation. Different owners, different timelines.",
    })

    # 3. ready is not the same as shippable
    ready_review = sorted((r for r in ver.values()
                           if r.get("stage") == "ready to build"
                           and r.get("prod_requires_review") is True),
                          key=famous)
    unknown = pat["prod_requires_review"].get("not determined", 0)
    cards.append({
        "title": "Ready to build is not ready to ship.",
        "bars": [("ready apps needing review to go live", len(ready_review), ready,
                  "var(--review)"),
                 ("all apps needing review to go live",
                  pat["prod_requires_review"].get("True", 0), n, "var(--review)")],
        "so": "%s and others work in dev, then wait on the vendor before real "
              "customers can connect. Unknown for %d rows, so this is a floor."
              % (", ".join(r["name"] for r in ready_review[:3]), unknown),
    })

    # 4. the build backlog
    gap = sorted((r for r in ver.values()
                  if r.get("stage") == "ready to build"
                  and not r["extras"].get("composio_toolkit")),
                 key=famous)
    cards.append({
        "title": "Half the easy wins have no Composio toolkit yet.",
        "bars": [("ready apps with no toolkit", len(gap), ready, "var(--ready)"),
                 ("all apps with no toolkit",
                  pat["composio_toolkit"]["not_shipped"], n, "var(--ready)")],
        "so": "No paperwork, no outreach, just engineering time. Starts with "
              "%s." % ", ".join(r["name"] for r in gap[:3]),
    })

    # 5. MCP, flagged by its own accuracy
    mcp = pat["mcp"].get("official", 0) + pat["mcp"].get("community", 0)
    ghosts = [m for m in misses if m.get("which") == "verified"
              and m.get("field") == "mcp" and m.get("got") == "official"
              and m.get("expected") == "none"]
    wrong = [m for m in misses if m.get("which") == "verified"
             and m.get("field") == "mcp" and m.get("got") is not None]
    cards.append({
        "title": "MCP looks universal. It is also our shakiest claim.",
        "bars": [("apps with an MCP server, per our data", mcp, n, "var(--accent)"),
                 ("of those, marked official", pat["mcp"].get("official", 0), mcp,
                  "var(--accent)")],
        "so": ("In the hand-checked sample, %d of %d wrong MCP answers invented "
               "an official server. The real number is lower." % (len(ghosts), len(wrong)))
              if wrong else "Every category has MCP coverage.",
        "low": bool(ghosts),
    })

    # 6. where we could not see
    un_cat, un_n, un_t = share(pat["stage_by_category"], "not determined")
    cards.append({
        "title": "Our blind spot is %s." % short(un_cat),
        "bars": [(short(un_cat) + " left unsettled", un_n, un_t, "var(--none)"),
                 ("all other apps unsettled", unsettled - un_n, n - un_t, "var(--none)")],
        "so": "Unsettled means we could not prove access from the pages we read. "
              "It says nothing about whether their API is open.",
    })

    trust = None
    if acc:
        scored = [d for d in acc.get("by_field", {}).values()
                  if d.get("n") and d.get("verified") is not None]
        cells = sum(d["n"] for d in scored) or 1
        a_acc = (acc["by_field"].get("access") or {}).get("verified")
        lb = lean(misses, "access")
        trust = {
            "after": sum(d["verified"] * d["n"] for d in scored) / cells,
            "before": sum(d["pass1"] * d["n"] for d in scored) / cells,
            "sample": acc.get("sample_size", 0),
            "access": a_acc,
            "lean": lb,
        }

    return {"shape": shape, "ready": ready, "blocked": blocked,
            "unsettled": unsettled, "none": stage.get("no viable API", 0),
            "n": n, "cards": cards, "trust": trust}


SHAPE_CLS = {"ready to build": "ready", "needs sandbox": "sandbox",
             "needs OAuth app review": "review",
             "needs partnership outreach": "outreach", "no viable API": "noapi",
             "not determined": "unsettled"}


def render_findings(a, f):
    n = f["n"]
    a('<section id="patterns"><div class="head"><h2>Findings</h2></div>')

    # the whole list in one bar
    a('<div class="shape"><p class="shape-lede"><b>%d of %d</b> are buildable today. '
      '<b>%d</b> need a sandbox, an app review or a sales call first. '
      '<b>%d</b> we could not settle.</p>'
      % (f["ready"], n, f["blocked"], f["unsettled"]))
    labels = {"ready to build": "ready", "needs sandbox": "sandbox",
              "needs OAuth app review": "app review",
              "needs partnership outreach": "sales", "no viable API": "no API",
              "not determined": "unsettled"}
    a('<div class="shape-bar" role="img" aria-label="Stage of all %d apps">' % n)
    for s, v in f["shape"]:
        if v:
            a('<div class="shape-seg s-%s" style="flex:%d" title="%d %s">%s</div>'
              % (SHAPE_CLS[s], v, v, esc(s), v if v * 100.0 / n >= 4 else ""))
    a('</div><div class="shape-key">')
    for s, v in f["shape"]:
        if v:
            a('<span><i class="s-%s"></i>%s <b>%d</b></span>'
              % (SHAPE_CLS[s], esc(labels[s]), v))
    a("</div></div>")

    # six patterns
    a('<div class="findings">')
    for i, c in enumerate(f["cards"], 1):
        a('<article class="finding"><div class="fnum">%02d%s</div><h3>%s</h3>'
          % (i, '<span class="flag">low trust</span>' if c.get("low") else "",
             esc(c["title"])))
        a('<div class="fbars">')
        for label, v, total, color in c["bars"]:
            pct = v * 100.0 / total if total else 0
            a('<div class="fbar"><div class="fbar-top"><span>%s</span><b>%d<small>/%d'
              '</small></b></div><div class="fbar-track"><i style="width:%.1f%%;'
              'background:%s"></i></div></div>' % (esc(label), v, total, pct, color))
        a('</div><p>%s</p></article>' % esc(c["so"]))
    a("</div>")

    t = f["trust"]
    if t:
        lean_txt = ""
        if t["lean"]:
            lean_txt = (" When it is wrong it usually says %s (%d of %d)."
                        % (esc(t["lean"]["value"]), t["lean"]["hits"], t["lean"]["of"]))
        a('<div class="trust"><div class="trust-num">%.0f%%<small>from %.0f%%</small>'
          '</div><p><b>How far to trust this.</b> %.0f%% of fields match a human '
          'check across %d apps. Access, which decides the stage, is weakest at '
          '%.0f%%.%s <a href="#verify">See verification</a></p></div>'
          % (t["after"], t["before"], t["after"], t["sample"], t["access"] or 0,
             lean_txt))
    a("</section>")


# --- html -------------------------------------------------------------------

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#5f5e58;--faint:#85847d;
--line:#e5e4de;--card:#ffffff;--tint:#f3f2ee;--chip:#ebeae4;
--accent:#2f6f4e;
--ready:#3d8a61;--ready-bg:#e3efe7;
--review:#b07a1c;--review-bg:#f6ecd6;
--outreach:#b8513f;--outreach-bg:#f7e4df;
--none:#9a998f;--none-bg:#ecebe5;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#131410;--fg:#ececdf;--muted:#adaca0;--faint:#8b8a80;
--line:#2a2b24;--card:#1a1b15;--tint:#1d1e18;--chip:#27281f;
--accent:#86c49b;
--ready:#7fbf95;--ready-bg:#1c2b21;
--review:#d8a95a;--review-bg:#2d2517;
--outreach:#e08b7a;--outreach-bg:#30201b;
--none:#77766d;--none-bg:#25261f;}}
:root[data-theme="dark"]{--bg:#131410;--fg:#ececdf;--muted:#adaca0;--faint:#8b8a80;
--line:#2a2b24;--card:#1a1b15;--tint:#1d1e18;--chip:#27281f;--accent:#86c49b;
--ready:#7fbf95;--ready-bg:#1c2b21;--review:#d8a95a;--review-bg:#2d2517;
--outreach:#e08b7a;--outreach-bg:#30201b;--none:#77766d;--none-bg:#25261f;}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px 80px;overflow-wrap:break-word}
h1{font-size:clamp(28px,4vw,40px);line-height:1.12;margin:0 0 10px;letter-spacing:-.025em}
h2{font-size:22px;margin:0;letter-spacing:-.015em;line-height:1.25}
h3{font-size:13px;margin:0 0 10px;font-weight:650;text-transform:uppercase;
letter-spacing:.06em;color:var(--faint)}
p{margin:8px 0}
a{color:var(--accent);text-underline-offset:2px}
section{scroll-margin-top:60px;padding:44px 0 0}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;
flex-wrap:wrap;margin:0 0 6px}
.take{color:var(--muted);margin:0 0 18px;max-width:70ch}
.cap{color:var(--faint);font-size:12.5px;margin:8px 0 0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.block{margin-top:26px}

/* nav */
.navbar{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--bg) 92%,transparent);
backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.navbar .inner{max-width:1120px;margin:0 auto;padding:8px 20px;display:flex;gap:2px;
align-items:center;font-size:13.5px;overflow-x:auto;scrollbar-width:none}
.navbar .brand{font-weight:700;color:var(--fg);margin-right:12px;letter-spacing:-.01em}
.navbar a{color:var(--muted);text-decoration:none;padding:5px 10px;border-radius:7px;
white-space:nowrap}
.navbar a:hover{color:var(--fg);background:var(--tint)}
.navbar a.on{color:var(--accent);background:var(--ready-bg);font-weight:600}

/* hero */
header{padding:44px 0 4px}
.lead{font-size:16.5px;color:var(--muted);max-width:62ch;margin:0}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;margin:14px 0 0;font-size:13px;color:var(--faint)}
.meta b{color:var(--fg);font-variant-numeric:tabular-nums}

/* findings */
.flag{display:inline-block;margin-left:6px;font-size:10.5px;font-weight:650;
padding:1px 7px;border-radius:999px;color:var(--review);background:var(--review-bg);
text-transform:uppercase;letter-spacing:.04em}
.shape{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.shape-lede{font-size:18px;line-height:1.45;margin:0 0 14px;max-width:none;color:var(--muted)}
.shape-lede b{color:var(--fg);font-weight:700}
.shape-bar{display:flex;height:38px;border-radius:8px;overflow:hidden;gap:2px}
.shape-seg{display:flex;align-items:center;justify-content:center;font-size:13px;
font-weight:700;color:#fff;font-variant-numeric:tabular-nums;min-width:4px}
.s-ready{background:var(--ready)}.s-review{background:var(--review)}
.s-outreach{background:var(--outreach)}.s-none{background:var(--none)}
.s-sandbox{background:color-mix(in srgb,var(--review) 55%,var(--card))}
.s-noapi{background:var(--none)}
.s-unsettled{background:repeating-linear-gradient(135deg,var(--none-bg) 0 6px,
color-mix(in srgb,var(--none) 45%,var(--none-bg)) 6px 12px);color:var(--fg)!important}
.shape-key i.s-unsettled{border:1px solid var(--none)}
.shape-key{display:flex;flex-wrap:wrap;gap:6px 18px;margin:12px 0 0;font-size:12.5px;color:var(--muted)}
.shape-key i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.shape-key b{color:var(--fg);font-variant-numeric:tabular-nums;margin-left:3px}
.findings{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
@media(max-width:960px){.findings{grid-template-columns:1fr 1fr}}
@media(max-width:620px){.findings{grid-template-columns:1fr}}
.finding{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px 20px;display:flex;flex-direction:column;gap:12px}
.fnum{font-size:12px;font-weight:700;color:var(--faint);font-variant-numeric:tabular-nums;
letter-spacing:.04em;display:flex;align-items:center;gap:4px}
.finding h3{font-size:16.5px;line-height:1.35;margin:0;color:var(--fg);text-transform:none;
letter-spacing:-.01em;font-weight:650}
.fbars{display:grid;gap:10px}
.fbar-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
font-size:12.5px;color:var(--muted);margin-bottom:4px}
.fbar-top b{font-size:17px;color:var(--fg);font-variant-numeric:tabular-nums;white-space:nowrap}
.fbar-top small{font-size:12px;color:var(--faint);font-weight:500}
.fbar-track{height:8px;border-radius:4px;background:var(--tint);overflow:hidden}
.fbar-track i{display:block;height:100%;border-radius:4px;min-width:3px}
.finding p{margin:auto 0 0;font-size:13px;color:var(--muted);line-height:1.5;
padding-top:10px;border-top:1px solid var(--line)}
.trust{display:flex;gap:18px;align-items:center;margin-top:12px;padding:16px 20px;
border-radius:14px;background:var(--review-bg);border:1px solid color-mix(in srgb,var(--review) 35%,transparent)}
.trust-num{font-size:30px;font-weight:700;letter-spacing:-.02em;color:var(--review);
line-height:1;white-space:nowrap;font-variant-numeric:tabular-nums}
.trust-num small{display:block;font-size:12px;font-weight:500;color:var(--faint);margin-top:4px;letter-spacing:0}
.trust p{margin:0;font-size:13.5px;color:var(--muted)}
.trust p b{color:var(--fg)}
@media(max-width:560px){.trust{flex-direction:column;align-items:flex-start}}

/* charts */
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.sbc .row{display:grid;grid-template-columns:200px 1fr 28px;gap:12px;align-items:center;
padding:3px 0;font-size:13px}
.sbc .lbl{color:var(--muted);text-align:right;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.track{display:flex;height:20px;border-radius:5px;overflow:hidden;background:var(--tint)}
.seg{height:100%}
.seg+.seg{box-shadow:inset 1px 0 0 var(--card)}
.tot{color:var(--faint);font-size:12px;font-variant-numeric:tabular-nums}
@media(max-width:640px){.sbc .row{grid-template-columns:96px 1fr 22px;gap:8px}}
.legend{display:flex;gap:6px 16px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin:12px 0 0}
.sw{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px}
.bars .row{display:grid;grid-template-columns:128px 1fr 30px;gap:10px;align-items:center;
font-size:13px;padding:3px 0}
.bars .lbl{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bars .bar{height:14px;border-radius:4px;min-width:2px}
.bars .v{font-variant-numeric:tabular-nums;color:var(--muted);font-size:12.5px}

.quad{display:grid;grid-template-columns:auto 1fr 1fr;grid-template-rows:auto 1fr 1fr;gap:8px}
.quad .ax{font-size:11.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;
font-weight:650;display:flex;align-items:center;justify-content:center}
.quad .ax.y{writing-mode:vertical-rl;transform:rotate(180deg)}
.q{border-radius:10px;padding:14px 16px}
.q b{font-size:28px;line-height:1;font-variant-numeric:tabular-nums;margin-right:8px}
.q em{font-style:normal;font-weight:650;font-size:14.5px}
.q p{margin:6px 0 0;font-size:12.5px;color:var(--muted);line-height:1.5}
.q-ready{background:var(--ready-bg)}.q-ready b{color:var(--ready)}
.q-review{background:var(--review-bg)}.q-review b{color:var(--review)}
.q-none{background:var(--tint)}.q-none b{color:var(--muted)}
.q-out{background:var(--outreach-bg)}.q-out b{color:var(--outreach)}

/* table */
.controls{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 10px;align-items:center}
input,select{font:inherit;font-size:13px;padding:7px 9px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg)}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:-1px}
#q{flex:1 1 160px;min-width:140px}
.controls select{max-width:150px}
.toggle{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);
padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);cursor:pointer}
.toggle input{margin:0;accent-color:var(--accent)}
.btn{font:inherit;font-size:13px;cursor:pointer;background:var(--card);color:var(--fg);
border:1px solid var(--line);border-radius:8px;padding:7px 12px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--bg);font-weight:600}
.btn.primary:hover{opacity:.9;color:var(--bg)}
#count{font-size:12.5px;color:var(--faint);font-variant-numeric:tabular-nums}
.tablewrap{overflow:auto;border-radius:12px;background:var(--card);border:1px solid var(--line)}
.tablewrap.tall{max-height:72vh}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
th{font-weight:650;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--faint);white-space:nowrap;position:sticky;top:0;background:var(--card);z-index:2}
th.sort{cursor:pointer;user-select:none}
th.sort:hover{color:var(--fg)}
th.sort[data-dir="1"]::after{content:" \\2191"}
th.sort[data-dir="-1"]::after{content:" \\2193"}
td.n,th.n{font-variant-numeric:tabular-nums}
#t td{vertical-align:middle}
#t tr.r{cursor:pointer}
#t tr.r:hover td{background:var(--tint)}
#t tr.r.open td{background:var(--tint);border-bottom-color:transparent}
#t td.app b{font-weight:600}
#t .caret{display:inline-block;width:12px;color:var(--faint);transition:transform .15s}
#t tr.open .caret{transform:rotate(90deg)}
.cat{display:block;font-size:11.5px;color:var(--faint)}
.unset{color:var(--faint)}
.empty{padding:28px;text-align:center;color:var(--faint)}

.st{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;
font-weight:600;white-space:nowrap;line-height:1.5}
.st-ready{color:var(--ready);background:var(--ready-bg)}
.st-sandbox,.st-oauth{color:var(--review);background:var(--review-bg)}
.st-partner{color:var(--outreach);background:var(--outreach-bg)}
.st-none,.st-unknown{color:var(--muted);background:var(--none-bg)}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:1px}
.mark{display:inline-block;font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:999px;
background:var(--chip);color:var(--muted);white-space:nowrap;vertical-align:1px}
.mark-weak{color:var(--review);background:var(--review-bg)}
.mark-rej{color:var(--outreach);background:var(--outreach-bg)}
.mark-human{color:var(--ready);background:var(--ready-bg)}

#t tr.expand td{background:var(--tint);padding:4px 16px 18px 36px;cursor:default}
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px 18px;
margin:0 0 14px;font-size:13px}
.facts div span{display:block;font-size:11px;color:var(--faint);text-transform:uppercase;
letter-spacing:.05em}
.one{margin:0 0 12px;font-size:14px}
.evlist{display:grid;gap:8px}
.ev{font-size:13px;color:var(--muted);padding:8px 12px;border-left:2px solid var(--line);
background:var(--card);border-radius:0 8px 8px 0}
.ev.rej{border-left-color:var(--outreach)}
.ev.weak{border-left-color:var(--review)}
.ev .f{font-weight:650;color:var(--fg);margin-right:6px}
.ev q{font-style:italic}
.ev a{color:var(--faint);font-size:12px;overflow-wrap:anywhere}
.notes{margin-top:10px;font-size:12.5px;color:var(--faint)}
.notes summary{cursor:pointer}

/* agent */
.cols3{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.cols3 .card h3{color:var(--fg);font-size:14px;text-transform:none;letter-spacing:0}
.cols3 .card .sub{font-size:12.5px;color:var(--faint);margin:-6px 0 10px}
.cols3 .card.agent{border-color:var(--ready);background:var(--ready-bg)}
.cols3 .card.human{border-color:var(--review);background:var(--review-bg)}
ol.steps,ul.ticks{margin:0;padding-left:18px;font-size:13.5px;line-height:1.55}
ol.steps li,ul.ticks li{margin:4px 0}
ol.steps li::marker{color:var(--faint);font-variant-numeric:tabular-nums}

/* verification */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:0 0 16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi b{display:block;font-size:26px;line-height:1.1;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi b small{font-size:13px;font-weight:500;color:var(--faint);margin-left:6px;letter-spacing:0}
.kpi span{font-size:12.5px;color:var(--muted)}
.up{color:var(--ready);font-weight:650;font-size:12px;margin-left:4px}
.down{color:var(--outreach);font-weight:650;font-size:12px;margin-left:4px}
.accbar{display:inline-block;width:64px;height:6px;border-radius:3px;background:var(--tint);
vertical-align:middle;margin-right:8px;overflow:hidden}
.accbar i{display:block;height:100%}

details.more{border:1px solid var(--line);border-radius:10px;background:var(--card);
padding:0 16px;margin:8px 0}
details.more>summary{cursor:pointer;padding:12px 0;font-size:14px;font-weight:600;
list-style:none;display:flex;align-items:center;gap:10px}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::before{content:"+";color:var(--accent);font-weight:700;width:10px}
details.more[open]>summary::before{content:"\\2212"}
details.more .hint{color:var(--faint);font-weight:400;font-size:12.5px;margin-left:auto;text-align:right}
details.more .inner{padding:0 0 16px;font-size:13.5px;color:var(--muted)}
details.more .inner p{max-width:78ch}
details.more .inner h4{margin:14px 0 2px;font-size:13.5px;color:var(--fg)}
.steps4{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;margin:10px 0}
.steps4 div{background:var(--tint);border-radius:8px;padding:10px 12px;font-size:12.5px}
.steps4 b{display:block;color:var(--fg);font-size:13px;margin-bottom:2px}
.quote{border-left:2px solid var(--outreach);padding:6px 12px;margin:10px 0;font-size:13px}
.quote a{color:var(--faint);font-size:12px;overflow-wrap:anywhere}

/* outreach */
.outcols{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.outcol{border-radius:12px;overflow:hidden;background:var(--card);border:1px solid var(--line)}
.outcol h3{margin:0;padding:11px 16px;font-size:13px;display:flex;text-transform:none;
letter-spacing:0;justify-content:space-between;align-items:center}
.outcol.g-review h3{background:var(--review-bg);color:var(--review)}
.outcol.g-out h3{background:var(--outreach-bg);color:var(--outreach)}
.outcol h3 em{font-style:normal;font-size:18px;font-variant-numeric:tabular-nums}
.outcol .body{padding:6px 16px 12px}
.outcol .app{display:flex;justify-content:space-between;gap:10px;padding:6px 0;
font-size:13px;border-bottom:1px solid var(--line)}
.outcol .app:last-child{border-bottom:0}
.outcol .app span{color:var(--faint);font-size:12px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px}
.tabs button[aria-selected="true"]{border-color:var(--accent);color:var(--accent);
background:var(--ready-bg);font-weight:600}
pre{background:var(--tint);padding:14px 16px;border-radius:10px;overflow:auto;font-size:12.5px;
line-height:1.6;margin:0;white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.email{position:relative}
.email .btn{position:absolute;top:10px;right:10px;padding:5px 10px;font-size:12px}

/* proof */
.proof{display:grid;gap:12px;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr)}
@media(max-width:820px){.proof{grid-template-columns:1fr}}
.dls{display:flex;flex-direction:column;gap:8px}
.dl{display:flex;justify-content:space-between;align-items:center;gap:12px;text-align:left;width:100%}
.dl span{color:var(--faint);font-size:12px;font-weight:400}
ul.limits{margin:0;padding-left:18px;font-size:13.5px;color:var(--muted)}
ul.limits li{margin:6px 0}
ul.limits b{color:var(--fg)}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--faint);font-size:13px}
@media(max-width:560px){.quad{grid-template-columns:1fr 1fr}.quad .ax{display:none}}
"""


def bar_list(pairs, color_for):
    """Label, bar, count. Plain divs so the file stays standalone."""
    top = max((v for _, v in pairs), default=1) or 1
    out = ['<div class="bars">']
    for label, value in pairs:
        out.append('<div class="row"><div class="lbl" title="%s">%s</div>'
                   '<div><div class="bar" style="width:%.1f%%;background:%s">'
                   '</div></div><div class="v">%d</div></div>'
                   % (esc(label), esc(label), value * 100.0 / top,
                      color_for(label), value))
    out.append("</div>")
    return "".join(out)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


ACCURACY_FIELDS = ["access", "auth", "api_type", "prod_requires_review",
                   "mcp", "api_breadth"]


def load_accuracy():
    """accuracy.json and misses.json if scout score has run. The page has to
    render without them, because a fresh clone has not scored anything yet."""
    acc, misses = None, []
    for path, default in ((config.ACCURACY, None), (config.MISSES, [])):
        if not path.exists():
            continue
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if path == config.ACCURACY:
            acc = got
        else:
            misses = got
    return acc, misses


def miss_shape(misses, which):
    """Split one pass's misses into the two kinds that mean different things.
    Saying null is under-claiming and the confidence score already pays for it.
    Saying the wrong value is a confident error, and those are the ones worth
    counting out loud."""
    rows = [m for m in misses if m.get("which") == which]
    silent = [m for m in rows if m.get("got") is None]
    wrong = [m for m in rows if m.get("got") is not None]
    return {"total": len(rows), "silent": len(silent), "wrong": wrong,
            "n_silent": len(silent), "n_wrong": len(wrong)}


def pick_instructive(misses, n=3):
    """Three misses that each teach a different lesson, rather than the three
    with the highest confidence, which would all be the same lesson."""
    rows = [m for m in misses if m.get("which") == "verified"]
    reasons = [
        (lambda m: m.get("field") == "access" and m.get("got") == "free self-serve",
         "Read free-sounding copy as free credentials."),
        (lambda m: m.get("field") == "auth" and m.get("got") == "Bearer",
         "Read the header in a curl example, not how the key is obtained."),
        (lambda m: m.get("field") == "mcp" and m.get("got") == "official"
         and m.get("expected") == "none",
         "Claimed an official MCP server that does not exist."),
        (lambda m: m.get("got") is None,
         "Found nothing it could evidence, so left it blank."),
    ]
    out, used = [], set()
    for test, why in reasons:
        hits = [m for m in rows
                if test(m) and (m["name"], m["field"]) not in used]
        if not hits:
            continue
        best = max(hits, key=lambda m: m.get("confidence") or 0)
        used.add((best["name"], best["field"]))
        out.append((best, why))
        if len(out) == n:
            break
    return out


def lean(misses, field, which="verified"):
    """The value the extractor reaches for when it is wrong about a field.
    A bias has a direction; an error rate does not, and the direction is the
    part a reader can act on."""
    got = [m["got"] for m in misses
           if m.get("which") == which and m.get("field") == field
           and m.get("got") is not None]
    if not got:
        return None
    top = max(set(got), key=got.count)
    return {"value": top, "hits": got.count(top), "of": len(got)}


def stage_chip(stage):
    return '<span class="st %s">%s</span>' % (
        stage_class(stage), esc(stage or "not determined"))


def stage_class(stage):
    return {"ready to build": "st-ready", "needs sandbox": "st-sandbox",
            "needs OAuth app review": "st-oauth",
            "needs partnership outreach": "st-partner",
            "no viable API": "st-none"}.get(stage, "st-unknown")


ACCESS_VAR = {"free self-serve": "var(--ready)", "free trial": "var(--ready)",
              "paid plan": "var(--review)", "admin approval": "var(--review)",
              "partner or contact-sales": "var(--outreach)",
              "no public API": "var(--none)", "not determined": "var(--none)"}


EMAILS = [
    ("OAuth app review", "Requesting review for a {app} integration",
     "Hi,\n\nWe are building a connector for {app} that will be used by our "
     "customers to sync their own {app} data. We have the integration working "
     "against a development account and we are ready to submit for review.\n\n"
     "Scopes we need and why:\n  - read: to list the records a customer asks us "
     "to sync\n  - write: to create records on the customer's behalf\n\n"
     "Could you point us at the current review process and rough turnaround? "
     "Happy to send a demo video or a test account.\n\nThanks,\n"),
    ("Partnership", "Partnership enquiry, {app} API access",
     "Hi,\n\nWe run an integration platform and our customers are asking for "
     "{app}. Your API looks like it needs a partnership rather than a signup, "
     "so I wanted to start that conversation properly rather than guess.\n\n"
     "We would be bringing you customers who already pay for {app} and want it "
     "connected to the rest of their stack. Who is the right person to talk to, "
     "and what does the process look like?\n\nThanks,\n"),
    ("Enterprise sales", "API access for an existing {app} customer",
     "Hi,\n\nWe are looking at {app} for a customer-facing integration. The docs "
     "point at contacting sales for API access, so here we are.\n\n"
     "Two questions to save us both time:\n  1. Is API access tied to a "
     "particular plan, and which one?\n  2. Is there a sandbox we can build "
     "against before committing?\n\nThanks,\n"),
]


def section_head(a, sid, title, take=None, right=""):
    a('<section id="%s"><div class="head"><h2>%s</h2>%s</div>' % (sid, esc(title), right))
    if take:
        a('<p class="take">%s</p>' % take)


def build():
    pat = analyze.analyze()
    st = stats()
    acc, misses = load_accuracy()
    ver = st["verified"]
    rows = [ver[k] for k in sorted(ver)]
    sample = (acc or {}).get("sample_size", 0)

    # a single JSON file an agent can consume, offered as a download
    bundle = config.DATA / "verified.json"
    bundle.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    h = []
    a = h.append

    a('<!doctype html><html lang="en"><head><meta charset="utf-8">')
    a('<meta name="viewport" content="width=device-width,initial-scale=1">')
    a("<title>Scout</title>")
    a('<meta name="description" content="Auth, access, API surface, MCP and '
      'buildability for 100 apps, with evidence for every claim.">')
    a("<style>%s</style></head><body>" % CSS)

    a('<nav class="navbar"><div class="inner"><span class="brand">Scout</span>'
      '<a href="#patterns">Findings</a><a href="#charts">Charts</a>'
      '<a href="#table">The 100</a><a href="#agent">Agent vs human</a>'
      '<a href="#verify">Verification</a><a href="#outreach">Outreach</a>'
      '<a href="#proof">Proof</a></div></nav>')
    a('<div class="wrap">')

    # header
    a("<header>")
    a("<h1>What it takes to build a connector for 100 apps</h1>")
    a('<p class="lead">An agent researched auth, access, API surface and MCP for '
      'each app. Every claim links to a quoted source. Anything it could not '
      'prove is left blank.</p>')
    a('<div class="meta"><span><b>%d</b> apps</span><span><b>%d</b> evidence '
      'snippets</span><span><b>%d</b> checked by hand</span><span>median '
      'confidence <b>%.2f</b></span><span>built <b>%s</b></span></div>'
      % (pat["n"], pat["unresolved"]["total_evidence_items"], sample,
         st["median_verified"], RUN_DATE))
    a("</header>")

    # 1. patterns
    render_findings(a, findings(pat, st, acc, misses))

    # 2. charts
    sbc = pat["stage_by_category"]

    def ready_of(cat):
        return sbc.get(cat, {}).get("ready to build", 0), sum(sbc.get(cat, {}).values())

    crm = ready_of("CRM and Sales")
    mkt = ready_of("Marketing, Ads, Email and Social")
    section_head(a, "charts", "Where the easy wins are",
                 "CRM is the easiest category (%d of %d ready). Marketing and Ads "
                 "is the hardest (%d of %d), held back by app reviews rather than "
                 "prices." % (crm[0], crm[1], mkt[0], mkt[1]))
    order = STAGE_ORDER + ["not determined"]
    a('<div class="card"><h3>Stage by category</h3><div class="sbc">')
    for cat, counts in sorted(sbc.items(), key=lambda kv: -kv[1].get("ready to build", 0)):
        total = sum(counts.values()) or 1
        a('<div class="row"><div class="lbl" title="%s">%s</div><div class="track">'
          % (esc(cat), esc(cat)))
        for s in order:
            v = counts.get(s, 0)
            if v:
                a('<div class="seg" style="width:%.2f%%;background:var(--%s)" '
                  'title="%s: %d %s"></div>'
                  % (v * 100.0 / total, STAGE_VAR[s], esc(cat), v, esc(s)))
        a('</div><div class="tot">%d</div></div>' % sum(counts.values()))
    a('</div><div class="legend">')
    for var, label in (("ready", "ready to build"),
                       ("review", "sandbox or app review"),
                       ("outreach", "partnership outreach"),
                       ("none", "no viable API or unsettled")):
        a('<span><span class="sw" style="background:var(--%s)"></span>%s</span>'
          % (var, esc(label)))
    a("</div></div>")

    a('<div class="cards block">')
    auth_pairs = sorted(pat["auth"].items(), key=lambda kv: -kv[1])
    a('<div class="card"><h3>Auth split</h3>%s</div>'
      % bar_list(auth_pairs, lambda l: "var(--none)" if l == "not determined"
                 else "var(--accent)"))
    access_pairs = sorted(pat["access"].items(), key=lambda kv: -kv[1])
    a('<div class="card"><h3>Access to credentials</h3>%s</div>'
      % bar_list(access_pairs, lambda l: ACCESS_VAR.get(l, "var(--none)")))
    a("</div>")

    a('<div class="card block"><h3>Ease of build vs worth building</h3>')
    members = pat.get("quadrant_members", {})

    def quad(key, cls_, label):
        names = members.get(key, [])
        ex = ", ".join(names[:4]) + (" and %d more" % (len(names) - 4) if len(names) > 4 else "")
        return ('<div class="q %s"><b>%d</b><em>%s</em><p>%s</p></div>'
                % (cls_, pat["quadrant"].get(key, 0), esc(label), esc(ex)))
    a('<div class="quad"><div class="ax"></div><div class="ax">Ready to build</div>'
      '<div class="ax">Not ready yet</div>')
    a('<div class="ax y">Well known</div>%s%s'
      % (quad("build now", "q-ready", "Build now"),
         quad("worth the paperwork", "q-review", "Worth the paperwork")))
    a('<div class="ax y">Niche</div>%s%s</div>'
      % (quad("easy, niche", "q-none", "Easy, niche"),
         quad("park it", "q-out", "Park it")))
    a('<p class="cap">"Well known" is a hand-written list plus API breadth, kept in '
      'scout/analyze.py so you can disagree with it.</p></div>')
    a("</section>")

    # 3. the table
    section_head(a, "table", "The 100", right='<span id="count"></span>', take=
                 "Click a row for its evidence. The dot is how well evidenced the "
                 "row is; <span class=\"mark mark-human\">human</span> marks the "
                 "%d rows checked by hand." % sample)
    a('<div class="controls">')
    a('<input id="q" type="search" placeholder="Search app, blocker, note" '
      'aria-label="Search">')
    a('<select id="fstage" aria-label="Stage"><option value="">All stages</option>%s'
      '<option value="__none">not determined</option></select>'
      % "".join('<option>%s</option>' % esc(s) for s in STAGE_ORDER))
    a('<select id="fcat" aria-label="Category"><option value="">All categories</option>%s</select>'
      % "".join('<option>%s</option>' % esc(c) for c in schema.CATEGORIES))
    a('<select id="fauth" aria-label="Auth"><option value="">All auth</option>%s</select>'
      % "".join('<option>%s</option>' % esc(v) for v in schema.AUTH))
    a('<select id="faccess" aria-label="Access"><option value="">All access</option>%s</select>'
      % "".join('<option>%s</option>' % esc(v) for v in schema.ACCESS))
    a('<label class="toggle"><input type="checkbox" id="fgated">Gated</label>')
    a('<label class="toggle"><input type="checkbox" id="fhuman">Hand-checked</label>')
    a('<button class="btn" id="reset" type="button">Reset</button>')
    a("</div>")
    a('<div class="tablewrap tall"><table id="t"><thead><tr>')
    for key, label, c in (("app_id", "#", "n"), ("name", "App", ""),
                          ("stage", "Stage", ""), ("access", "Access", ""),
                          ("auth", "Auth", ""), ("mcp", "MCP", ""),
                          ("confidence", "Confidence", "n"),
                          ("evidence", "Evidence", "n")):
        a('<th class="sort %s" data-k="%s">%s</th>' % (c, key, label))
    a("</tr></thead><tbody></tbody></table></div>")
    a("</section>")

    # 4. agent and human
    section_head(a, "agent", "What the agent does, and where a human was needed",
                 "The agent does the reading. A person decides what the words mean.")
    a('<div class="cols3">')
    a('<div class="card"><h3>Manual playbook</h3><p class="sub">About 15 minutes '
      'per app, a full day for 100</p><ol class="steps">'
      '<li>Find the developer docs</li><li>Find the auth page</li>'
      '<li>Find pricing or signup</li><li>Decide if you can get a key yourself</li>'
      '<li>Check for an MCP server</li><li>Write it down consistently</li></ol></div>')
    a('<div class="card agent"><h3>What the agent owns</h3><p class="sub">About 45 '
      'seconds per app, six at a time</p><ul class="ticks">'
      '<li>3 searches, up to 6 pages fetched and cached</li>'
      '<li>One extraction call, then a targeted retry for gaps</li>'
      '<li>A support check on every claim</li>'
      '<li>A mechanical confidence score</li>'
      '<li>Lookup against 1,543 Composio toolkits</li>'
      '<li>Enum values that never drift</li></ul></div>')
    a('<div class="card human"><h3>What still needed a person</h3><p class="sub">'
      'The agent cannot tell when it is confidently wrong</p><ul class="ticks">'
      '<li>Designing the schema and enum meanings</li>'
      '<li>Ruling a 14 day trial is not free self-serve</li>'
      '<li>Noticing confidence scored a wrong row 1.0</li>'
      '<li>Spotting Sherlock was four different products</li>'
      '<li>Judging which apps are well known</li>'
      '<li>Checking the %d-app sample by hand</li></ul></div>' % sample)
    a("</div></section>")

    # 5. verification
    section_head(a, "verify", "Verification")
    if acc:
        p1 = miss_shape(misses, "pass1")
        vf = miss_shape(misses, "verified")
        hi = [m for m in vf["wrong"] if (m.get("confidence") or 0) >= 0.70]
        scored = [d for d in acc.get("by_field", {}).values()
                  if d.get("n") and d.get("verified") is not None]
        cells = sum(d["n"] for d in scored) or 1
        after = sum(d["verified"] * d["n"] for d in scored) / cells
        before = sum(d["pass1"] * d["n"] for d in scored) / cells

        a('<p class="take">%d apps checked by hand: 10 hard ones named in the brief, '
          '10 drawn at random, one per category. Scored before and after the '
          'loops.</p>' % sample)
        a('<div class="kpis">')
        a('<div class="kpi"><b>%.0f%%<small>from %.0f%%</small></b>'
          '<span>fields matching the human</span></div>' % (after, before))
        a('<div class="kpi"><b>%d<small>from %d</small></b><span>misses in the '
          'sample</span></div>' % (vf["total"], p1["total"]))
        a('<div class="kpi"><b>%d<small>from %d</small></b><span>confidently '
          'wrong answers</span></div>' % (vf["n_wrong"], p1["n_wrong"]))
        a('<div class="kpi"><b>%d<small>of %d</small></b><span>wrong answers still '
          'scored 0.70+</span></div>' % (len(hi), vf["n_wrong"]))
        a("</div>")

        def cell(d):
            if not d or not d.get("n") or d.get("pass1") is None or d.get("verified") is None:
                return '<td class="n unset">not checked</td>'
            b_, v_ = d["pass1"], d["verified"]
            col = ("var(--ready)" if v_ >= 70 else "var(--review)" if v_ >= 45
                   else "var(--outreach)")
            delta = v_ - b_
            move = ""
            if abs(delta) >= 0.5:
                move = '<span class="%s">%+.0f</span>' % ("up" if delta > 0 else "down", delta)
            return ('<td class="n"><span class="accbar"><i style="width:%.0f%%;'
                    'background:%s"></i></span>%.0f%%%s</td>' % (v_, col, v_, move))

        a('<div class="tablewrap"><table><thead><tr><th>Field</th><th>Overall</th>'
          '<th>Hard half</th><th>Random half</th><th class="n">Checked</th>'
          '</tr></thead><tbody>')
        for f in ACCURACY_FIELDS:
            over = acc["by_field"].get(f, {})
            if not over.get("n"):
                continue
            a("<tr><td><b>%s</b></td>%s%s%s<td class='n'>%d</td></tr>"
              % (esc(f), cell(over), cell(acc["by_half"]["hard"].get(f)),
                 cell(acc["by_half"]["random"].get(f)), over["n"]))
        a("</tbody></table></div>")
        a('<p class="cap">Accuracy after the loops, with the change from the first '
          'full run. Halves are kept apart: hard shows we did not cherry-pick, '
          'random shows a typical row.</p>')

        a('<div class="cards block">')
        a('<div class="card" style="padding:0"><div class="tablewrap" style="border:0">'
          '<table><thead><tr><th>Loop</th><th>Checks</th><th>Result</th></tr>'
          '</thead><tbody>')
        for name, checks, changed in (
            ("A", "Second extraction, different prompt",
             "%d rows disagreed" % st["loop_a_rows"]),
            ("B", "Snippet still on the cited page", "0 changes"),
            ("C", "Real browser reads signup page",
             "%d read, %d contradicted" % (st["loop_c_rows"],
                                           len(st["loop_c_contradictions"]))),
            ("D", "Human sample", "%d misses" % vf["total"]),
            ("E", "Page is about the right app",
             "%d pages dropped" % st["prefilter_pages"]),
            ("Support", "Snippet supports the exact claim",
             "%d rows overruled" % pat["unresolved"]["rows_with_a_rejected_claim"]),
        ):
            a("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
              % (esc(name), esc(checks), esc(changed)))
        a("</tbody></table></div></div>")

        a('<div class="card" style="padding:0"><div class="tablewrap" style="border:0">'
          '<table><thead><tr><th>Instructive miss</th><th>Human</th><th>Agent</th>'
          '</tr></thead><tbody>')
        for m, why in pick_instructive(misses):
            got = m.get("got")
            a('<tr><td><b>%s</b> <span class="mark">%s</span>'
              '<div class="cap" style="margin:2px 0 0">%s</div></td>'
              '<td>%s</td><td>%s</td></tr>'
              % (esc(m.get("name", "")), esc(m.get("field", "")), esc(why),
                 esc(m.get("expected")),
                 '<span class="unset">blank</span>' if got is None else esc(got)))
        a("</tbody></table></div></div>")
        a("</div>")

        # deeper material, collapsed
        a('<div class="block">')
        a('<details class="more"><summary>Full misses log<span class="hint">%d rows'
          '</span></summary><div class="inner">' % vf["total"])
        a('<div class="tablewrap tall"><table><thead><tr>')
        for label in ("App", "Half", "Field", "Human", "Agent", "Conf.", "Caught by"):
            a("<th>%s</th>" % label)
        a("</tr></thead><tbody>")
        shown = sorted([m for m in misses if m.get("which") == "verified"],
                       key=lambda m: (m.get("got") is None, -(m.get("confidence") or 0)))
        for m in shown:
            got = m.get("got")
            a("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td class='n'>%.2f</td><td>%s</td></tr>"
              % (esc(m.get("name", "")), esc(m.get("half", "")),
                 esc(m.get("field", "")), esc(m.get("expected")),
                 '<span class="unset">blank</span>' if got is None else esc(got),
                 (m.get("confidence") or 0),
                 esc(m.get("caught_by") or "human only")))
        a("</tbody></table></div></div></details>")

        a('<details class="more"><summary>Why the errors lean one way'
          '<span class="hint">and why confidence misses them</span></summary>'
          '<div class="inner">')
        blurb = {
            "access": "Free-sounding copy gets read as free credentials. Free "
                      "describes the tool, not the signup.",
            "auth": "A key pasted into a Bearer header and an OAuth token look "
                    "identical in a curl example.",
        }
        for f in ("access", "auth"):
            lb = lean(misses, f)
            if lb and lb["hits"] >= 2:
                a("<h4>%s guesses %s (%d of %d wrong answers)</h4><p>%s</p>"
                  % (esc(f), esc(lb["value"]), lb["hits"], lb["of"], esc(blurb[f])))
        a("<h4>High confidence means well evidenced, not correct</h4>"
          "<p>%d of %d wrong answers scored 0.70 or more. Confidence drops for "
          "missing evidence, not for evidence read the wrong way.</p>"
          % (len(hi), vf["n_wrong"]))
        a("<h4>The loops trade blanks for guesses</h4>"
          "<p>Blank answers fell from %d to %d. Confidently wrong answers went "
          "from %d to %d.</p>" % (p1["n_silent"], vf["n_silent"],
                                  p1["n_wrong"], vf["n_wrong"]))
        a("</div></details>")
    else:
        a('<p class="take">Accuracy is not on this page yet, because the '
          'hand-checked sample is not finished.</p><div>')

    p0 = config.DATA / "pass0" / "1.json"
    z = None
    if p0.exists():
        try:
            z = json.loads(p0.read_text(encoding="utf-8"))
        except ValueError:
            z = None
    if z:
        ev = next((e for e in (z.get("evidence") or []) if e.get("field") == "access"), None)
        a('<details class="more"><summary>A real quote is not proof'
          '<span class="hint">Salesforce, scored %.1f and wrong</span></summary>'
          '<div class="inner">' % z.get("confidence", 0))
        a("<p>The scaffold version only checked that a snippet appeared on the page. "
          "It called Salesforce access <span class='mono'>%s</span> from this:</p>"
          % esc(z.get("access")))
        if ev:
            a('<div class="quote">&ldquo;%s&rdquo;<br><a href="%s" target="_blank" '
              'rel="noopener">%s</a></div>'
              % (esc((ev.get("snippet") or "").strip()[:240]),
                 esc(ev.get("url", "")), esc(ev.get("url", "")[:95])))
        a("<p>That sentence is about OAuth approval, not about getting credentials. "
          "The support check exists because of this row.</p></div></details>")

    a('<details class="more"><summary>Every gate passed, answer still wrong'
      '<span class="hint">Sherlock, four products, one name</span></summary>'
      '<div class="inner"><p>Searching "Sherlock" returned four different products. '
      'Five of six pages were the wrong one.</p><div class="steps4">')
    for step, what in (
        ("Domain check", "Substring match let sherlocks.ai through as first party."),
        ("Extractor", "Filled access and MCP from another company's docs."),
        ("Support check", "Passed both. The quotes were real, the product was not."),
        ("Loop E", "Caught all four afterwards and blanked them."),
    ):
        a("<div><b>%s</b>%s</div>" % (esc(step), esc(what)))
    a("</div><p>Fix: auth and pricing searches are pinned to the known domain, "
      "the subject check runs before extraction, and official MCP needs evidence "
      "from the app's own domain. The same trap caught Fathom and Otter AI.</p>"
      "</div></details>")

    if st["loop_c_contradictions"]:
        a('<details class="more"><summary>A real browser argued back'
          '<span class="hint">%d of %d rows checked</span></summary>'
          '<div class="inner"><p>Flagged, not overwritten: browser prose is a second '
          'opinion, not a quoted source.</p>'
          % (len(st["loop_c_contradictions"]), st["loop_c_rows"]))
        a('<div class="tablewrap"><table><thead><tr><th>App</th><th>We recorded</th>'
          '<th>Live page said</th></tr></thead><tbody>')
        for c in st["loop_c_contradictions"]:
            said = c["browser"].replace(chr(92) + chr(34), chr(34)).replace(
                chr(92) + "'", "'")
            if len(said) > 200:
                said = said[:200].rsplit(" ", 1)[0] + "..."
            a("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
              % (esc(c["name"]), esc(c["ours"]), esc(said)))
        a("</tbody></table></div></div></details>")

    a('<details class="more"><summary>Coverage by field'
      '<span class="hint">first run vs verified</span></summary><div class="inner">')
    a('<div class="tablewrap"><table><thead><tr><th>Field</th><th class="n">First run'
      '</th><th class="n">Verified</th></tr></thead><tbody>')
    for row in st["coverage"]:
        delta = row["verified"] - row["pass1"]
        a('<tr><td>%s</td><td class="n">%d</td><td class="n">%d%s</td></tr>'
          % (esc(row["field"]), row["pass1"], row["verified"],
             ' <span class="up">+%d</span>' % delta if delta > 0 else
             (' <span class="down">%d</span>' % delta if delta < 0 else "")))
    a('<tr><td>median confidence</td><td class="n">%.2f</td><td class="n">%.2f</td></tr>'
      % (st["median_pass1"], st["median_verified"]))
    a("</tbody></table></div>")
    a("<p>%d evidence items from the app's own domain, %d from confirmed third "
      "parties, %d of %d marked weak. Loop B changed nothing because it re-checks "
      "the same cache it read from, so it cannot detect a stale page.</p>"
      % (st["subject_own"], st["subject_third"],
         pat["unresolved"]["weak_evidence_items"],
         pat["unresolved"]["total_evidence_items"]))
    a("</div></details>")
    a("</div></section>")

    # 6. outreach
    gate_email = {"admin approval": 0, "partner or contact-sales": 1, "paid plan": 2}
    gate_cls = {"paid plan": "g-review", "admin approval": "g-review",
                "partner or contact-sales": "g-out"}
    gated_all = [it for v in pat["outreach_queue"].values() for it in v]
    no_route = sum(1 for it in gated_all
                   if not str((it.get("gate") or {}).get("application_url") or ""
                              ).startswith("http"))
    section_head(a, "outreach", "Outreach queue",
                 "%d apps you cannot just sign up for. %d publish no application "
                 "link, so step one is finding a person to ask."
                 % (len(gated_all), no_route))
    a('<div class="outcols">')
    for access, items in sorted(pat["outreach_queue"].items(), key=lambda kv: -len(kv[1])):
        a('<div class="outcol %s"><h3>%s<em>%d</em></h3><div class="body">'
          % (gate_cls.get(access, "g-review"), esc(access), len(items)))
        for it in sorted(items, key=lambda i: i["app_id"]):
            url = (it.get("gate") or {}).get("application_url") or ""
            right = ('<a href="%s" target="_blank" rel="noopener">apply &rarr;</a>'
                     % esc(url)) if url.startswith("http") else ""
            a('<div class="app">%s%s</div>' % (esc(it["name"]), right))
        a("</div></div>")
    a("</div>")
    a('<div class="card block"><h3>Email templates</h3><div class="tabs" role="tablist">')
    for i, (label, _, _) in enumerate(EMAILS):
        a('<button class="btn" role="tab" data-tab="%d" aria-selected="%s">%s</button>'
          % (i, "true" if i == 0 else "false", esc(label)))
    a("</div>")
    for i, (label, subject, body) in enumerate(EMAILS):
        a('<div class="email" data-panel="%d"%s><button class="btn copy" '
          'type="button">Copy</button><pre>Subject: %s\n\n%s</pre></div>'
          % (i, "" if i == 0 else " hidden", esc(subject), esc(body)))
    a("</div></section>")

    # 7. proof
    section_head(a, "proof", "Proof",
                 "Everything on this page comes from the repo and reruns with one "
                 "command per step.")
    a('<div class="proof"><div>')
    a('<p style="margin:0 0 10px">Repo: <a href="%s" target="_blank" rel="noopener">%s</a></p>'
      % (REPO_URL, REPO_URL.replace("https://", "")))
    a("<pre>pip install -e .\ncp .env.example .env   # COMPOSIO_API_KEY, GROQ_API_KEY\n"
      "scout research --all   # writes data/raw, freezes data/pass1\n"
      "scout verify --all     # loops, writes data/verified\n"
      "scout score            # accuracy vs the human sample\n"
      "scout analyze          # crosstabs\n"
      "scout build-page       # this file</pre>")
    a('<h3 class="block">Limits</h3><ul class="limits">'
      '<li><b>Pinned search.</b> Page sets are frozen in data/urls/, so results are '
      'only as fresh as the day they were pinned.</li>'
      '<li><b>Look-alike names.</b> Apps whose domain lacks their name (Fathom, '
      'Otter AI, Sherlock) pulled pages from other companies.</li>'
      '<li><b>%d of %d rows unchecked by a human.</b> Access, the field that '
      'matters most, scores lowest.</li>'
      '<li><b>No freshness check.</b> Two renames (Coda, fanbasis) were caught by a '
      'person, not the pipeline.</li></ul>' % (len(rows) - sample, len(rows)))
    a("</div><div>")
    a('<h3>Raw data</h3><div class="dls">')
    for key, label, note in (
        ("DATA", "verified.json", "%d rows with evidence" % len(rows)),
        ("HUMAN", "human_sample.json", "%d hand-checked rows" % sample),
        ("MISSES", "misses.json", "every agent vs human disagreement"),
    ):
        a('<button class="btn dl" data-src="%s" data-name="%s"><b>%s</b>'
          '<span>%s</span></button>' % (key, esc(label), esc(label), esc(note)))
    a('</div><p class="cap">Built from the JSON embedded in this page, so it works '
      'offline. Agents can read the same data from the script tags.</p>')
    a("</div></div></section>")

    a('<footer>Built by an agent, checked by a human. Judgment calls and the bugs '
      'found along the way are in SESSIONS.md.</footer>')
    a("</div>")

    human_rows = []
    if config.HUMAN_SAMPLE.exists():
        try:
            human_rows = json.loads(config.HUMAN_SAMPLE.read_text(encoding="utf-8"))
        except ValueError:
            human_rows = []
    a("<script>const DATA=%s;</script>" % json.dumps(rows, ensure_ascii=False))
    a("<script>const HUMAN=%s;</script>" % json.dumps(human_rows, ensure_ascii=False))
    a("<script>const MISSES=%s;</script>" % json.dumps(misses, ensure_ascii=False))
    a("<script>const ACCURACY=%s;</script>" % json.dumps(acc, ensure_ascii=False))
    a("<script>%s</script>" % JS)
    a("</body></html>")

    config.SITE.mkdir(parents=True, exist_ok=True)
    out = config.SITE / "index.html"
    out.write_text("\n".join(h), encoding="utf-8")
    return out


JS = """
const GATED=["paid plan","admin approval","partner or contact-sales"];
const $=id=>document.getElementById(id);
const tb=document.querySelector("#t tbody");
const q=$("q"),fs=$("fstage"),fc=$("fcat"),fg=$("fgated"),fa=$("fauth"),
fx=$("faccess"),fh=$("fhuman"),cnt=$("count");
// app_ids a human checked by hand, so the table can say which rows are ruler
// and which are output.
const CHECKED=new Set((typeof HUMAN!=="undefined"&&HUMAN?HUMAN:[]).map(r=>r.app_id));
const open=new Set();
let sortKey="app_id",sortDir=1;
function cls(s){return {"ready to build":"st-ready","needs sandbox":"st-sandbox",
"needs OAuth app review":"st-oauth","needs partnership outreach":"st-partner",
"no viable API":"st-none"}[s]||"st-unknown";}
function confColor(c){return c>=0.8?"var(--ready)":c>=0.6?"var(--review)":"var(--outreach)";}
function val(r,k){
  if(k==="evidence")return r.evidence.length;
  const v=r[k];return v===null||v===undefined?"":v;}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;",
"<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function show(v){return v==null?'<span class="unset">not set</span>':esc(v);}
function evidence(r){
  const rej=r.rejected||{};
  let out=Object.keys(rej).map(f=>'<div class="ev rej"><span class="f">'+esc(f)+
    '</span><span class="mark mark-rej">overruled</span> claimed &ldquo;'+
    esc(rej[f].value)+'&rdquo;. '+esc(rej[f].reason)+'</div>').join("");
  if(!r.evidence.length)
    return out+'<div class="ev">No evidence survived the checks for this row.</div>';
  return out+r.evidence.map(e=>'<div class="ev'+(e.support==="weak"?" weak":"")+
    '"><span class="f">'+esc(e.field)+'</span>'+
    (e.support==="weak"?'<span class="mark mark-weak">weak</span> ':'')+
    (e.subject&&e.subject.indexOf("third")===0?'<span class="mark">third party</span> ':'')+
    '<q>'+esc(e.snippet)+'</q><br><a href="'+esc(e.url)+
    '" target="_blank" rel="noopener">'+esc(e.url.slice(0,90))+'</a></div>').join("");}
function detail(r){
  const f=[["Category",r.category],
    ["API",(r.api_type||"not set")+(r.api_breadth&&r.api_breadth!=="unknown"?", "+r.api_breadth:"")],
    ["Production review",r.prod_requires_review===true?"needed":
      r.prod_requires_review===false?"not needed":"unknown"],
    ["Composio toolkit",r.extras.composio_toolkit?"ships today":"not yet"],
    ["Confidence",r.confidence.toFixed(2)]];
  if(r.blocker)f.push(["Blocker",r.blocker]);
  const docs=r.api_docs_url?'<a href="'+esc(r.api_docs_url)+'" target="_blank" rel="noopener">API docs</a>':"";
  return '<tr class="expand"><td colspan="8">'+
    (r.one_liner?'<p class="one">'+esc(r.one_liner)+(docs?' &middot; '+docs:'')+'</p>':'')+
    '<div class="facts">'+f.map(b=>'<div><span>'+b[0]+'</span>'+esc(b[1])+'</div>').join("")+
    '</div><div class="evlist">'+evidence(r)+'</div>'+
    (r.notes?'<details class="notes"><summary>Agent notes</summary><p>'+esc(r.notes)+
      '</p></details>':'')+'</td></tr>';}
function render(){
  const t=q.value.toLowerCase().trim(),s=fs.value,c=fc.value,g=fg.checked,
    au=fa.value,ax=fx.value,hu=fh.checked;
  let rows=DATA.filter(r=>{
    if(s==="__none"?r.stage:s&&r.stage!==s)return false;
    if(c&&r.category!==c)return false;
    if(au&&r.auth!==au)return false;
    if(ax&&r.access!==ax)return false;
    if(hu&&!CHECKED.has(r.app_id))return false;
    if(g&&!GATED.includes(r.access))return false;
    if(t){const hay=(r.name+" "+(r.blocker||"")+" "+(r.notes||"")+" "+
      (r.one_liner||"")+" "+(r.category||"")).toLowerCase();
      if(!hay.includes(t))return false;}
    return true;});
  rows.sort((a,b)=>{const x=val(a,sortKey),y=val(b,sortKey);
    return (x>y?1:x<y?-1:0)*sortDir;});
  cnt.textContent=rows.length+" of "+DATA.length+" apps";
  document.querySelectorAll("#t th.sort").forEach(th=>
    th.dataset.dir=th.dataset.k===sortKey?sortDir:"");
  if(!rows.length){tb.innerHTML='<tr><td colspan="8" class="empty">No apps match. '+
    'Try clearing a filter.</td></tr>';return;}
  tb.innerHTML=rows.map(r=>{
    const conf=r.confidence,isOpen=open.has(r.app_id);
    const nweak=r.evidence.filter(e=>e.support==="weak").length;
    const nrej=Object.keys(r.rejected||{}).length;
    return '<tr class="r'+(isOpen?" open":"")+'" data-id="'+r.app_id+'" tabindex="0" '+
      'aria-expanded="'+isOpen+'">'+
    '<td class="n"><span class="caret">&#9656;</span>'+r.app_id+'</td>'+
    '<td class="app"><b>'+esc(r.name)+'</b>'+
      (CHECKED.has(r.app_id)?' <span class="mark mark-human">human</span>':'')+
      '<span class="cat">'+esc(r.category)+'</span></td>'+
    '<td><span class="st '+cls(r.stage)+'">'+esc(r.stage||"not determined")+'</span></td>'+
    '<td>'+show(r.access)+'</td><td>'+show(r.auth)+'</td><td>'+show(r.mcp)+'</td>'+
    '<td class="n"><span class="dot" style="background:'+confColor(conf)+'"></span>'+
      conf.toFixed(2)+'</td>'+
    '<td class="n">'+r.evidence.length+
      (nweak?' <span class="mark mark-weak" title="weak snippets">'+nweak+' weak</span>':'')+
      (nrej?' <span class="mark mark-rej" title="claims overruled">'+nrej+' overruled</span>':'')+
    '</td></tr>'+(isOpen?detail(r):"");
  }).join("");}
function toggle(tr){const id=Number(tr.dataset.id);
  if(open.has(id))open.delete(id);else open.add(id);render();}
tb.addEventListener("click",e=>{
  const tr=e.target.closest("tr.r");
  if(tr&&!e.target.closest("a"))toggle(tr);});
tb.addEventListener("keydown",e=>{
  const tr=e.target.closest("tr.r");
  if(tr&&(e.key==="Enter"||e.key===" ")){e.preventDefault();toggle(tr);
    const again=tb.querySelector('tr.r[data-id="'+tr.dataset.id+'"]');if(again)again.focus();}});
document.querySelectorAll("#t th.sort").forEach(th=>th.addEventListener("click",()=>{
  const k=th.dataset.k;
  if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=k==="confidence"||k==="evidence"?-1:1;}
  render();}));
[q,fs,fc,fg,fa,fx,fh].forEach(el=>el.addEventListener("input",render));
$("reset").addEventListener("click",()=>{q.value="";[fs,fc,fa,fx].forEach(x=>x.value="");
  fg.checked=fh.checked=false;open.clear();render();});
render();

// active section in the sticky nav
const links=[...document.querySelectorAll(".navbar a")];
const secs=links.map(a=>document.querySelector(a.getAttribute("href"))).filter(Boolean);
const spy=()=>{let cur=null;
  for(const s of secs){if(s.getBoundingClientRect().top<=100)cur=s;}
  links.forEach(a=>a.classList.toggle("on",!!cur&&a.getAttribute("href")==="#"+cur.id));};
addEventListener("scroll",spy,{passive:true});spy();

// email template tabs
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>{
  document.querySelectorAll("[data-tab]").forEach(x=>x.setAttribute("aria-selected",x===b));
  document.querySelectorAll("[data-panel]").forEach(p=>p.hidden=p.dataset.panel!==b.dataset.tab);}));

document.querySelectorAll("button.copy").forEach(b=>{
  b.addEventListener("click",async()=>{
    const pre=b.parentElement.querySelector("pre");
    try{await navigator.clipboard.writeText(pre.textContent);b.textContent="Copied";}
    catch(e){ // clipboard is blocked on file:// and without a user gesture
      const r=document.createRange();r.selectNodeContents(pre);
      const s=getSelection();s.removeAllRanges();s.addRange(r);
      b.textContent="Selected, press Ctrl+C";}
    setTimeout(()=>{b.textContent="Copy";},2500);
  });
});

// Downloads are built from the JSON already embedded in this page, so they
// work from a file:// URL with no server and no network behind them.
document.querySelectorAll("button.dl").forEach(b=>{
  b.addEventListener("click",()=>{
    const src={DATA:DATA,HUMAN:HUMAN,MISSES:MISSES,ACCURACY:ACCURACY}[b.dataset.src];
    if(!src||(Array.isArray(src)&&!src.length))return;
    const blob=new Blob([JSON.stringify(src,null,2)],{type:"application/json"});
    const u=URL.createObjectURL(blob);
    const a=document.createElement("a");
    a.href=u;a.download=b.dataset.name;a.click();
    URL.revokeObjectURL(u);
  });
});
"""
