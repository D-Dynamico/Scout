"""Loop D sample selection and scoring.

The agent picks which 20 apps a human should check, and writes a blank template
for them to fill. It never writes data/human_sample.json. That file is the only
thing in this repo the agent is not allowed to touch, because it is the ruler,
and a ruler you drew yourself measures nothing.
"""

import json
import random

from . import config, schema

SEED = 20260916

# The ten hard ones, named in CLAUDE.md. Obscure apps, apps with no public API,
# and apps where the answer is a partnership conversation rather than a doc.
HARD_NAMES = [
    "Sherlock", "Mermaid CLI", "Paygent Connect", "iPayX", "fanbasis",
    "Waterfall.io", "NotebookLM", "Consensus", "PitchBook", "DealCloud",
]

# Ordered by how much each field decides the stage verdict. If a checker runs
# out of time, the first four are the ones that matter.
CHECK_FIELDS = ["access", "auth", "api_type", "prod_requires_review",
                "mcp", "api_breadth", "stage"]


def choose(apps):
    """Twenty apps in two halves. The hard half shows we did not cherry-pick.
    The random half shows what a typical row is worth. Reported separately,
    because a blended number hides both."""
    by_name = dict((a["name"], a) for a in apps)

    hard = []
    for name in HARD_NAMES:
        if name in by_name:
            hard.append(by_name[name])
        else:
            raise KeyError("hard sample app %r is not in apps.csv" % name)

    hard_ids = set(a["app_id"] for a in hard)
    rng = random.Random(SEED)
    random_half = []
    for category in schema.CATEGORIES:
        pool = sorted([a for a in apps
                       if a["category"] == category and a["app_id"] not in hard_ids],
                      key=lambda a: a["app_id"])
        if pool:
            random_half.append(rng.choice(pool))

    return hard, random_half


def write_plan(apps):
    hard, random_half = choose(apps)
    plan = {
        "seed": SEED,
        "why": "Ten named hard apps, plus one seeded draw per category from the "
               "remaining ninety. Accuracy is reported for each half separately.",
        "hard": [{"app_id": a["app_id"], "name": a["name"],
                  "category": a["category"]} for a in hard],
        "random": [{"app_id": a["app_id"], "name": a["name"],
                    "category": a["category"]} for a in random_half],
    }
    (config.DATA / "sample_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    # A blank template for the human to fill in by hand. Deliberately not
    # data/human_sample.json, and deliberately empty: prefilling it with our
    # own answers would invite agreement rather than checking.
    rows = []
    for half, group in (("hard", hard), ("random", random_half)):
        for a in group:
            row = {
                "app_id": a["app_id"],
                "name": a["name"],
                "category": a["category"],
                "half": half,
                "checked_by": "",
                # one line when the agent got it wrong: what it said, what is
                # true, and your best guess why. This becomes the misses log.
                "notes": "",
            }
            for f in CHECK_FIELDS:
                row[f] = None
                # "_source", not "_url": the schema already has a real
                # mcp_url field and two different meanings for one key is how
                # a spreadsheet quietly lies to you
                row[f + "_source"] = ""
            rows.append(row)
    (config.DATA / "human_sample.template.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    return plan


# --- scoring ---------------------------------------------------------------

def score():
    """Compare pass1 and verified against the human sample, per field and per
    half, and write accuracy.json plus misses.json."""
    if not config.HUMAN_SAMPLE.exists():
        raise FileNotFoundError(
            "data/human_sample.json does not exist yet.\n"
            "Run `scout sample` to get the list of 20 apps and a blank template,\n"
            "fill it in by hand against real docs, and save it as human_sample.json.\n"
            "The agent does not write that file."
        )

    human = json.loads(config.HUMAN_SAMPLE.read_text(encoding="utf-8"))

    def load(folder, app_id):
        p = folder / ("%d.json" % app_id)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    accuracy = {"sample_size": len(human), "by_half": {}, "by_field": {}}
    misses = []

    for half in ("hard", "random"):
        rows = [h for h in human if h.get("half") == half]
        if not rows:
            continue
        half_stats = {}
        for field in CHECK_FIELDS:
            counts = {"pass1": [0, 0], "verified": [0, 0]}
            for h in rows:
                truth = h.get(field)
                if truth is None:
                    continue  # the human did not check this field
                for which, folder in (("pass1", config.PASS1),
                                      ("verified", config.VERIFIED)):
                    rec = load(folder, h["app_id"])
                    if rec is None:
                        continue
                    got = rec.get(field)
                    counts[which][1] += 1
                    if got == truth:
                        counts[which][0] += 1
                    else:
                        misses.append({
                            "app_id": h["app_id"], "name": h["name"],
                            "half": half, "field": field, "which": which,
                            "expected": truth, "got": got,
                            "confidence": rec.get("confidence"),
                            "caught_by": _caught_by(rec, field),
                        })
            half_stats[field] = {
                "pass1": _pct(counts["pass1"]),
                "verified": _pct(counts["verified"]),
                "n": counts["pass1"][1],
            }
        accuracy["by_half"][half] = half_stats

    for field in CHECK_FIELDS:
        tot = {"pass1": [0, 0], "verified": [0, 0]}
        for half_stats in accuracy["by_half"].values():
            s = half_stats.get(field)
            if not s:
                continue
            for which in ("pass1", "verified"):
                if s[which] is not None:
                    tot[which][0] += round(s[which] * s["n"] / 100.0)
                    tot[which][1] += s["n"]
        accuracy["by_field"][field] = {
            "pass1": _pct(tot["pass1"]), "verified": _pct(tot["verified"]),
            "n": tot["pass1"][1],
        }

    (config.DATA / "accuracy.json").write_text(
        json.dumps(accuracy, indent=2), encoding="utf-8")
    (config.DATA / "misses.json").write_text(
        json.dumps(misses, indent=2, ensure_ascii=False), encoding="utf-8")
    return accuracy, misses


def _pct(pair):
    hit, total = pair
    return round(100.0 * hit / total, 1) if total else None


def _caught_by(rec, field):
    """Which loop, if any, already flagged this field. A miss the pipeline
    knew about is a different kind of miss from one it never saw."""
    notes = rec.get("notes") or ""
    if (rec.get("rejected") or {}).get(field):
        return "support check"
    for label, marker in (("loop A", "loop A"), ("loop B", "loop B"),
                          ("loop C", "loop C browser check")):
        if marker in notes and field in notes.split(marker)[-1][:300]:
            return label
    return "human only"
