"""Verification loops. Reads data/raw/, writes data/verified/, never touches
data/pass1/.

The point is not to make the numbers look better. It is to find out how wrong
the first pass was, on purpose, and write that down.

  Loop A  self consistency. Extract a second time with a different prompt and a
          different model on the same cached pages. Disagreements are flagged,
          never silently resolved.
  Loop B  evidence integrity. Every evidence URL must resolve and every snippet
          must still be on that page. Failures null the field.
  Loop C  browser check. For low confidence rows and rows where Loop A argued
          about access, load the real signup or pricing page in a browser and
          ask what it actually says.
"""

import concurrent.futures
import copy
import json
import threading

from . import cache, config, extract, pipeline, schema, subject, tools

CLAIM_FIELDS = schema.EVIDENCE_REQUIRED

# A second opinion is only worth having if it is genuinely independent, so
# Loop A uses a different model as well as a differently worded prompt.
ALT_MODEL = "openai/gpt-oss-120b"

ALT_PROMPT = """Below are pages about {name}. Answer six questions about its API
using only what these pages say.

{pages}

1. auth: how does a developer authenticate? One of {auth}, or null.
2. access: what must a developer do to get working API credentials? One of
   {access}, or null.
3. api_type: what kind of interface is it? One of {api_type}, or null.
4. api_breadth: how many endpoints? One of {api_breadth}, or null.
5. mcp: is there a Model Context Protocol server? One of {mcp}, or null.
6. prod_requires_review: does shipping to other people's accounts need the
   vendor to approve the app first? true, false, or null.

Answer from the pages, not from what you already know about {name}. Null is a
correct answer and is better than a guess.

JSON only: {{"auth": ..., "access": ..., "api_type": ..., "api_breadth": ...,
"mcp": ..., "prod_requires_review": ...}}"""


def load_raw(app_id):
    p = config.RAW / ("%d.json" % app_id)
    if not p.exists():
        raise FileNotFoundError("no raw record for app %d, run research first" % app_id)
    return json.loads(p.read_text(encoding="utf-8"))


def pages_for(app_id):
    urls = pipeline.load_pinned(app_id) or []
    return [p for p in (cache.get(u) for u in urls) if p and p.get("text")]


# --- Loop A ----------------------------------------------------------------

def loop_a(rec, pages):
    """Second extraction, different prompt, different model. Returns a list of
    disagreements. Nothing is overwritten: a disagreement is information about
    how much to trust the row, not a correction."""
    if not pages:
        return []

    blocks = []
    for p in pages:
        text = (p.get("text") or "")[:12000]
        if text:
            blocks.append("--- PAGE %s ---\n%s" % (p["url"], text))

    prompt = ALT_PROMPT.format(
        name=rec["name"],
        pages="\n\n".join(blocks),
        auth=schema.AUTH, access=schema.ACCESS, api_type=schema.API_TYPE,
        api_breadth=schema.API_BREADTH, mcp=schema.MCP,
    )

    try:
        second = extract.parse_json(
            extract.call_text(prompt, model=ALT_MODEL, json_mode=True) or "")
    except Exception:  # noqa: BLE001 - a failed second opinion is not a verdict
        return []
    if not isinstance(second, dict):
        return []

    out = []
    for f in CLAIM_FIELDS:
        if f == "api_docs_url":
            continue  # a URL string will never match exactly, and Loop B covers it
        mine, theirs = rec.get(f), second.get(f)
        if theirs is None or mine is None:
            continue
        allowed = extract.RETRIEVE_ALLOWED.get(f)
        if allowed is not None and theirs not in allowed:
            continue
        if mine != theirs:
            out.append("%s: pass1 said %r, second model said %r" % (f, mine, theirs))
    return out


# --- Loop B ----------------------------------------------------------------

