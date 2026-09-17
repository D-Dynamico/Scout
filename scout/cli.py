"""scout <command>. Thin argparse over the pipeline."""

import argparse
import json
import sys

from . import analyze, config, extract, pipeline, sample, tools, verify


def cmd_research(args):
    config.load_dotenv()
    if args.app:
        rec = pipeline.research_app(args.app, force=args.force,
                                    research=args.research)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    elif args.all:
        pipeline.research_all(force=args.force, workers=args.workers,
                              research=args.research)
    else:
        print("pass --app <id> or --all")
        return 1
    return 0


def cmd_verify(args):
    config.load_dotenv()
    if args.app:
        rec, dis, fails = verify.verify_app(args.app, browser=args.browser)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        print("\nloop A disagreements: %d\nloop B failures: %d"
              % (len(dis), len(fails)))
    elif args.subject:
        verify.subject_pass(workers=args.workers)
    elif args.browser_only:
        verify.browser_pass(limit=args.browser_limit)
    elif args.all:
        verify.verify_all(workers=args.workers, browser=args.browser,
                          browser_limit=args.browser_limit)
    else:
        print("pass --app <id> or --all")
        return 1
    return 0


def cmd_sample(args):
    config.load_dotenv()
    plan = sample.write_plan(pipeline.load_apps())
    print("hard half:")
    for a in plan["hard"]:
        print("  %3d %s" % (a["app_id"], a["name"]))
    print("random half, seed %d:" % plan["seed"])
    for a in plan["random"]:
        print("  %3d %-24s %s" % (a["app_id"], a["name"], a["category"]))
    print("\nwrote data/sample_plan.json and data/human_sample.template.json")
    print("fill the template in by hand and save it as data/human_sample.json")
    return 0


def cmd_score(args):
    config.load_dotenv()
    try:
        accuracy, misses = sample.score()
    except FileNotFoundError as exc:
        print(exc)
        return 1
    print(json.dumps(accuracy, indent=2))
    print("\n%d misses written to data/misses.json" % len(misses))
    return 0


def cmd_analyze(args):
    config.load_dotenv()
    p = analyze.analyze()
    print(json.dumps({k: v for k, v in p.items()
                      if k in ("n", "stage", "access", "auth", "mcp", "quadrant",
                               "unresolved", "composio_toolkit")}, indent=2))
    print("wrote data/patterns.json")
    return 0


def cmd_build_page(args):
    config.load_dotenv()
    from . import page
    out = page.build()
    print("wrote %s" % out)
    return 0


def cmd_probe(args):
    """Check what we actually have, for tools and for the model. No
    assumptions, just the answer from each API."""
    config.load_dotenv()
    print(json.dumps({
        "composio": tools.probe(),
        "llm": extract.probe_llm(),
    }, indent=2))
    return 0


def cmd_todo(args):
    print("not built yet: %s" % args.which)
    return 1


def main(argv=None):
    # Windows consoles default to cp1252 and blow up on characters that are
    # ordinary in docs pages, like a non-breaking hyphen. The records are
    # written as UTF-8 either way, this just stops printing from crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(prog="scout")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("research", help="research one app or all 100")
    r.add_argument("--app", type=int, help="app_id from apps.csv")
    r.add_argument("--all", action="store_true")
    r.add_argument("--force", action="store_true",
                   help="redo an app even if data/raw already has it")
    r.add_argument("--workers", type=int, default=6,
                   help="parallel apps for --all, default 6")
    r.add_argument("--research", action="store_true",
                   help="search again even if this app has a pinned URL set, "
                        "and merge anything new into the pin")
    r.set_defaults(func=cmd_research)

    pr = sub.add_parser("probe", help="report which Composio tools we have")
    pr.set_defaults(func=cmd_probe)

    v = sub.add_parser("verify", help="run the verification loops")
    v.add_argument("--app", type=int)
    v.add_argument("--all", action="store_true")
    v.add_argument("--workers", type=int, default=6)
    v.add_argument("--browser", action="store_true",
                   help="also run Loop C, the browser check, on low confidence rows")
    v.add_argument("--subject", action="store_true",
                   help="run only Loop E, the subject check, over verified rows")
    v.add_argument("--browser-only", action="store_true",
                   help="run only Loop C over already verified rows")
    v.add_argument("--browser-limit", type=int, default=15,
                   help="cap on Loop C browser runs, they are slow")
    v.set_defaults(func=cmd_verify)

    sa = sub.add_parser("sample", help="pick the 20 apps a human should check")
    sa.set_defaults(func=cmd_sample)

    sc = sub.add_parser("score", help="accuracy against the human sample")
    sc.set_defaults(func=cmd_score)

    an = sub.add_parser("analyze", help="crosstabs into data/patterns.json")
    an.set_defaults(func=cmd_analyze)

    bp = sub.add_parser("build-page", help="render site/index.html")
    bp.set_defaults(func=cmd_build_page)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
