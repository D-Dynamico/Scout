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

def headlines(pat, st, acc=None, misses=None):
    misses = misses or []
    n = pat["n"]
    ready = pat["stage"].get("ready to build", 0)
    self_serve = pat["access"].get("free self-serve", 0)
    gated = sum(pat["access"].get(k, 0) for k in schema.GATED_ACCESS)
    mcp = pat["mcp"].get("official", 0) + pat["mcp"].get("community", 0)
    official = pat["mcp"].get("official", 0)
    oauth = pat["auth"].get("OAuth2", 0)
    shipped = pat["composio_toolkit"]["already_shipped"]
    partner = pat["access"].get("partner or contact-sales", 0)
    review = pat["prod_requires_review"].get("True", 0)
    undetermined = pat["unresolved"]["stage_not_determined"]

    # What the hand-checked sample says about the two fields these claims rest
    # on. A headline that ignores its own accuracy number is just a louder guess.
    def field_acc(f):
        if not acc:
            return None
        d = acc.get("by_field", {}).get(f) or {}
        return d.get("verified") if d.get("n") else None

    acc_access = field_acc("access")
    acc_mcp = field_acc("mcp")
    access_lean = lean(misses, "access")
    mcp_ghosts = [m for m in misses
                  if m.get("which") == "verified" and m.get("field") == "mcp"
                  and m.get("got") == "official" and m.get("expected") == "none"]
    mcp_wrong = [m for m in misses
                 if m.get("which") == "verified" and m.get("field") == "mcp"
                 and m.get("got") is not None]

    if acc_access is not None and access_lean:
        buildable = (
            "%d/%d" % (ready, n),
            "Read as buildable today, and this is the softest number here.",
            "Free self-serve credentials and a documented REST or GraphQL API, "
            "no money and no waiting on a human. But access is the least "
            "accurate field we have, %.0f%% against the hand-checked sample, and "
            "when it is wrong it says free self-serve %d times out of %d. Read "
            "%d as a ceiling, not a count. The verification section shows the "
            "working."
            % (acc_access, access_lean["hits"], access_lean["of"], ready))
    else:
        buildable = (
            "%d/%d" % (ready, n),
            "Apps are buildable today.",
            "Free self-serve credentials and a documented REST or GraphQL API. No "
            "money, no forms, no waiting on a human. That is the single largest "
            "group and it is more than half the list.")

    if acc_mcp is not None and mcp_ghosts:
        names = ", ".join(sorted(set(m["name"] for m in mcp_ghosts))[:4])
        mcp_claim = (
            "%d/%d" % (mcp, n),
            "Have an MCP server, and it is the claim we trust least.",
            "%d of those read as official. We spot checked it and some hold up: "
            "Podio's is on Progress's own docs, SE Ranking's on its own domain. "
            "Then the hand-checked sample came in at %.0f%% on this field, and "
            "%d of its %d wrong answers claimed an official server for an app "
            "that has none at all. %s all read official and all were wrong. The "
            "direction of the error is consistent, so the real number is lower "
            "than %d and we cannot yet say by how much."
            % (official, acc_mcp, len(mcp_ghosts), len(mcp_wrong), names, official))
    else:
        mcp_claim = (
            "%d/%d" % (mcp, n),
            "Have an MCP server, %d of them official." % official,
            "This surprised us enough that we spot checked it by hand. The evidence "
            "holds up: Podio's is on Progress's own docs, LiveAgent's on its support "
            "site, Pumble's and SE Ranking's on their own domains. MCP has gone from "
            "a curiosity to table stakes across every category here.")

    return [
        buildable,
        ("%d/%d" % (gated, n),
         "Are gated behind money, an admin, or a salesperson.",
         "%d need a paid plan or a trial, %d need admin approval or an app review, "
         "and %d cannot be reached at all without talking to a human. Those last "
         "ones are a business development queue, not an engineering backlog."
         % (pat["access"].get("paid plan", 0) + pat["access"].get("free trial", 0),
            pat["access"].get("admin approval", 0), partner)),
        mcp_claim,
        ("%d/%d" % (oauth, n),
         "Use OAuth2, the default, but that is not the whole story.",
         "%d apps let you build today and still need the vendor to review your app "
         "before anyone else can use it. Slack, Meta, Google, and Salesforce all "
         "work this way. A connector catalogue that only tracks stage will tell you "
         "these are ready and be wrong at the worst moment." % review),
        ("%d/%d" % (shipped, n),
         "Already have a Composio toolkit shipping today.",
         "The remaining %d are the interesting half. Cross them against stage and "
         "%d of them are ready to build right now." % (n - shipped, sum(
             1 for r in st["verified"].values()
             if not r["extras"].get("composio_toolkit") and r.get("stage") == "ready to build"))),
        ("%d/%d" % (undetermined, n),
         "Rows we could not settle at all.",
         "No stage, because the pages we read never said clearly enough to pass "
         "the evidence checks. They are in the table, marked, rather than filled "
         "in with a plausible guess."),
        ("%d/8" % sum(1 for r in st["coverage"] if r["verified"] > r["pass1"]),
         "Tracked fields the verification loops improved.",
         "Stage went from %d to %d, api_type from %d to %d, auth from %d to %d. "
         "Median confidence went from %.2f to %.2f."
         % (st["coverage"][7]["pass1"], st["coverage"][7]["verified"],
            st["coverage"][2]["pass1"], st["coverage"][2]["verified"],
            st["coverage"][0]["pass1"], st["coverage"][0]["verified"],
            st["median_pass1"], st["median_verified"])),
    ]


# --- html -------------------------------------------------------------------

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6f6e68;--faint:#8b8a83;
--line:#e7e6e1;--card:#ffffff;--tint:#f5f4f0;--chip:#eeede8;
--accent:#2f6f4e;
--ready:#2f6f4e;--ready-bg:#e6f0ea;
--review:#8a5d12;--review-bg:#f6eeda;
--outreach:#9b3b2f;--outreach-bg:#f7e7e3;
--none:#6f6e68;--none-bg:#eeede8;
--warn:#8a5d12;--bad:#9b3b2f;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#14150f;--fg:#ececdf;--muted:#a3a297;--faint:#8b8a80;
--line:#2c2d25;--card:#1b1c15;--tint:#1e1f18;--chip:#26271f;
--accent:#7fbf95;
--ready:#7fbf95;--ready-bg:#1d2c22;
--review:#d8a95a;--review-bg:#2e2617;
--outreach:#e08b7a;--outreach-bg:#31201c;
--none:#a3a297;--none-bg:#26271f;
--warn:#d8a95a;--bad:#e08b7a;}}
:root[data-theme="dark"]{--bg:#14150f;--fg:#ececdf;--muted:#a3a297;--faint:#8b8a80;
--line:#2c2d25;--card:#1b1c15;--tint:#1e1f18;--chip:#26271f;--accent:#7fbf95;
--ready:#7fbf95;--ready-bg:#1d2c22;--review:#d8a95a;--review-bg:#2e2617;
--outreach:#e08b7a;--outreach-bg:#31201c;--none:#a3a297;--none-bg:#26271f;
--warn:#d8a95a;--bad:#e08b7a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px 96px}

h1{font-size:clamp(30px,4.6vw,46px);line-height:1.1;margin:0 0 14px;letter-spacing:-.025em}
h2{font-size:clamp(23px,2.8vw,31px);margin:0 0 4px;letter-spacing:-.02em;line-height:1.2}
h3{font-size:15px;margin:28px 0 10px;letter-spacing:.01em;font-weight:650}
p{margin:10px 0}
.lead{font-size:18px;line-height:1.55;max-width:68ch;color:var(--fg);margin:0 0 18px}
.sub{color:var(--muted);max-width:72ch;font-size:15px}
.cap{color:var(--faint);font-size:13px;line-height:1.5}
.take{font-size:17px;line-height:1.5;max-width:74ch;margin:0 0 18px;color:var(--fg)}
section{scroll-margin-top:64px;margin:0 0 64px}
section>h2+.take{margin-top:8px}
a{color:var(--accent);text-underline-offset:2px}
.num{color:var(--accent);font-variant-numeric:tabular-nums}

.navbar{position:sticky;top:0;z-index:50;background:var(--bg);
border-bottom:1px solid var(--line);margin:0 0 40px}
.navbar .inner{max-width:1100px;margin:0 auto;padding:10px 20px;display:flex;gap:4px;
flex-wrap:wrap;align-items:center;font-size:14px}
.navbar a{color:var(--muted);text-decoration:none;padding:5px 10px;border-radius:7px}
.navbar a:hover{color:var(--fg);background:var(--tint)}
.navbar a.on{color:var(--accent);background:var(--ready-bg);font-weight:600}

header{padding:56px 0 8px}
.statline{display:flex;flex-wrap:wrap;gap:8px 22px;margin:18px 0 0;
font-size:13px;color:var(--faint)}
.statline b{color:var(--fg);font-variant-numeric:tabular-nums;font-weight:650}

.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:26px 0 0}
.tile{background:var(--tint);border-radius:14px;padding:20px 22px}
.tile b{display:block;font-size:44px;line-height:1;letter-spacing:-.03em;
font-variant-numeric:tabular-nums}
.tile .then{display:inline-block;font-size:15px;color:var(--faint);margin-left:10px;
letter-spacing:0;vertical-align:7px;font-weight:500}
.tile span{display:block;margin-top:8px;font-size:15px;font-weight:600}
.tile i{display:block;margin-top:5px;font-size:13px;color:var(--muted);font-style:normal;
line-height:1.45}
@media(max-width:760px){.tiles{grid-template-columns:1fr}.tile b{font-size:34px}}

