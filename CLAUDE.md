# Scout

Agent-driven research of 100 apps for the Composio AI Product Ops Intern take-home. For each app: auth, access gating, API surface, MCP availability, buildability stage, evidence. Then patterns, verification, and a single self-explanatory HTML page.

Time budget: 8 hours total. Submit early. Result and clarity matter, not hours.

## What the reviewer is grading

1. Patterns up top, plainly stated, with numbers.
2. A trustworthy, skimmable table of 100 that reads like a connector status tracker.
3. What the agent does and where a human was still needed.
4. Verification: accuracy on a human-checked sample, before and after the verification loops, misses shown honestly.
5. Proof: repo, one-command run, raw JSON an agent can consume.

Honesty rule: if the agent got something wrong or an app defeated us, it goes on the page. A gated app with evidence is a correct finding, not a failure.

## Non-negotiable rules

- `data/pass1/` is the first full run of the finished pipeline, written once and never modified. It is the baseline for the accuracy comparison.
- `data/pass0/` is the scaffold-era run, kept on purpose. That version checked that a quoted snippet really appeared on the page but never checked that the snippet supported the claim, so it produced confident wrong rows. It is not a baseline, it is the before picture for the verification story on the page.
- No claim without evidence. Every non-null field needs a source URL and a quoted snippet from that page. If the extractor cannot find a snippet, the field is null and confidence drops.
- The pipeline is resumable. One JSON file per app in `data/raw/`. A crash at app 47 costs nothing.
- Cache every fetched page in `cache/` keyed by URL. Never re-fetch on rerun. A failed fetch is
  recorded but never served back as a hit, otherwise one bad afternoon poisons the cache forever.
- Pin the page set per app in `data/urls/<id>.json` once a run succeeds. Search is not
  reproducible, so an unpinned pipeline quietly produces a different dataset every run.
- Verification loops write to `data/verified/`, never over `data/pass1/`.
- Human-checked rows live in `data/human_sample.json` and are edited by hand only. The agent never writes there. The sample is complete as of 2026-09-17; it is the ruler the pipeline is measured against, so the agent does not touch it again, not even to fix a typo or fill a null.
- Composio is used for tools (search, scrape, browser, toolkit registry over MCP). Orchestration is plain Python we own. Do not build the loop inside their agent framework.
- Prose on the page and in the README: plain, conversational, no em dashes.

## Schema (fixed enums, do not add values without updating this file)

```
app_id            int, 1 to 100
name              str
website           str
category          one of the 10 given categories
one_liner         str, what it does in one line

auth              OAuth2 | API key | Basic | Bearer | custom | none
auth_notes        str, e.g. OAuth scopes model, key location

access            free self-serve | free trial | paid plan | admin approval | partner or contact-sales | no public API
access_notes      str

api_type          REST | GraphQL | SDK-only | CLI-only | none
api_breadth       small (<30 endpoints) | medium (30 to 150) | large (>150) | unknown
api_docs_url      str

mcp               official | community | none
mcp_url           str or null

stage             ready to build | needs sandbox | needs OAuth app review | needs partnership outreach | no viable API
blocker           str, one phrase, or null

prod_requires_review  bool or null
                  Can you build today but not ship to other people's accounts without review?
                  Slack, Meta, Google, and Salesforce are all self-serve for development and
                  reviewed for production. This is the distinction that matters most for a
                  connector catalogue, so it gets its own column.

gate              object or null, required when the app is gated:
  application_url str
  process         str, what the review or partnership involves
  contact         str, email, form, or "unknown"
  est_time        str or "unknown"

extras
  sdks            list of str
  webhooks        bool or null
  rate_limits     str or null
  composio_toolkit bool, does Composio already ship a toolkit for this app

rejected          object or null, keyed by field name:
  <field>         {value, reason}
                  A claim the extractor made and the support check overruled. The field
                  itself is null, but we keep what was claimed and why it was rejected.
                  A null with a rejection attached is more useful than a bare null, and
                  counting these gives the verification section a real number: how often
                  the support check overruled the extractor.

evidence          list of {field, url, snippet, fetcher, support, subject}
                  fetcher is the tool that got the page, e.g. COMPOSIO_SEARCH_FETCH_URL_CONTENT
                  or http, so the page can say how every claim was obtained.
                  support is strong | weak. Strong means a second model call confirmed the
                  snippet supports that exact claim. Weak means it passed the term filter but
                  not the support check, and it drags confidence down.
confidence        float 0 to 1
notes             str, anything odd, including where the agent struggled
```

Stage is derived from access and api_type, not guessed independently. Rules:
- no public API or api_type none/CLI-only with no programmatic surface: no viable API
- partner or contact-sales: needs partnership outreach
- admin approval: needs OAuth app review
  An app that is self-serve for development but needs review before production does not
  change stage. It stays buildable and records the review in `prod_requires_review`. That
  distinction is the whole point of the column.
- paid plan or free trial with no free tier for credentials: needs sandbox
- free self-serve with documented REST or GraphQL: ready to build

Gated, defined operationally: a developer cannot get working credentials on their own, in
under an hour, without paying or waiting on a human. So `paid plan`, `admin approval`, and
`partner or contact-sales` are gated and need a `gate` object. `free self-serve` and
`free trial` are not gated, though trial is flagged in the table. Gating is a property of
`access`, not of `stage`, because an app can be ungated for development and still need
review before production, which is what `prod_requires_review` records.

## Pipeline

`scout research --app <id>` or `scout research --all`