def loop_b(rec, pages):
    """Every evidence URL must resolve and every snippet must still be on it.

    A field whose evidence does not survive is nulled. This is the loop that
    catches a snippet that was never really there, a page that has since
    changed, and any bug of ours that let an unbacked claim through.
    """
    by_url = dict((p["url"], p) for p in pages)
    failures, survivors = [], []

    for item in rec["evidence"]:
          page = by_url.get(item["url"]) or cache.get(item["url"])
          if page is None:
              page = tools.scrape(item["url"])
          if not page or not page.get("text"):
              failures.append("%s: evidence url did not resolve, %s"
                              % (item["field"], item["url"]))
              continue
          if not extract.snippet_supported(item["snippet"], [page]):
              failures.append("%s: snippet is no longer on %s"
                              % (item["field"], item["url"]))
              continue
          survivors.append(item)

    rec["evidence"] = survivors
    backed = set(i["field"] for i in survivors)
    for f in CLAIM_FIELDS:
        if f == "api_breadth" and rec.get(f) == "unknown":
            continue
        if rec.get(f) is not None and f not in backed:
            failures.append("%s nulled, its evidence did not survive Loop B" % f)
            rec[f] = None

    if rec["mcp"] is None:
        rec["mcp_url"] = None
    return failures


# --- Loop C ----------------------------------------------------------------

BROWSER_TASK = (
    "Go to {url}. I need to know one thing: can a developer sign up and get "
    "API credentials themselves, right now, without paying and without a "
    "human approving them? Look for a free tier, a developer plan, a signup "
    "button, or wording like contact sales, request access, or apply. "
    "Answer in two sentences, quoting the exact wording you saw."
)


def loop_c(rec, timeout_s=180):
    """Load the real page in a browser and ask what it says about access.

    Reserved for rows we already distrust, because it is the slowest and most
    expensive check we have. The answer is recorded in notes as a human
    readable second opinion rather than used to overwrite access, since a
    browser agent's prose is not evidence in the schema sense.
    """
    url = rec.get("website") or rec.get("api_docs_url")
    if not url or not url.startswith("http"):
        return None

    try:
        created = tools.execute("BROWSER_TOOL_CREATE_TASK", {
            "task": BROWSER_TASK.format(url=url),
            "startUrl": url,
        })
    except Exception as exc:  # noqa: BLE001
        return "browser check failed to start: %s" % type(exc).__name__

    data = created.get("data") if isinstance(created, dict) else None
    task_id = None
    if isinstance(data, dict):
        task_id = (data.get("watch_task_id") or data.get("taskId")
                   or data.get("task_id") or data.get("id"))
    if not task_id:
        return "browser check did not return a task id"

    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            res = tools.execute("BROWSER_TOOL_WATCH_TASK", {"taskId": task_id})
        except Exception:  # noqa: BLE001
            return "browser check could not be watched"
        d = res.get("data") if isinstance(res, dict) else None
        if isinstance(d, dict):
            status = str(d.get("status", "")).lower()
            if status in ("finished", "completed", "done", "success"):
                answer = d.get("output") or d.get("result") or d.get("summary")
                return str(answer)[:500] if answer else "browser check returned nothing"
            if status in ("failed", "error", "stopped"):
                return "browser check failed: %s" % str(d.get("error"))[:120]
        time.sleep(5)
    return "browser check timed out"


# --- the loop --------------------------------------------------------------

_print_lock = threading.Lock()