.claims{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:22px 0 0}
.claim{background:var(--tint);border-radius:12px;padding:18px 20px}
.claim .orb{width:78px;height:78px;border-radius:50%;display:grid;place-items:center;
background:var(--ready-bg);color:var(--accent);font-weight:700;font-size:17px;
letter-spacing:-.02em;font-variant-numeric:tabular-nums;margin-bottom:12px}
.claim b{display:block;font-size:15.5px;line-height:1.4;margin-bottom:5px;font-weight:650}
.claim span{color:var(--muted);font-size:13.5px;line-height:1.55;display:block}
@media(max-width:700px){.claims{grid-template-columns:1fr}}

.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
margin-top:18px}
.agentgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.agentgrid .card{text-align:center;padding:18px 20px}
.agentgrid .card h3{margin-bottom:8px}
.agentgrid .card .sub{max-width:44ch;margin:0 auto;font-size:13.5px;line-height:1.55}
@media(max-width:700px){.agentgrid{grid-template-columns:1fr}}
.card{background:var(--tint);border-radius:12px;padding:18px}
.card h3{margin-top:0}

table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:650;font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;
color:var(--faint);cursor:pointer;user-select:none;white-space:nowrap;position:sticky;top:0;
background:var(--bg);z-index:2}
tbody tr:hover{background:var(--tint)}
td.n,th.n{font-variant-numeric:tabular-nums}

.st{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12.5px;
font-weight:600;white-space:nowrap;line-height:1.35}
.st-ready{color:var(--ready);background:var(--ready-bg)}
.st-sandbox,.st-oauth{color:var(--review);background:var(--review-bg)}
.st-partner{color:var(--outreach);background:var(--outreach-bg)}
.st-none,.st-unknown{color:var(--none);background:var(--none-bg)}
.swatch{width:10px;height:10px;border-radius:3px;display:inline-block;vertical-align:-1px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin:12px 0 0}

.chip{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--chip);
font-size:12px;white-space:nowrap;color:var(--muted)}
.conf{display:inline-flex;align-items:center;gap:7px;white-space:nowrap}
.dot{width:9px;height:9px;border-radius:50%;flex:0 0 9px;display:inline-block}
.mark{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;
background:var(--chip);color:var(--muted);white-space:nowrap;vertical-align:1px}
.mark-weak{color:var(--review);background:var(--review-bg)}
.mark-rej{color:var(--outreach);background:var(--outreach-bg)}
.mark-human{color:var(--ready);background:var(--ready-bg)}
.ev{font-size:13px;color:var(--muted);margin:7px 0 0;padding-left:14px;
border-left:2px solid var(--line)}
.ev a{color:inherit;overflow-wrap:anywhere}
.wrap{overflow-wrap:break-word}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
.ev.rej{border-left-color:var(--outreach)}

.controls{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 10px;align-items:center}
input,select{font:inherit;font-size:13.5px;padding:7px 9px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg)}
.controls select{max-width:146px;text-overflow:ellipsis}
input{min-width:130px;flex:1 1 150px}
.controls label.chip{padding:5px 10px;font-size:12.5px}
#count{margin-left:auto}
.tablewrap{max-height:70vh;overflow:auto;border-radius:12px;background:var(--card);
border:1px solid var(--line)}
.card>.tablewrap{border:0;border-radius:0;max-height:none;background:transparent}
#t{min-width:0}
#t td{vertical-align:middle}
#t td.app{min-width:200px}
#t tr.r{cursor:pointer}
#t tr.expand td{background:var(--tint);padding:14px 16px;cursor:default}
.rowdetail{font-size:13.5px}
.rowdetail .one{color:var(--muted);margin:0 0 10px;font-size:14px}
.rowdetail .meta{display:flex;flex-wrap:wrap;gap:6px 14px;margin:0 0 10px;
font-size:13px;color:var(--muted)}

button.btn{font:inherit;font-size:13.5px;cursor:pointer;background:var(--card);
color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 12px}
button.btn:hover{border-color:var(--accent);color:var(--accent)}
button.dl{margin-top:12px}