1. Search: docs hint from the app list, plus "<app> API authentication", "<app> developer pricing", "<app> MCP server".
   Search results vary between runs, so the page set is pinned. Once an app's run succeeds,
   the URLs actually read are written to `data/urls/<id>.json` and reruns read that list
   instead of searching again. `--research` forces a fresh search, and new URLs are merged
   into the pinned set rather than replacing it, so a page that search surfaced once is
   never lost because search did not surface it the second time.
2. Fetch: 3 to 6 pages via Composio tools (Firecrawl or Exa for docs, browser tool when the page needs JS). Cache.
3. Extract: one LLM call over all fetched pages, output strictly the schema above, snippet per claim.
3b. Retrieve: for any of `access`, `prod_requires_review`, `mcp`, `api_breadth` the first
   call left unevidenced, score sentences from the same cached pages by that field's
   keywords, hand the model the top twelve, and ask one narrow question. Finding one
   sentence in 70,000 characters is a needle hunt; picking from twelve is a question a
   small model can answer. Retrieved claims clear the same support check as any other,
   so this adds coverage without adding a weaker standard of proof.
4. Check evidence, in three stages, cheapest first:
   a. Provenance. The snippet must appear verbatim in the cached page it cites, or the field is nulled.
   b. Terms. The snippet must contain language relevant to the field it backs. An `mcp` claim
      whose snippet never says MCP or Model Context Protocol is not evidence of anything.
   c. Support. One small model call per surviving claim, given only the claim and the snippet:
      does this snippet support this exact claim, yes or no. This is what turns the evidence
      check from provenance into support, and it catches "we offer a free trial" being used to
      back `free self-serve`. Pass marks the item strong. An explicit no nulls the field
      and records it under `rejected`. A call that fails or does not answer marks the item
      weak, because a check that did not run is not a check that passed.
      URL fields are exempt: `api_docs_url`, `mcp_url`, and `gate.application_url` are
      evidenced by the page being what it claims to be, which is provenance, not support.
      Loop B confirms those resolve. They never take a support penalty.
5. Score: confidence starts at 1.0 and only ever goes down. Subtract per null claim field,
   subtract more per field surviving on a weak snippet. Filling a field never raises the score.
   Fields are weighted by how much they decide the stage verdict, so adding an eighth field
   never dilutes the penalties on the four that matter. Full weight: `access`, `auth`,
   `api_type`, `prod_requires_review`. Partial: `api_breadth`, `mcp`, `api_docs_url`.
6. Registry: query Composio toolkit list over MCP for `composio_toolkit`.
7. Write `data/raw/<id>.json`. On the first full run of the finished pipeline, also copy to `data/pass1/`.

## Verification loops

`scout verify --all`

- Loop A, self-consistency: second extraction with a different prompt (or model) on the same cached pages. Disagreements flagged in `notes`, not silently resolved.
- Loop B, evidence check: every evidence URL must resolve and the snippet must appear on the page. Failures null the field and lower confidence.
- Loop C, browser check: for confidence < 0.7 or any Loop A disagreement, load the developer signup or pricing page with the browser tool and confirm `access`.
- Loop E, subject check: none of the other loops asks whether a page is about the right
  *app*. Two layers. First the domain check, free: the app's own site and docs domain are
  known from apps.csv, so anything else is third party. Third party is not disqualifying,
  MCP registries and GitHub are legitimate sources for Sherlock and Mermaid CLI, so those
  pages are marked rather than dropped. Second, one small call per third-party page: is
  this page primarily about this app, yes or no. A page that fails both is removed from
  that app's evidence and the field is re-derived from what survives.
- Loop D, human sample: 20 apps in two halves of 10, hand-checked against real docs into `data/human_sample.json`.
  - Hard half, named: Sherlock, Mermaid CLI, Paygent, iPayX, fanbasis, Waterfall.io, NotebookLM, Consensus, PitchBook, DealCloud.
  - Random half: a seeded draw, one per category, from the remaining 90. Seed recorded in the file so the draw is reproducible.
  - Accuracy is reported for each half separately, never only blended. The hard half shows we did not cherry-pick. The random half shows what a typical row is worth. A blended number hides both.

`scout score` compares `data/pass1/` and `data/verified/` against the human sample, per field and per sample half, and writes `data/accuracy.json` plus `data/misses.json` (what was wrong, why, which loop caught it, or "human only").

## Patterns

`scout analyze` writes `data/patterns.json`:
- auth by category
- access by category
- stage counts overall and by category
- MCP coverage by category
- blocker frequency
- 2x2: ease of build (stage) vs worth building (well-known, API breadth)
- outreach queue: gated apps grouped by gate type

Headline claims: 5 to 7, each with a number. Written by hand after looking at the crosstabs, not generated blindly.

## Page

`scout build-page` renders `site/index.html`, single static file, order top to bottom:

1. Headline patterns with numbers
2. 2x2 plus two or three charts (auth split, access by category, stage counts)
3. Filterable, sortable table of 100: stage column, confidence colour, evidence links
4. Agent: manual playbook on the left, what the agent owns on the right, what still needs a human
5. Verification: sample size, per-field accuracy before and after, misses table
6. Outreach queue plus three short email templates (OAuth app review, partnership, enterprise sales)
7. Proof: repo link, run command, download of `data/verified.json`

Deploy: Vercel or GitHub Pages.

## Repo layout

```
scout/          pipeline code
data/           pass0/, pass1/, raw/, verified/, urls/, human_sample.json, accuracy.json, misses.json, patterns.json
cache/          fetched pages, gitignored
site/           index.html and assets
apps.csv        the 100 apps with category and docs hint
CLAUDE.md
README.md       setup, run, verify, analyze, build-page
```

## Cut order if time runs short

1. Live run trigger on the page
2. Extras columns (rate limits, webhooks)
3. Email templates

Never cut the verification section or the misses log.