def verify_app(app_id, browser=False):
    config.ensure_dirs()
    config.VERIFIED.mkdir(parents=True, exist_ok=True)

    rec = copy.deepcopy(load_raw(app_id))
    pages = pages_for(app_id)
    notes = []

    disagreements = loop_a(rec, pages)
    if disagreements:
        notes.append("loop A: " + "; ".join(disagreements))

    failures = loop_b(rec, pages)
    if failures:
        notes.append("loop B: " + "; ".join(failures))

    app = pipeline.get_app(app_id)
    removed, _third = loop_e(rec, app, pages)
    if removed:
        notes.append("loop E: " + "; ".join(removed))

    # stage follows access, which Loop B may have just nulled
    rec["stage"], rec["blocker"] = schema.derive_stage(rec["access"], rec["api_type"])
    if rec["access"] not in schema.GATED_ACCESS:
        rec["gate"] = None
    elif not rec["gate"]:
        rec["gate"] = {"application_url": "unknown", "process": "unknown",
                       "contact": "unknown", "est_time": "unknown"}

    needs_browser = rec["confidence"] < 0.7 or any("access" in d for d in disagreements)
    if browser and needs_browser:
        said = loop_c(rec)
        if said:
            notes.append("loop C browser check: " + said)

    rec["confidence"], reasons = pipeline.score_confidence(rec, pages)
    if reasons:
        notes.append("confidence after verification: " + "; ".join(reasons))

    rec["notes"] = " | ".join(filter(None, [rec.get("notes")] + notes))
    schema.validate_or_raise(rec)
    (config.VERIFIED / ("%d.json" % app_id)).write_text(
        json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    return rec, disagreements, failures


def verify_all(workers=6, browser=False, browser_limit=15):
    apps = pipeline.load_apps()
    done, failed = [], []
    browser_budget = [browser_limit]

    def one(app):
        app_id = app["app_id"]
        try:
            use_browser = False
            if browser:
                with _print_lock:
                    if browser_budget[0] > 0:
                        browser_budget[0] -= 1
                        use_browser = True
            rec, dis, fails = verify_app(app_id, browser=use_browser)
            with _print_lock:
                print("ok   %3d %-26s conf %.2f  %-24s A:%d B:%d"
                      % (app_id, rec["name"][:26], rec["confidence"],
                         (rec["stage"] or "-")[:24], len(dis), len(fails)))
            return rec
        except Exception as exc:  # noqa: BLE001
            with _print_lock:
                print("FAIL %3d %-26s %s: %s"
                      % (app_id, app["name"][:26], type(exc).__name__, exc))
            failed.append(app_id)
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(one, apps):
            if rec is not None:
                done.append(rec)

    print("\nverified: %d ok, %d failed" % (len(done), len(failed)))
    return done


def browser_pass(limit=15, workers=3):
    """Loop C on its own, over rows already verified.

    Kept separate because Loop C is the expensive one. Re-running A and B just
    to reach it would burn a few hundred model calls and re-introduce the
    run-to-run variance we already know about, for no gain. This loads the
    verified records, picks the ones we distrust most, and appends what a real
    browser saw.
    """
    recs = []
    for p in sorted(config.VERIFIED.glob("*.json")):
        recs.append(json.loads(p.read_text(encoding="utf-8")))

    def suspect(r):
        return (r["confidence"] < 0.7
                or r.get("access") is None
                or "access:" in (r.get("notes") or ""))

    picked = sorted([r for r in recs if suspect(r)],
                    key=lambda r: r["confidence"])[:limit]
    print("loop C on %d rows, lowest confidence first" % len(picked))

    def one(rec):
        said = loop_c(rec)
        if not said:
            return None
        rec["notes"] = " | ".join(filter(None, [
            rec.get("notes"), "loop C browser check: " + said]))
        schema.validate_or_raise(rec)
        (config.VERIFIED / ("%d.json" % rec["app_id"])).write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        with _print_lock:
            print("  %3d %-24s %s" % (rec["app_id"], rec["name"][:24], said[:90]))
        return rec

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        done = [r for r in pool.map(one, picked) if r is not None]
    print("loop C finished on %d rows" % len(done))
    return done


# --- Loop E, subject check --------------------------------------------------

# The subject check itself lives in subject.py, because the pipeline now runs
# it before extraction as well. Loop E is the safety net, not the only gate.
from .subject import (CODE_HOSTS, MULTI_PROJECT_HOSTS, domains_for,  # noqa: E402
                      is_own_domain, page_is_about, repo_path, subject_key)


def loop_e(rec, app, pages):
    """Is every page we quoted actually about this app?

    Two layers, cheapest first. The domain check costs nothing and clears most
    evidence outright. Only genuinely third-party pages reach the model. A page
    that fails both is removed from this app's evidence, because a real quote
    from the wrong product is the most convincing kind of wrong answer there is.

    When apps.csv has no domain for the app, both layers stand down, the same
    way the pre-filter does. Paygent Connect's website is recorded as
    "unknown", so every page counted as third party and the model was asked to
    adjudicate with nothing to adjudicate against. It threw away what were
    probably the real docs and took the row from 0.84 to 0.37. A check with no
    anchor is not a strict check, it is a coin toss with a bias.
    """
    from urllib.parse import urlparse

    if not domains_for(app):
        return ["subject check skipped, apps.csv has no domain for this app"], 0
    text_by_url = dict((p["url"], p.get("text")) for p in pages)

    def host_of(url):
        return (urlparse(url).hostname or "").lower().replace("www.", "")

    # First decide, per host, whether that host is about this app. Verdicts
    # aggregate per host rather than per page. Sherlock showed why: the checker
    # said no to one docs.cloudferro.com page and yes to another, and the yes
    # let a different product's REST API through. One no condemns the host.
    verdicts, third_party = {}, 0
    for item in rec["evidence"]:
        if is_own_domain(host_of(item["url"]), app, item["url"]):
            continue
        key = subject_key(item["url"])
        third_party += 1
        v = page_is_about(app, item["url"], text_by_url.get(item["url"]))
        if v is False:
            verdicts[key] = False
        elif key not in verdicts:
            # None means the check could not run, which is not a failure
            verdicts[key] = True

    survivors, removed = [], []
    for item in rec["evidence"]:
        host = host_of(item["url"])
        if is_own_domain(host, app, item["url"]):
            item["subject"] = "own-domain"
            survivors.append(item)
        elif verdicts.get(subject_key(item["url"])) is False:
            removed.append("%s: dropped, %s is not about %s"
                           % (item["field"], subject_key(item["url"]), app["name"]))
        else:
            item["subject"] = "third-party-confirmed"
            survivors.append(item)

    rec["evidence"] = survivors

    backed = set(i["field"] for i in survivors)
    for f in CLAIM_FIELDS:
        if f == "api_breadth" and rec.get(f) == "unknown":
            continue
        if rec.get(f) is not None and f not in backed:
            removed.append("%s nulled, its only evidence was another product's page" % f)
            rec[f] = None
    if rec["mcp"] is None:
        rec["mcp_url"] = None

    demoted = subject.demote_unofficial_mcp(rec, app)
    if demoted:
        removed.append(demoted)

    return removed, third_party


def subject_pass(workers=6):
    """Run Loop E over every verified record and rewrite them in place."""
    apps = dict((a["app_id"], a) for a in pipeline.load_apps())
    recs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(config.VERIFIED.glob("*.json"))]
    recs = [r for r in recs if isinstance(r, dict) and "app_id" in r]

    touched, total_removed, total_third = [], 0, 0

    def one(rec):
        nonlocal total_removed, total_third
        app = apps[rec["app_id"]]
        pages = pages_for(rec["app_id"])
        removed, third = loop_e(rec, app, pages)

        rec["stage"], rec["blocker"] = schema.derive_stage(rec["access"], rec["api_type"])
        if rec["access"] not in schema.GATED_ACCESS:
            rec["gate"] = None
        elif not rec["gate"]:
            rec["gate"] = {"application_url": "unknown", "process": "unknown",
                           "contact": "unknown", "est_time": "unknown"}
        if removed:
            rec["notes"] = " | ".join(filter(None, [
                rec.get("notes"), "loop E: " + "; ".join(removed)]))
        rec["confidence"], reasons = pipeline.score_confidence(rec, pages)

        schema.validate_or_raise(rec)
        (config.VERIFIED / ("%d.json" % rec["app_id"])).write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")

        with _print_lock:
            total_third += third
            if removed:
                total_removed += len([r for r in removed if "dropped" in r])
                print("  %3d %-24s dropped %d, conf %.2f"
                      % (rec["app_id"], rec["name"][:24],
                         len([r for r in removed if "dropped" in r]), rec["confidence"]))
        return rec if removed else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(one, recs):
            if r is not None:
                touched.append(r)

    print("\nloop E: %d third-party pages examined, %d evidence items dropped, "
          "%d rows changed" % (total_third, total_removed, len(touched)))
    return touched