.quad{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
.q{border-radius:12px;padding:16px 18px;background:var(--tint)}
.q b{font-size:30px;display:block;line-height:1;font-variant-numeric:tabular-nums}
.q em{display:block;font-style:normal;font-weight:650;font-size:14.5px;margin-top:8px}
.q span{color:var(--muted);font-size:13px;display:block;margin-top:3px}
.q-ready{background:var(--ready-bg)}
.q-review{background:var(--review-bg)}
.q-out{background:var(--outreach-bg)}
.q-none{background:var(--none-bg)}
@media(max-width:600px){.quad{grid-template-columns:1fr}}

.sbc{margin-top:18px;font-size:13px}
.sbc .row{display:grid;grid-template-columns:186px 1fr 46px;gap:12px;align-items:center;
padding:4px 0}
.sbc .lbl{color:var(--muted);text-align:right;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.sbc .track{display:flex;height:22px;border-radius:5px;overflow:hidden;background:var(--tint)}
.sbc .seg{height:100%}
.sbc .tot{color:var(--faint);font-variant-numeric:tabular-nums;font-size:12px}
@media(max-width:640px){.sbc .row{grid-template-columns:98px 1fr 34px;gap:8px}}

details.more{margin:14px 0;border-radius:10px;background:var(--tint);padding:0 16px}
details.more>summary{cursor:pointer;padding:12px 0;font-size:14.5px;font-weight:600;
list-style:none;display:flex;align-items:center;gap:8px}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::before{content:"+";color:var(--accent);font-weight:700;font-size:16px}
details.more[open]>summary::before{content:"\2212"}
details.more .hint{color:var(--faint);font-weight:400;font-size:13px;margin-left:auto}
details.more .inner{padding:0 0 16px;font-size:14.5px;color:var(--muted);line-height:1.6}
details.more .inner p{max-width:78ch}
details.more .inner h4{margin:18px 0 4px;font-size:14px;color:var(--fg);font-weight:650}

.note{border-left:3px solid var(--review);padding:12px 16px;background:var(--review-bg);
border-radius:0 8px 8px 0;margin:16px 0;font-size:14.5px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
pre{background:var(--tint);padding:16px;border-radius:10px;overflow:auto;font-size:13px;
line-height:1.55}
.kv{display:flex;justify-content:space-between;gap:12px;padding:6px 0;
border-bottom:1px solid var(--line);font-size:14px}
.kv:last-child{border-bottom:0}
.kv span:last-child{color:var(--muted);font-variant-numeric:tabular-nums}
ul.limits{margin:12px 0;padding-left:0;list-style:none}
ul.limits li{padding:10px 0 10px 20px;border-top:1px solid var(--line);font-size:14.5px;
position:relative;color:var(--muted);line-height:1.55}
ul.limits li:first-child{border-top:0}
ul.limits li::before{content:"";position:absolute;left:0;top:19px;width:9px;height:2px;
background:var(--faint)}
ul.limits b{color:var(--fg);font-weight:650}
.outcols{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
margin-top:16px}
.outcol{border-radius:12px;overflow:hidden;background:var(--tint)}
.outcol h3{margin:0;padding:12px 16px;font-size:14px;display:flex;
justify-content:space-between;align-items:center;gap:10px}
.outcol.g-review h3{background:var(--review-bg);color:var(--review)}
.outcol.g-out h3{background:var(--outreach-bg);color:var(--outreach)}
.outcol h3 em{font-style:normal;font-size:20px;font-variant-numeric:tabular-nums}
.outcol .body{padding:10px 16px 16px}
.outcol .app{display:flex;justify-content:space-between;gap:10px;padding:5px 0;
font-size:13.5px;border-bottom:1px solid var(--line)}
.outcol .app:last-of-type{border-bottom:0}
.outcol .app span{color:var(--faint);font-size:12px;white-space:nowrap}
footer{margin-top:72px;padding-top:22px;border-top:1px solid var(--line);
color:var(--faint);font-size:14px}
"""


def bar_chart(pairs, color="var(--accent)"):
    """A plain horizontal bar chart. Inline SVG so the file stays standalone."""
    if not pairs:
        return ""
    top = max(v for _, v in pairs) or 1
    rows, y = [], 0
    for label, value in pairs:
        w = int(320 * value / top)
        rows.append(
            '<text x="0" y="%d" font-size="12" fill="var(--muted)">%s</text>'
            '<rect x="170" y="%d" width="%d" height="13" rx="3" fill="%s"/>'
            '<text x="%d" y="%d" font-size="12" fill="var(--muted)">%d</text>'
            % (y + 11, esc(label[:26]), y + 1, max(w, 2), color,
               170 + max(w, 2) + 7, y + 11, value))
        y += 22
    return ('<svg viewBox="0 0 560 %d" width="100%%" height="%d" '
            'role="img">%s</svg>' % (y, y, "".join(rows)))


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
         "Read free-sounding language on the page as free credentials. The app "
         "is gated."),
        (lambda m: m.get("field") == "auth" and m.get("got") == "Bearer",
         "Read the Authorization header in a curl example instead of asking how "
         "the credential was obtained."),
        (lambda m: m.get("field") == "mcp" and m.get("got") == "official"
         and m.get("expected") == "none",
         "Claimed an official MCP server for an app that has none at all."),
        (lambda m: m.get("got") is None,
         "Found nothing it could evidence, so the field stayed null. Honest, "
         "and still a gap a human had to fill."),
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


def accent_numbers(html_text):
    """Put the accent colour on the figures in a claim, so the eye lands on the
    number before it reads the sentence. Runs on already-escaped text."""
    return re.sub(r"(\d+(?:\.\d+)?%?)", r'<span class="num">\1</span>', html_text)


def stage_chip(stage):
    return '<span class="st %s">%s</span>' % (
        stage_class(stage), esc(stage or "not determined"))


def stage_class(stage):
    return {"ready to build": "st-ready", "needs sandbox": "st-sandbox",
            "needs OAuth app review": "st-oauth",
            "needs partnership outreach": "st-partner",
            "no viable API": "st-none"}.get(stage, "st-unknown")


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


def build():
    pat = analyze.analyze()
    st = stats()
    acc, misses = load_accuracy()
    ver = st["verified"]
    rows = [ver[k] for k in sorted(ver)]

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
    a("<style>%s</style></head><body><div class=\"wrap\">" % CSS)

    # header
    a("<header>")
    a("<h1>100 apps, and what it actually takes to build a connector for each one</h1>")
    a('<p class="lead">An agent researched auth, access gating, API surface, MCP '
      'availability and buildability for 100 apps. Every claim on this page is '
      'backed by a quoted sentence from a page we fetched, and every claim that '
      'is not is shown as blank rather than guessed.</p>')
    a('<div class="statline">'
      '<span><b>%d</b> apps</span><span><b>%d</b> evidence snippets</span>'
      '<span><b>%d</b> checked by hand</span>'
      '<span>median confidence <b>%.2f</b></span><span>run <b>%s</b></span>'
      '</div>'
      % (pat["n"], pat["unresolved"]["total_evidence_items"],
         (acc or {}).get("sample_size", 0), st["median_verified"], RUN_DATE))

    # the three numbers a reviewer should leave with
    gated_n = sum(pat["access"].get(k, 0) for k in schema.GATED_ACCESS)
    outreach_n = pat["access"].get("partner or contact-sales", 0)
    # One overall accuracy figure, weighted by how many cells each field
    # actually contributed. Leading with the best field would be spin.
    overall = before = None
    best = worst = None
    if acc:
        scored = [(f, d) for f, d in acc.get("by_field", {}).items()
                  if d.get("n") and d.get("verified") is not None]
        if scored:
            cells = sum(d["n"] for _, d in scored)
            overall = sum(d["verified"] * d["n"] for _, d in scored) / cells
            before = sum(d["pass1"] * d["n"] for _, d in scored) / cells
            best = max(scored, key=lambda kv: kv[1]["verified"])
            worst = min(scored, key=lambda kv: kv[1]["verified"])
    a('<div class="tiles">')
    a('<div class="tile"><b>%d</b><span>read as buildable today</span>'
      '<i>of %d, before you discount for accuracy</i></div>' % (
          pat["stage"].get("ready to build", 0), pat["n"]))
    a('<div class="tile"><b>%d</b><span>need a human before you can start</span>'
      '<i>a paid plan, an admin, or a salesperson. %d of them cannot be reached '
      'without a conversation</i></div>' % (gated_n, outreach_n))
    if overall is not None:
        a('<div class="tile"><b>%.0f%%<span class="then">from %.0f%%</span></b>'
          '<span>of fields agree with a human</span>'
          '<i>across %d hand-checked apps, before and after the loops. Best is '
          '%s at %.0f%%, worst is %s at %.0f%%</i></div>'
          % (overall, before, acc.get("sample_size", 0),
             esc(best[0]), best[1]["verified"],
             esc(worst[0]), worst[1]["verified"]))
    else:
        a('<div class="tile"><b>not scored</b><span>agreement with a human</span>'
          '<i>run <span class="mono">scout score</span> once the sample is '
          'filled in</i></div>')
    a("</div>")
    a("</header>")

    a('<nav class="navbar"><div class="inner">'
      '<a href="#patterns">Patterns</a><a href="#charts">Charts</a>'
      '<a href="#table">Table</a><a href="#agent">Agent</a>'
      '<a href="#verify">Verification</a><a href="#outreach">Outreach</a>'
      '<a href="#proof">Proof</a><a href="#data">Data</a></div></nav>')

    # 1. patterns
    a('<section id="patterns"><h2>What the data says</h2>')
    a('<p class="take">Seven claims, each with a number behind it. The two we '
      'trust least say so in their first line.</p>')
    a('<div class="claims">')
    for stat, title, body in headlines(pat, st, acc, misses):
        a('<div class="claim"><div class="orb">%s</div>'
          '<b>%s</b><span>%s</span></div>'
          % (esc(stat), esc(title), esc(body)))
    a("</div></section>")

    # 2. where the easy wins are
    a('<section id="charts"><h2>Where the easy wins are</h2>')
    a('<p class="take">CRM and Sales is the softest target, %d of %d ready to '
      'build. Marketing and Ads is the hardest, %d of %d ready and %d gated, and '
      'every one of those gates is an app review rather than a bill. Developer '
      'and Infra is the odd one out: nothing in it is gated at all, but we could '
      'not settle %d of its %d rows, so that is our gap and not theirs.</p>'
      % (pat["stage_by_category"]["CRM and Sales"].get("ready to build", 0),
         sum(pat["stage_by_category"]["CRM and Sales"].values()),
         pat["stage_by_category"]["Marketing, Ads, Email and Social"].get(
             "ready to build", 0),
         sum(pat["stage_by_category"]["Marketing, Ads, Email and Social"].values()),
         pat["stage_by_category"]["Marketing, Ads, Email and Social"].get(
             "needs OAuth app review", 0)
         + pat["stage_by_category"]["Marketing, Ads, Email and Social"].get(
             "needs sandbox", 0)
         + pat["stage_by_category"]["Marketing, Ads, Email and Social"].get(
             "needs partnership outreach", 0),
         pat["stage_by_category"]["Developer, Infra and Data platforms"].get(
             "not determined", 0),
         sum(pat["stage_by_category"]["Developer, Infra and Data platforms"].values())))

    order = STAGE_ORDER + ["not determined"]
    a('<div class="sbc">')
    for cat, counts in sorted(pat["stage_by_category"].items(),
                              key=lambda kv: -kv[1].get("ready to build", 0)):
        total = sum(counts.values()) or 1
        a('<div class="row"><div class="lbl">%s</div><div class="track">' % esc(cat))
        for s in order:
            v = counts.get(s, 0)
            if not v:
                continue
            a('<div class="seg" style="width:%.2f%%;background:var(--%s)" '
              'title="%s: %d %s"></div>'
              % (v * 100.0 / total, STAGE_VAR[s], esc(cat), v, esc(s)))
        a('</div><div class="tot">%d</div></div>' % sum(counts.values()))
    a("</div>")
    # four colours, four labels. Listing six stages against four colours just
    # asks the reader to tell two identical swatches apart.
    a('<div class="legend">')
    for var, label in (("ready", "ready to build"),
                       ("review", "needs a sandbox or an app review"),
                       ("outreach", "needs partnership outreach"),
                       ("none", "no viable API, or we could not settle it")):
        a('<span><span class="swatch" style="background:var(--%s)"></span> %s</span>'
          % (var, esc(label)))
    a("</div>")

    a("<h3>Ease of build against worth building</h3>")
    a('<p class="sub">Ease comes from stage. Worth is name recognition or an API '
      'of real breadth, a hand written judgment that is in the repo so you can '
      'disagree with it.</p>')
    a('<div class="quad">')
    for key, cls_, label, blurb in (
            ("build now", "q-ready", "Build now",
             "Ready today and people want them"),
            ("worth the paperwork", "q-review", "Quick outreach",
             "Worth the review or the sales call"),
            ("easy, niche", "q-none", "Long game",
             "Easy to build, narrower demand"),
            ("park it", "q-out", "Skip",
             "Gated and niche. Revisit later")):
        a('<div class="q %s"><b>%d</b><em>%s</em><span>%s</span></div>'
          % (cls_, pat["quadrant"].get(key, 0), esc(label), esc(blurb)))
    a("</div></section>")

    # 3. the table
    a('<section id="table"><h2>The 100</h2>')
    a('<p class="take">One line per app, collapsed. Colour is stage, the dot is '
      'how well evidenced the row is, and the badge marks the twenty a human '
      'checked by hand.</p>')
    a('<div class="controls">')
    a('<input id="q" placeholder="Search app, blocker, note">')
    a('<select id="fstage"><option value="">Every stage</option>%s</select>'
      % "".join('<option>%s</option>' % esc(s) for s in STAGE_ORDER))
    a('<select id="fcat"><option value="">Every category</option>%s</select>'
      % "".join('<option>%s</option>' % esc(c) for c in schema.CATEGORIES))
    a('<select id="fauth"><option value="">Every auth</option>%s</select>'
      % "".join('<option>%s</option>' % esc(v) for v in schema.AUTH))
    a('<select id="faccess"><option value="">Every access</option>%s</select>'
      % "".join('<option>%s</option>' % esc(v) for v in schema.ACCESS))
    a('<label class="chip"><input type="checkbox" id="fgated" style="min-width:auto">'
      ' gated</label>')
    a('<label class="chip"><input type="checkbox" id="fhuman" style="min-width:auto">'
      ' human checked</label>')
    a('<span class="chip" id="count"></span>')
    a("</div>")
    a('<div class="tablewrap"><table id="t"><thead><tr>')
    for label in ("#", "App", "Stage", "Access", "Auth", "MCP", "Confidence",
                  "Evidence"):
        a("<th>%s</th>" % label)
    a("</tr></thead><tbody></tbody></table></div>")
    a('<p class="cap">Click any row for its one-liner, category, API type, '
      'production review, Composio toolkit, and every claim with its quoted '
      'snippet and source.</p>')
    a("</section>")

    # 4. agent and human
    a('<section id="agent"><h2>What the agent does, and where a human was still needed</h2>')
    a('<p class="take">The agent does the reading. A person decides what the '
      'words mean.</p>')
    a('<div class="agentgrid">')
    a('<div class="card"><h3>The manual version</h3>'
      '<p class="sub">For one app: search for the developer docs, find the auth '
      'page, find the pricing or signup page, work out whether you can get a key '
      'yourself, check whether an MCP server exists, then write it all down in a '
      'consistent shape. Call it fifteen minutes if the docs are good and the app '
      'is one you know. For 100 apps that is a full day, and by app 60 your '
      'definition of "free self-serve" has quietly drifted.</p></div>')
    a('<div class="card"><h3>What the agent owns</h3>'
      '<p class="sub">Three searches per app, six pages fetched and cached, one '
      'extraction call, a keyword shortlist and a targeted retrieval call for '
      'anything the first pass could not evidence, a support check per claim, a '
      'mechanical confidence score, and a lookup against Composio\'s 1,543 '
      'toolkits. About 45 seconds per app, six at a time. The enum never drifts, '
      'because a value outside it fails validation and the record is not written.</p></div>')
    a('<div class="card"><h3>What still needed a person</h3><p class="sub">'
      'Deciding the schema and what each enum value means. Ruling that a 14 day '
      'trial is not free self-serve. Noticing that confidence was scoring a wrong '
      'row 1.0. Spotting that the six pages for Sherlock were four different '
      'products. Judging which apps are well known. Reading the sample by hand. '
      'The agent is fast and consistent. It has no idea when it is confidently '
      'wrong, and that is the whole job.</p></div>')
    a('<div class="card"><h3>Where it struggled</h3><p class="sub">'
      'Small models paraphrase quotes instead of copying them, so we snap a '
      'paraphrase back to the nearest real sentence and store that. Search is not '
      'reproducible, so page sets are pinned per app in the repo. The model is not '
      'deterministic even at temperature 0, so the same input can give a different '
      'row, which is exactly why Loop A exists.</p></div>')
    a("</div></section>")

    # 5. verification
    a('<section id="verify"><h2>Verification</h2>')
    if acc:
        p1 = miss_shape(misses, "pass1")
        vf = miss_shape(misses, "verified")
        hi = [m for m in vf["wrong"] if (m.get("confidence") or 0) >= 0.70]
        a('<p class="take">Twenty apps were checked by hand against real docs, '
          'ten hard ones named in the brief and ten drawn one per category. The '
          'loops moved %d of the misses, mostly by filling blanks rather than by '
          'correcting wrong answers.</p>'
          % (p1["total"] - vf["total"]))

        a('<div class="tablewrap"><table><thead><tr>')
        for label in ("Field", "Hard half", "Random half", "Overall", "Checked"):
            a("<th>%s</th>" % label)
        a("</tr></thead><tbody>")

        def cell(d):
            if not d or not d.get("n"):
                return '<td class="n">not checked</td>'
            before_, after_ = d.get("pass1"), d.get("verified")
            if before_ is None or after_ is None:
                return '<td class="n">not checked</td>'
            delta = after_ - before_
            move = ""
            if abs(delta) >= 0.05:
                col = "var(--ready)" if delta > 0 else "var(--outreach)"
                move = (' <b style="color:%s">%+.0f</b>' % (col, delta))
            return ('<td class="n">%.0f%% &rarr; %.0f%%%s</td>'
                    % (before_, after_, move))

        for f in ACCURACY_FIELDS:
            over = acc["by_field"].get(f, {})
            if not over.get("n"):
                continue
            a("<tr><td><b>%s</b></td>" % esc(f))
            a(cell(acc["by_half"]["hard"].get(f)))
            a(cell(acc["by_half"]["random"].get(f)))
            a(cell(over))
            a('<td class="n">%d</td></tr>' % over["n"])
        a("</tbody></table></div>")
        a('<p class="cap">Each cell is the first full run, then the same field '
          'after the loops. Halves are shown apart because a blended number '
          'hides both: the hard half proves we did not cherry-pick, the random '
          'half is what a typical row is worth.</p>')

        a("<h3>What each loop checks, and what it changed</h3>")
        a('<div class="tablewrap"><table><thead><tr><th>Loop</th>'
          '<th>What it checks</th><th class="n">Changed</th></tr></thead><tbody>')
        for name, checks, changed in (
            ("A, self-consistency",
             "A second extraction with a different prompt on the same cached "
             "pages. Disagreements are flagged, never silently resolved.",
             "%d rows disagreed" % st["loop_a_rows"]),
            ("B, evidence",
             "Every snippet must still appear on the page it cites.",
             "0 rows"),
            ("C, browser",
             "A real browser loads the signup or pricing page for any row under "
             "0.7 confidence and reads the access model back.",
             "%d rows read, %d contradicted us"
             % (st["loop_c_rows"], len(st["loop_c_contradictions"]))),
            ("D, human sample",
             "Twenty apps checked by hand into a file the agent cannot write to.",
             "%d misses found" % vf["total"]),
            ("E, subject",
             "Asks whether the page is about the right app at all, by domain "
             "first and then by one small model call.",
             "%d pages stopped before extraction" % st["prefilter_pages"]),
            ("support check",
             "One model call per claim: does this snippet support this exact "
             "claim. A no nulls the field and records what was claimed.",
             "%d rows had a claim overruled"
             % pat["unresolved"]["rows_with_a_rejected_claim"]),
        ):
            a("<tr><td><b>%s</b></td><td>%s</td><td class='n'>%s</td></tr>"
              % (esc(name), esc(checks), esc(changed)))
        a("</tbody></table></div>")

        a("<h3>The three most instructive misses</h3>")
        picks = pick_instructive(misses)
        a('<div class="tablewrap"><table><thead><tr><th>App</th><th>Field</th>'
          '<th>Human found</th><th>Agent said</th><th>Why it happened</th>'
          '</tr></thead><tbody>')
        for m, why in picks:
            got = m.get("got")
            a("<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td></tr>"
              % (esc(m.get("name", "")), esc(m.get("field", "")),
                 esc(m.get("expected")),
                 '<span class="mark">said nothing</span>' if got is None
                 else esc(got),
                 esc(why)))
        a("</tbody></table></div>")

        a('<details class="more"><summary>The full misses log'
          '<span class="hint">%d rows after verification</span></summary>'
          '<div class="inner">' % vf["total"])
        a('<div class="tablewrap"><table><thead><tr>')
        for label in ("App", "Half", "Field", "Human found", "Agent said",
                      "Confidence", "Caught by"):
            a("<th>%s</th>" % label)
        a("</tr></thead><tbody>")
        shown = sorted([m for m in misses if m.get("which") == "verified"],
                       key=lambda m: (m.get("got") is None,
                                      -(m.get("confidence") or 0)))
        for m in shown:
            got = m.get("got")
            a("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td class='n'>%.2f</td><td>%s</td></tr>"
              % (esc(m.get("name", "")), esc(m.get("half", "")),
                 esc(m.get("field", "")), esc(m.get("expected")),
                 '<span class="mark">said nothing</span>' if got is None
                 else esc(got),
                 (m.get("confidence") or 0),
                 esc(m.get("caught_by") or "human only")))
        a("</tbody></table></div></div></details>")

        biases = []
        for f in ("access", "auth"):
            lb = lean(misses, f)
            if lb and lb["hits"] >= 2:
                biases.append((f, lb))
        blurb = {
            "access": ("The extractor reads any free-sounding language as free "
                       "credentials. Free describes the tool, not the signup. "
                       "Every app it got wrong here was gated in some way."),
            "auth": ("The extractor reads the shape of the Authorization header "
                     "in a curl example instead of asking how the credential was "
                     "obtained. A key pasted into a Bearer header and a token "
                     "from a consent redirect look identical on the page."),
        }
        a('<details class="more"><summary>Why the errors lean one way'
          '<span class="hint">two fields, one direction each</span></summary>'
          '<div class="inner">')
        a("<p>An error rate tells you how often to doubt a column. The direction "
          "tells you what to doubt it about, and these do not scatter.</p>")
        for f, lb in biases:
            a("<h4>%s guesses %s, %d of its %d wrong answers</h4><p>%s</p>"
              % (esc(f), esc(lb["value"]), lb["hits"], lb["of"],
                 esc(blurb.get(f, ""))))
        a("<h4>Confidence is not catching any of it</h4>")
        a("<p>%d of the %d wrong answers scored 0.70 or higher. Confidence only "
          "ever goes down, and it goes down for missing evidence, so a field "
          "backed by a real quoted snippet that the support check accepted still "
          "scores high when the snippet was read the wrong way. A high score "
          "means well evidenced, not correct.</p>" % (len(hi), vf["n_wrong"]))
        a("<h4>The loops trade silence for guesses</h4>")
        a("<p>Misses fell from %d to %d. Cases where a field came back null fell "
          "from %d to %d, which is what the retrieval step is for. But "
          "confidently wrong answers went from %d to %d. Filling a null only "
          "helps when the fill is right.</p>"
          % (p1["total"], vf["total"], p1["n_silent"], vf["n_silent"],
             p1["n_wrong"], vf["n_wrong"]))
        a("</div></details>")
    else:
        a('<p class="take">Per-field accuracy is not on this page yet, because '
          'the hand-checked sample is not finished. Until then the honest '
          'numbers are coverage and loop counts, not accuracy.</p>')

    # the two worked examples, collapsed and titled by their lesson
    p0 = config.DATA / "pass0" / "1.json"
    z = None
    if p0.exists():
        try:
            z = json.loads(p0.read_text(encoding="utf-8"))
        except ValueError:
            z = None
    if z:
        ev = next((e for e in (z.get("evidence") or [])
                   if e.get("field") == "access"), None)
        a('<details class="more"><summary>A real quote is not the same as proof'
          '<span class="hint">Salesforce, scored %.1f, and wrong</span></summary>'
          '<div class="inner">' % z.get("confidence", 0))
        a("<p>An early version of this pipeline checked that a quoted snippet "
          "really appeared on the page it cited. It never checked that the "
          "snippet supported the claim. So this row called access "
          "<span class=\'mono\'>%s</span> and gave itself full marks.</p>"
          % esc(z.get("access")))
        if ev:
            a('<p class="ev">&ldquo;%s&rdquo;<br><a href="%s" target="_blank" '
              'rel="noopener">%s</a></p>'
              % (esc((ev.get("snippet") or "").strip()[:240]),
                 esc(ev.get("url", "")), esc(ev.get("url", "")[:95])))
        a("<p>The sentence is real and the page is real. It is about approving "
          "an application's access to an org, which is a sentence about OAuth, "
          "not about whether a developer can get credentials without paying. "
          "Provenance passed and the claim was still unsupported. The support "
          "check exists because of this row, and confidence can no longer reach "
          "%.1f on evidence like this.</p>" % z.get("confidence", 0))
        a("</div></details>")

    a('<details class="more"><summary>Every gate can pass and the answer can '
      'still be wrong<span class="hint">Sherlock, four products, one '
      'name</span></summary><div class="inner">')
    a("<p>Sherlock is four different products: an open source OSINT command "
      "line tool, CloudFerro's Sherlock AI, and two unrelated companies at "
      "sherlocks.ai and usesherlock.ai. Searching the bare word returned all "
      "four, and five of the six pages we read were the wrong product.</p>")
    for step, what in (
        ("Domain check",
         "Matched host names by substring, so sherlocks.ai contained sherlock "
         "and was waved through as first party."),
        ("Extractor",
         "Read CloudFerro's API key docs and wrote access free self-serve, then "
         "a third party's MCP server and wrote mcp official. Its own notes said "
         "the CloudFerro page was not applicable. It filled the fields anyway."),
        ("Support check",
         "Both claims had real quotes from real pages, so both passed. "
         "Provenance cannot catch this. The snippet was genuine, the product "
         "was not."),
        ("Loop E",
         "Caught all four, after the fact, and nulled them. Correct, and too "
         "late: nulling a field is not the same as never having been wrong."),
    ):
        a("<h4>%s</h4><p>%s</p>" % (esc(step), esc(what)))
    a("<p>Four things changed. Search for auth and pricing is pinned to the "
      "domain apps.csv already knows. The subject check moved in front of the "
      "extractor. Host matching became label equality rather than substring. "
      "And mcp official now requires evidence from a domain the app owns. "
      "Sherlock now reads mcp community, which is what a human check says, and "
      "its access cell is blank rather than confidently wrong.</p>")
    a("<p>The names that break are not the ones you would guess. Front, Close, "
      "Clay, Plain, Linear, Grain and Harvest all resolved correctly, because "
      "those companies own the search results for their own word. What breaks "
      "is an app whose domain does not contain its name: Fathom is at "
      "fathom.video and pulled from usefathom.com, Otter AI is at otter.ai and "
      "pulled from connect.tryotter.com, a restaurant ordering platform.</p>")
    a("</div></details>")

    if st["loop_c_contradictions"]:
        a('<details class="more"><summary>A real browser argued back'
          '<span class="hint">%d of %d rows it checked</span></summary>'
          '<div class="inner">'
          % (len(st["loop_c_contradictions"]), st["loop_c_rows"]))
        a("<p>These are flagged, not corrected. A browser agent writing prose is "
          "a second opinion, not a quoted sentence, and quietly overwriting a "
          "field with it would be the kind of unevidenced edit this project "
          "exists to avoid.</p>")
        a('<div class="tablewrap"><table><thead><tr><th>App</th>'
          '<th>We recorded</th><th>What the live page said</th></tr></thead>'
          '<tbody>')
        for c in st["loop_c_contradictions"]:
            said = c["browser"].replace(chr(92) + chr(34), chr(34)).replace(
                chr(92) + "'", "'")
            if len(said) > 260:
                said = said[:260].rsplit(" ", 1)[0] + "..."
            a("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
              % (esc(c["name"]), esc(c["ours"]), esc(said)))
        a("</tbody></table></div></div></details>")

    a('<details class="more"><summary>Coverage, and the loop that found nothing'
      '<span class="hint">field by field, baseline against verified</span>'
      '</summary><div class="inner">')
    a('<div class="grid"><div class="card"><h3>Fields filled</h3>')
    for row in st["coverage"]:
        delta = row["verified"] - row["pass1"]
        mark = ("+%d" % delta) if delta > 0 else (str(delta) if delta
                                                  else "no change")
        a('<div class="kv"><span>%s</span><span>%d &rarr; %d &nbsp;%s</span></div>'
          % (esc(row["field"]), row["pass1"], row["verified"], mark))
    a('<div class="kv"><span>median confidence</span>'
      '<span>%.2f &rarr; %.2f</span></div></div>'
      % (st["median_pass1"], st["median_verified"]))
    a('<div class="card"><h3>Evidence</h3>'
      '<div class="kv"><span>from the app\'s own domain</span><span>%d</span></div>'
      '<div class="kv"><span>third party, confirmed on subject</span>'
      '<span>%d</span></div>'
      '<div class="kv"><span>marked weak</span><span>%d of %d</span></div>'
      '<div class="kv"><span>rows below 0.7 confidence</span><span>%d</span></div>'
      '</div></div>'
      % (st["subject_own"], st["subject_third"],
         pat["unresolved"]["weak_evidence_items"],
         pat["unresolved"]["total_evidence_items"],
         sum(1 for r in st["verified"].values() if r["confidence"] < 0.7)))
    a("<p>Loop B found nothing, and that is a finding about Loop B. It "
      "re-checks snippets against the same cached pages they were taken from, "
      "so it can only ever confirm itself. A real freshness check has to "
      "re-fetch the page and see whether the vendor still says it. The cache "
      "rule and the freshness check are in direct tension, and right now the "
      "cache rule wins.</p>")
    a("</div></details>")
    a("</section>")

    # 6. outreach
    a('<section id="outreach"><h2>Outreach queue</h2>')
    gate_email = {"admin approval": 0, "partner or contact-sales": 1,
                  "paid plan": 2}
    gate_cls = {"paid plan": "g-review", "admin approval": "g-review",
                "partner or contact-sales": "g-out"}
    gated_all = [it for v in pat["outreach_queue"].values() for it in v]
    no_route = sum(1 for it in gated_all
                   if not str((it.get("gate") or {}).get("application_url") or ""
                              ).startswith("http"))
    a('<p class="take">%d apps you cannot simply sign up for. Colour matches the '
      'table: amber needs a review or a plan, red needs a conversation. %d of '
      'the %d publish no application URL we could find, so for most of this '
      'queue the first step is finding a human to ask.</p>'
      % (len(gated_all), no_route, len(gated_all)))
    a('<div class="outcols">')
    for access, items in sorted(pat["outreach_queue"].items(),
                                key=lambda kv: -len(kv[1])):
        a('<div class="outcol %s"><h3>%s<em>%d</em></h3><div class="body">'
          % (gate_cls.get(access, "g-review"), esc(access), len(items)))
        for it in sorted(items, key=lambda i: i["app_id"]):
            gate = it.get("gate") or {}
            url = gate.get("application_url") or "unknown"
            proc = gate.get("process") or "unknown"
            if url and url.startswith("http"):
                right = ('<a href="%s" target="_blank" rel="noopener">apply</a>'
                         % esc(url))
            elif proc and proc != "unknown":
                right = '<span>%s</span>' % esc(proc[:34])
            else:
                right = '<span>no public route</span>'
            a('<div class="app"><span style="color:var(--fg)">%s</span>%s</div>'
              % (esc(it["name"]), right))
        label, subject, body = EMAILS[gate_email.get(access, 0)]
        a('<details><summary class="cap">%s email template</summary>'
          '<button class="btn copy">Copy this email</button>'
          '<pre>Subject: %s\n\n%s</pre></details>'
          % (esc(label), esc(subject), esc(body)))
        a("</div></div>")
    a("</div>")
    a("</section>")

    # 7. proof
    a('<section id="proof"><h2>Proof</h2>')
    a('<p class="sub">One command reproduces everything on this page.</p>')
    a("<pre>pip install -e .\ncp .env.example .env    # add COMPOSIO_API_KEY and GROQ_API_KEY\n"
      "scout probe             # confirm the tools and the model are really there\n"
      "scout research --all    # 100 apps, writes data/raw and freezes data/pass1\n"
      "scout verify --all      # loops A and B, writes data/verified\n"
      "scout analyze           # crosstabs into data/patterns.json\n"
      "scout build-page        # this file</pre>")
    a('<div class="grid"><div class="card"><h3>How to read a row</h3><p class="sub">'
      'Confidence starts at 1.0 and only goes down: a point off for a field we '
      'could not settle, more off for one resting on weak evidence. A filled '
      'field never raises it. Every deduction is named in the row notes.</p></div>'
      '<div class="card"><h3>Where the files live</h3><p class="sub">Per app '
      'records in <span class="mono">data/verified/</span>, the frozen first run '
      'in <span class="mono">data/pass1/</span>, the pinned page sets in '
      '<span class="mono">data/urls/</span>, and the hand-checked rows in '
      '<span class="mono">data/human_sample.json</span>.</p></div></div>')
    a("</section>")

    # 7. data and limits
    a('<section id="data"><h2>Take the data</h2>')
    a('<p class="sub">Everything on this page is embedded in it. The downloads '
      'below are built from that copy, so they work with no network and no '
      'server, and an agent can read the same JSON straight out of the '
      '<span class="mono">script</span> tags.</p>')
    a('<div class="grid">')
    for key, label, note in (
        ("DATA", "verified.json",
         "All %d rows after the verification loops, every field with its "
         "evidence: the source URL, the quoted snippet, which fetcher got it, "
         "and whether the support check called it strong or weak." % len(rows)),
        ("HUMAN", "human_sample.json",
         "The %d rows a human checked by hand against real docs. This is the "
         "ruler, not an output. The agent never writes to it."
         % (acc or {}).get("sample_size", 0)),
        ("MISSES", "misses.json",
         "Every disagreement between the agent and the human, for both the "
         "first run and the verified one, with which loop caught it."),
    ):
        a('<div class="card"><h3>%s</h3><p class="sub">%s</p>'
          '<button class="dl" data-src="%s" data-name="%s">Download %s</button>'
          '</div>' % (esc(label), esc(note), key, esc(label), esc(label)))
    a("</div>")

    a("<h3>Limits</h3>")
    a('<ul class="limits">')
    for title, body in (
        ("Search is not reproducible",
         "The same query returns different pages on different days, so an "
         "unpinned run quietly produces a different dataset every time. Once a "
         "run succeeds the URLs actually read are frozen into "
         "data/urls/, and a rerun reads that list instead of searching. That "
         "makes the numbers reproducible, and it also means they are only as "
         "good as the day the pages were pinned."),
        ("Ambiguous names are the real failure",
         "The risk is not ordinary English words like Front or Plain, which own "
         "their own search results. It is an app whose domain does not contain "
         "its name, where neither search nor the domain check has anything to "
         "hold on to. Fathom, Otter AI, YouTube Transcript and Sherlock all "
         "pulled pages from a different company with a similar name."),
        ("Most rows were never checked by a human",
         "%d of %d rows are hand-checked. The other %d carry the accuracy of "
         "whichever field you are reading, and the two fields that decide the "
         "most, access and stage, are the ones the sample scores lowest. A "
         "gated app recorded as free self-serve looks exactly like a correct "
         "row until you try to sign up."
         % ((acc or {}).get("sample_size", 0), len(rows),
            len(rows) - (acc or {}).get("sample_size", 0))),
        ("Freshness is not checked at all",
         "Loop B re-reads snippets against the cached copy of the page they "
         "came from, so it can only ever confirm itself. Nothing here re-fetches "
         "a page to ask whether the vendor still says it. Two apps were "
         "renamed mid-project, Coda to Superhuman Docs and fanbasis to Commas, "
         "and both were caught by a person, not by the pipeline."),
    ):
        a("<li><b>%s.</b> %s</li>" % (esc(title), esc(body)))
    a("</ul>")
    a("</section>")

    a('<footer>Built by an agent, checked by a human, and honest about which is '
      'which. Prose and judgment calls are in SESSIONS.md in the repo, including '
      'the eight bugs found along the way.</footer>')

    a("</div>")
    human_rows = []
    if config.HUMAN_SAMPLE.exists():
        try:
            human_rows = json.loads(
                config.HUMAN_SAMPLE.read_text(encoding="utf-8"))
        except ValueError:
            human_rows = []
    a("<script>const DATA=%s;</script>" % json.dumps(rows, ensure_ascii=False))
    a("<script>const HUMAN=%s;</script>"
      % json.dumps(human_rows, ensure_ascii=False))
    a("<script>const MISSES=%s;</script>"
      % json.dumps(misses, ensure_ascii=False))
    a("<script>const ACCURACY=%s;</script>"
      % json.dumps(acc, ensure_ascii=False))
    a("<script>%s</script>" % JS)
    a("</body></html>")

    config.SITE.mkdir(parents=True, exist_ok=True)
    out = config.SITE / "index.html"
    out.write_text("\n".join(h), encoding="utf-8")
    return out


JS = """
const GATED=["paid plan","admin approval","partner or contact-sales"];
const tb=document.querySelector("#t tbody");
const q=document.getElementById("q"),fs=document.getElementById("fstage"),
fc=document.getElementById("fcat"),fg=document.getElementById("fgated"),
fa=document.getElementById("fauth"),fx=document.getElementById("faccess"),
fh=document.getElementById("fhuman"),cnt=document.getElementById("count");
// app_ids a human checked by hand, so the table can say which rows are ruler
// and which are output.
const CHECKED=new Set((typeof HUMAN!=="undefined"?HUMAN:[]).map(r=>r.app_id));
const open=new Set();
let sortKey="app_id",sortDir=1;
function cls(s){return {"ready to build":"st-ready","needs sandbox":"st-sandbox",
"needs OAuth app review":"st-oauth","needs partnership outreach":"st-partner",
"no viable API":"st-none"}[s]||"st-unknown";}
function confColor(c){return c>=0.8?"var(--ready)":c>=0.6?"var(--review)":"var(--outreach)";}
function confWord(c){return c>=0.8?"well evidenced":c>=0.6?"partly":"thin";}
function val(r,k){
  if(k==="composio")return r.extras.composio_toolkit?1:0;
  if(k==="evidence")return r.evidence.length;
  if(k==="prod")return r.prod_requires_review===true?2:r.prod_requires_review===false?1:0;
  const v=r[k];return v===null||v===undefined?"":v;}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;",
"<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function evidence(r){
  let out="";
  const rej=r.rejected||{};
  const names=Object.keys(rej);
  if(names.length){
    out+=names.map(f=>'<p class="ev rej"><b>'+esc(f)+'</b> '+
      '<span class="mark mark-rej">overruled</span> the extractor said &ldquo;'+
      esc(rej[f].value)+'&rdquo; and the support check rejected it: '+
      esc(rej[f].reason)+'</p>').join("");}
  if(!r.evidence.length)
    return out+'<p class="ev">No evidence survived the checks for this row.</p>';
  return out+r.evidence.map(e=>'<p class="ev"><b>'+esc(e.field)+'</b> '+
  (e.support==="weak"?'<span class="mark mark-weak">weak</span>':'')+
  (e.subject&&e.subject!=="own"?' <span class="mark">third party</span>':'')+
  ' &ldquo;'+esc(e.snippet)+'&rdquo;<br><a href="'+esc(e.url)+
  '" target="_blank" rel="noopener">'+esc(e.url.slice(0,90))+'</a>'+
  '<span style="color:var(--faint)"> via '+esc(e.fetcher||"unknown")+'</span></p>')
  .join("");}
function detail(r){
  const bits=[["Category",r.category],["API",(r.api_type||"not set")+
    (r.api_breadth&&r.api_breadth!=="unknown"?", "+r.api_breadth:"")],
    ["Production review",r.prod_requires_review===true?"needed":
      r.prod_requires_review===false?"not needed":"not established"],
    ["Composio toolkit",r.extras.composio_toolkit?"ships today":"not yet"],
    ["Confidence",r.confidence.toFixed(2)]];
  return '<tr class="expand"><td colspan="8"><div class="rowdetail">'+
    (r.one_liner?'<p class="one">'+esc(r.one_liner)+'</p>':'')+
    '<div class="meta">'+bits.map(b=>'<span><b>'+b[0]+':</b> '+esc(b[1])+
      '</span>').join("")+'</div>'+
    (r.blocker?'<p class="one"><b>Blocker:</b> '+esc(r.blocker)+'</p>':'')+
    evidence(r)+
    (r.notes?'<p class="ev">'+esc(r.notes)+'</p>':'')+
    '</div></td></tr>';}
function render(){
  const t=q.value.toLowerCase(),s=fs.value,c=fc.value,g=fg.checked,
    au=fa.value,ax=fx.value,hu=fh.checked;
  let rows=DATA.filter(r=>{
    if(s&&r.stage!==s)return false;
    if(c&&r.category!==c)return false;
    if(au&&r.auth!==au)return false;
    if(ax&&r.access!==ax)return false;
    if(hu&&!CHECKED.has(r.app_id))return false;
    if(g&&!GATED.includes(r.access))return false;
    if(t){const hay=(r.name+" "+(r.blocker||"")+" "+(r.notes||"")+" "+
      (r.one_liner||"")+" "+(r.access||"")+" "+(r.auth||"")).toLowerCase();
      if(!hay.includes(t))return false;}
    return true;});
  rows.sort((a,b)=>{const x=val(a,sortKey),y=val(b,sortKey);
    return (x>y?1:x<y?-1:0)*sortDir;});
  cnt.textContent=rows.length+" of "+DATA.length;
  tb.innerHTML=rows.map(r=>{
    const conf=r.confidence;
    const nweak=r.evidence.filter(e=>e.support==="weak").length;
    const nrej=Object.keys(r.rejected||{}).length;
    const isOpen=open.has(r.app_id);
    return '<tr class="r" data-id="'+r.app_id+'">'+
    '<td class="n">'+r.app_id+'</td>'+
    '<td class="app"><b>'+esc(r.name)+'</b>'+
      (CHECKED.has(r.app_id)?' <span class="mark mark-human">human</span>':'')+'</td>'+
    '<td>'+'<span class="st '+cls(r.stage)+'">'+esc(r.stage||"not determined")+
      '</span></td>'+
    '<td>'+esc(r.access||"not set")+'</td>'+
    '<td>'+esc(r.auth||"not set")+'</td>'+
    '<td>'+esc(r.mcp||"not set")+'</td>'+
    '<td><span class="conf" title="confidence '+conf.toFixed(2)+'">'+
      '<span class="dot" style="background:'+confColor(conf)+'"></span>'+
      confWord(conf)+'</span></td>'+
    '<td class="n">'+r.evidence.length+
      (nweak?' <span class="mark mark-weak">'+nweak+' weak</span>':'')+
      (nrej?' <span class="mark mark-rej">'+nrej+'</span>':'')+'</td></tr>'+
    (isOpen?detail(r):"");
  }).join("");}
tb.addEventListener("click",e=>{
  const tr=e.target.closest("tr.r");
  if(!tr||e.target.closest("a"))return;
  const id=Number(tr.dataset.id);
  if(open.has(id))open.delete(id);else open.add(id);
  render();});
document.querySelectorAll("#t th").forEach((th,i)=>{
  const keys=["app_id","name","stage","access","auth","mcp","confidence","evidence"];
  th.addEventListener("click",()=>{
    const k=keys[i];
    if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=1;}
    render();});});
[q,fs,fc,fg,fa,fx,fh].forEach(el=>{
  el.addEventListener("input",render);
  el.addEventListener("change",render);});
render();

// active section in the sticky nav
const links=[...document.querySelectorAll(".navbar a")];
const secs=links.map(a=>document.querySelector(a.getAttribute("href"))).filter(Boolean);
const spy=()=>{
  let cur=secs[0];
  for(const s of secs){ if(s.getBoundingClientRect().top<=90)cur=s; }
  links.forEach(a=>a.classList.toggle("on",
    cur&&a.getAttribute("href")==="#"+cur.id));};
addEventListener("scroll",spy,{passive:true});spy();

document.querySelectorAll("button.copy").forEach(b=>{
  b.addEventListener("click",async()=>{
    const pre=b.parentElement.querySelector("pre");
    try{await navigator.clipboard.writeText(pre.textContent);
      b.textContent="Copied";}
    catch(e){ // clipboard is blocked on file:// and without a user gesture
      const r=document.createRange();r.selectNodeContents(pre);
      const s=getSelection();s.removeAllRanges();s.addRange(r);
      b.textContent="Selected, press ctrl+C";}
    setTimeout(()=>{b.textContent="Copy this email";},2500);
  });
});

// Downloads are built from the JSON already embedded in this page, so they
// work from a file:// URL with no server and no network behind them.
document.querySelectorAll("button.dl").forEach(b=>{
  b.addEventListener("click",()=>{
    const src={DATA:DATA,HUMAN:HUMAN,MISSES:MISSES,ACCURACY:ACCURACY}[b.dataset.src];
    if(!src||(Array.isArray(src)&&!src.length)){
      b.textContent="nothing to download yet";return;}
    const blob=new Blob([JSON.stringify(src,null,2)],{type:"application/json"});
    const u=URL.createObjectURL(blob);
    const a=document.createElement("a");
    a.href=u;a.download=b.dataset.name;a.click();
    URL.revokeObjectURL(u);
  });
});
"""
