"""The research loop. Plain Python, one app at a time, resumable.

search -> fetch -> extract -> score -> registry -> write.

Resumable means: one JSON file per app in data/raw/. If the run dies at app
47, rerunning skips the 46 that are already on disk and every page they used
is already in cache/.
"""

import concurrent.futures
import csv
import json
import threading

from . import cache, config, extract, schema, subject, tools


def load_apps():
    """apps.csv is the source of identity. The pipeline never invents an app."""
    if not config.APPS_CSV.exists():
        raise FileNotFoundError(
            "apps.csv not found. It holds the 100 apps with category and docs hint."
        )
    with open(config.APPS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    apps = []
    for r in rows:
        apps.append({
            "app_id": int(r["app_id"]),
            "name": r["name"].strip(),
            "website": r["website"].strip(),
            "category": r["category"].strip(),
            "docs_hint": (r.get("docs_hint") or "").strip(),
        })
    return apps


def get_app(app_id):
    for a in load_apps():
        if a["app_id"] == app_id:
            return a
    raise KeyError("no app with id %s in apps.csv" % app_id)


# --- step 1 and 2: find pages and fetch them -------------------------------

def pinned_path(app_id):
    return config.URLS / ("%d.json" % app_id)


def load_pinned(app_id):
    """The URLs a previous successful run actually read, or None."""
    p = pinned_path(app_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("urls") or None
    except (ValueError, OSError):
        return None


def save_pinned(app_id, urls):
    """Merge, never replace.

    Search is not reproducible. Ask it the same question next week and it
    returns a different set, so an unpinned pipeline quietly produces a
    different dataset every run. Merging means a page that search surfaced once
    is never lost because search did not surface it the second time.
    """
    config.URLS.mkdir(parents=True, exist_ok=True)
    existing = load_pinned(app_id) or []
    merged = list(existing)
    for u in urls:
        if u not in merged:
            merged.append(u)
    pinned_path(app_id).write_text(
        json.dumps({"app_id": app_id, "urls": merged}, indent=2), encoding="utf-8")
    return merged


def first_party_hits(app, question, limit=3):
    """Search for one question and keep only pages on the app's own domain.

    Two defences, because either one alone leaks. The site: operator asks the
    engine for the right domain, and the allowlist check enforces it on the way
    back, since not every search backend honours site:. When site: returns
    nothing we ask again without it and let the allowlist do the work, so a
    backend that ignores the operator costs us nothing.
    """
    domain = subject.primary_domain(app)
    hits = []
    if domain:
        hits = tools.search("site:%s %s" % (domain, question), limit=limit)
    if not hits:
        hits = tools.search("%s %s" % (app["name"], question), limit=limit)
    return [h for h in hits
            if subject.is_own_domain(subject.host_of(h["url"]), app, h["url"])]


def candidate_urls(app, research=False):
    """The pinned set if we have one, otherwise the docs hint plus whatever
    search turns up for the three questions we care about.

    research=True searches again even when a pinned set exists, and the new
    URLs get merged into the pin rather than replacing it.

    Searching the bare app name is how Sherlock went wrong. Roughly a sixth of
    the list are ordinary words before they are companies, and the engine has
    no way to know which Front or which Close we mean. So the search is split
    in two. Questions about auth and pricing are answered only from the app's
    own domain, which apps.csv already knows. The MCP question has to stay open,
    because an MCP server usually lives on someone else's GitHub, so it carries
    a disambiguating word instead: sherlock-project, not Sherlock.
    """
    pinned = load_pinned(app["app_id"])
    if pinned and not research:
        return pinned[:config.MAX_PAGES_PER_APP]

    urls, seen = [], set()

    def add(u):
        if u and u.startswith("http") and u not in seen:
            seen.add(u)
            urls.append(u)

    for u in (pinned or []):
        add(u)
    add(app.get("docs_hint"))

    # Three questions. They are taken round robin rather than one query at a
    # time, because the page budget is small and the first query alone will
    # happily eat it. Sequentially, the MCP query never got a page into a single
    # app, so mcp came back null every time and it looked like a model problem
    # when it was a queue problem.
    per_query = [
        first_party_hits(app, "API authentication"),
        first_party_hits(app, "developer pricing API access free tier"),
        tools.search("%s %s MCP server"
                     % (app["name"], subject.disambiguator(app)), limit=3),
    ]

    for rank in range(3):
        for hits in per_query:
            if rank < len(hits):
                add(hits[rank]["url"])

    if not urls:
        add(app["website"])

    # Deliberately over the page budget. The subject check in research_app is
    # about to drop the pages that are not about this app, and the budget should
    # be spent on six pages that survive, not six that included three imposters.
    return urls[:config.MAX_PAGES_PER_APP * 2]


def fetch_pages(urls):
    pages = []
    for u in urls:
        p = tools.scrape(u)
        if p and p.get("text"):
            pages.append(p)
    return pages


def fetch_kept_pages(app, urls, limit=None):
    """Fetch candidates and keep the ones that are about this app.

    The subject check used to run only in Loop E, after extraction, where all
    it could do was null a field the model had already been talked into. Here
    it runs first, so the wrong product's page never reaches the model. Loop E
    stays as the safety net and should now find almost nothing.

    Every candidate is fetched before any is judged. Judging as pages arrive
    looks cheaper and is wrong: one CloudFerro page reads as Sherlock and the
    next does not, and a verdict has to be able to condemn a host after a page
    from it has already been accepted.

    Returns (kept, dropped, tried).
    """
    limit = limit or config.MAX_PAGES_PER_APP
    fetched = []
    for u in urls:
        p = tools.scrape(u)
        if p and p.get("text"):
            fetched.append(p)

    kept, dropped, _verdicts = subject.filter_pages(app, fetched)
    return kept[:limit], dropped, len(fetched)


# --- step 4: confidence ----------------------------------------------------

# Confidence starts at 1.0 and only ever goes down. The old version started
# from how many fields were filled, which meant a record that confidently
# filled every field with bad evidence outscored an honest one that left a
# field null. A number that rises as accuracy falls is worse than no number.
NULL_FIELD_COST = 0.10
WEAK_FIELD_COST = 0.15   # deliberately worse than a null: a wrong filled field
                         # costs a reader more than a blank one
SINGLE_SOURCE_COST = 0.10
THIN_FETCH_COST = 0.10


def score_confidence(rec, pages):
    """Mechanical, not vibes, and every deduction is nameable.

    Returns (score, reasons) so the page can show why a row scored what it did
    instead of asking anyone to trust the number.
    """
    score, reasons = 1.0, []

    strong = set(e["field"] for e in rec["evidence"] if e.get("support") == "strong")
    rejected = rec.get("rejected") or {}

    for f in schema.EVIDENCE_REQUIRED:
        weight = schema.FIELD_WEIGHT.get(f, 1.0)
        if rec.get(f) is None:
            # a field the support check overruled costs what a weak field costs,
            # because we did have a claim and it did not hold up
            cost = (WEAK_FIELD_COST if f in rejected else NULL_FIELD_COST) * weight
            why = "was rejected" if f in rejected else "is null"
            score -= cost
            reasons.append("%s %s (-%.2f)" % (f, why, cost))
        elif f not in strong:
            cost = WEAK_FIELD_COST * weight
            score -= cost
            reasons.append("%s rests on weak evidence (-%.2f)" % (f, cost))

    sources = set(e["url"] for e in rec["evidence"])
    if len(sources) <= 1:
        score -= SINGLE_SOURCE_COST
        reasons.append("everything from one source (-%.2f)" % SINGLE_SOURCE_COST)

    if len(pages) < 2:
        score -= THIN_FETCH_COST
        reasons.append("fewer than two pages read (-%.2f)" % THIN_FETCH_COST)

    return round(max(0.0, min(1.0, score)), 2), reasons


# --- the loop --------------------------------------------------------------

def research_app(app_id, force=False, freeze=False, research=False):
    """Research one app and write data/raw/<id>.json. Returns the record.

    freeze is off by default on purpose. CLAUDE.md says data/pass1/ is written
    by the first full run and is the baseline every accuracy number is measured
    against. A one-off `scout research --app 1` while we are still shaking out
    the pipeline must not be allowed to become that baseline, so only
    research_all freezes.
    """
    config.ensure_dirs()
    out = config.RAW / ("%d.json" % app_id)
    if out.exists() and not force:
        # Resuming. The record is already good, so we do not redo the work, but
        # it still has to be frozen: a run that resumes past app 47 must not
        # leave the first 46 missing from the baseline.
        rec = json.loads(out.read_text(encoding="utf-8"))
        if freeze:
            freeze_pass1(rec)
        return rec

    app = get_app(app_id)
    rec = schema.blank_record(app["app_id"], app["name"], app["website"],
                              app["category"])

    urls = candidate_urls(app, research=research)
    pages, off_topic, tried = fetch_kept_pages(app, urls)

    notes = []
    if off_topic:
        notes.append("pre-filter dropped %d page(s) as not about %s: %s"
                     % (len(off_topic), app["name"], ", ".join(off_topic)))
    if not pages:
        notes.append("no pages could be fetched, every field left null")
        rec["confidence"] = 0.0
    else:
        answer = extract.call_llm(extract.build_prompt(app["name"], app["website"], pages))
        if answer is None:
            notes.append("extraction returned nothing, provider %s model %s"
                         % (config.LLM_PROVIDER, config.EXTRACT_MODEL))
        rec, dropped = extract.to_record(rec, answer, pages)
        if dropped:
            notes.append("dropped during extraction: " + "; ".join(dropped))

        # second pass: for anything the first pass could not evidence, pull a
        # shortlist of candidate sentences out of the same cached pages and ask
        # a narrower question. Same support check, no weaker standard of proof.
        fetcher_by_url = dict((p["url"], p.get("source") or "unknown") for p in pages)
        rec, filled = extract.fill_gaps(rec, pages, fetcher_by_url)
        if filled:
            notes.append("retrieval filled: " + "; ".join(filled))
        demoted = subject.demote_unofficial_mcp(rec, app)
        if demoted:
            notes.append(demoted)
        rec["confidence"], reasons = score_confidence(rec, pages)
        if reasons:
            notes.append("confidence: " + "; ".join(reasons))

    # step 5: does Composio already ship a toolkit for this app
    rec["extras"]["composio_toolkit"] = tools.has_toolkit(app["name"])
    if rec["extras"]["composio_toolkit"] is None:
        notes.append("composio registry unreachable, toolkit flag left null")

    notes.append("pages read: %d of %d tried" % (len(pages), tried))
    rec["notes"] = " | ".join(filter(None, [rec.get("notes")] + notes))

    schema.validate_or_raise(rec)
    # pin the pages this run actually read, so reruns read the same set
    if pages:
        save_pinned(app_id, [p["url"] for p in pages])
    out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    if freeze:
        freeze_pass1(rec)
    return rec


def freeze_pass1(rec):
    """data/pass1/ is the baseline for the accuracy comparison. It is written
    once per app and never touched again. If the file exists, we leave it
    alone, no matter what the new record says."""
    p = config.PASS1 / ("%d.json" % rec["app_id"])
    if p.exists():
        return False
    p.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


_print_lock = threading.Lock()


def research_all(force=False, freeze=True, workers=6, research=False):
    """The full run. This is the one that writes the frozen baseline.

    Apps are independent, so they run in parallel. The rate limiters in
    ratelimit.py are module level and shared, so eight workers still make one
    global stream of requests rather than eight competing ones.
    """
    if tools.composio() is None:
        raise SystemExit(
            "refusing to run --all without COMPOSIO_API_KEY.\n"
            "The HTTP fallback exists for offline testing of a single app, not for\n"
            "producing the dataset. Set the key in .env and try again."
        )

    apps = load_apps()
    results, failures = [], []

    def one(app):
        try:
            rec = research_app(app["app_id"], force=force, freeze=freeze,
                               research=research)
            with _print_lock:
                print("ok   %3d %-28s conf %.2f  %s"
                      % (app["app_id"], app["name"][:28], rec["confidence"],
                         rec["stage"] or "stage unknown"))
            return rec
        except Exception as exc:  # noqa: BLE001 - one bad app must not stop 99
            with _print_lock:
                print("FAIL %3d %-28s %s: %s"
                      % (app["app_id"], app["name"][:28], type(exc).__name__, exc))
            failures.append((app["app_id"], app["name"], str(exc)))
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(one, apps):
            if rec is not None:
                results.append(rec)

    print("\ndone: %d ok, %d failed" % (len(results), len(failures)))
    for app_id, name, err in failures:
        print("  failed: %d %s: %s" % (app_id, name, err[:120]))
    return results
