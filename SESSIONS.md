# Session log

What was built, what was decided, and why. Written as we went, so the interview
answer to "why is it like that" is in here rather than reconstructed later.

## Session 1, 2026-09-16

Scaffold, tool layer, schema, and the evidence checking that makes the dataset
worth anything. No full run yet, and the reason is at the bottom.

---

### Scaffold and orchestration

**Plain Python owns the loop, Composio provides tools.** Straight from CLAUDE.md.
The pipeline is `search -> fetch -> extract -> check -> score -> registry -> write`,
one app at a time, one JSON file per app. Nothing runs inside anyone's agent
framework, so every step is inspectable and explainable.

**`pip install -e .` gives a `scout` command.** One command to run, which is one
of the five things the reviewer grades.

**The validator rejects, it never coerces.** A value outside an enum fails the
record and the record does not get written. A silently coerced value is a wrong
claim nobody would notice, which is worse than a crash.

**The 10 categories came from the app list, not from CLAUDE.md.** CLAUDE.md says
"one of the 10 given categories" without listing them. They are now pinned in
`schema.py` and enforced, so a typo cannot quietly create an eleventh category.

**Stage is derived, never guessed, and the rule order matters.** An app can match
several rules at once, so they are checked in the order of what actually blocks
you: no surface, then partnership, then review, then money, then go. CLAUDE.md
did not cover free self-serve plus SDK-only, so that is `ready to build` with
`blocker: "SDK-only surface"` rather than being hidden.

**Not every field needs a snippet.** `stage` is derived by code,
`composio_toolkit` comes from the Composio registry, and the identity fields come
from `apps.csv`. Those are exempt. The seven real claims are not.

---

### What we learned about Composio, by asking it rather than assuming

Everything here was read off the API. Three of five tool slugs I first guessed
were wrong, which is why the code now carries a comment saying these were read
from the API and on what date.

- Real slugs: `COMPOSIO_SEARCH_WEB`, `EXA_SEARCH`, `FIRECRAWL_SEARCH` for search,
  `COMPOSIO_SEARCH_FETCH_URL_CONTENT`, `FIRECRAWL_SCRAPE`, `EXA_GET_CONTENTS_ACTION`
  for scrape, `BROWSER_TOOL_CREATE_TASK` and `BROWSER_TOOL_WATCH_TASK` for Loop C.
- **The first API key could read but not execute.** 403, `tool_execution` write
  access missing. Worth knowing that read scope and execute scope are separate.
- **Manual execution rejects `latest` and demands a concrete toolkit version.**
  `toolkit_version()` looks it up once per toolkit and caches it. Without this,
  every call fails even with a correct key.
- **Firecrawl and Exa need connected accounts**, and there are none, so they 404
  with `ActionExecute_ConnectedAccountNotFound`. `composio_search` is
  Composio-managed and works with no setup, so it is first in the list. If
  Firecrawl gets connected it is worth promoting for quality.
- The registry holds **1,543 toolkits**. An early probe printed 100 and that was
  just the page size, not the total. `has_toolkit` paginates properly.
- Toolkit items also carry `auth_schemes` and `meta.tools_count`. That is
  Composio's own view of how an app authenticates, which is a free cross-check
  against our extraction from a source that is not the vendor's docs. Not wired
  up yet, noted so it does not get forgotten.

**Composio's fetcher beats plain HTTP on exactly the pages that matter.**
Salesforce, Meta, and Otter all return 403 or 400 to a bare Python request and
come back fine through Composio.

---

### Model choice

**Groq, `openai/gpt-oss-20b`,** chosen on cost. The provider layer takes
`groq`, `anthropic`, or `claude-cli`, because Loop A wants a second extraction
from a different model and swapping providers is the cleanest way to get one.
`claude-cli` shells out to the Claude Code CLI and runs on a subscription rather
than an API key, which is a real option if per-token cost ever bites.

Model IDs are verified against the account rather than trusted from `.env`.
`scout probe` reports whether the configured model actually exists.

---

### The thing this session was actually about

**Version one checked provenance but not relevance, and it produced a confident
wrong row.** It verified that every quoted snippet really appeared on the page it
cited. It never checked that the snippet supported the claim. So Salesforce came
back with `mcp: "official"` backed by the snippet "REST API Developer Guide |
Salesforce Developers", which is real, verbatim, on the cited page, and says
nothing whatsoever about MCP.

**Worse, it scored that row 1.0.** The old confidence formula started from how
many fields were filled, so a record that confidently filled every field with bad
evidence outscored an earlier, more honest one that scored 0.57 and correctly left
`access` null. A number that rises as accuracy falls is worse than no number.

That run is kept as `data/pass0/` on purpose. It is not a baseline, it is the
before picture for the verification section.

**What replaced it, three gates, cheapest first:**

1. **Provenance.** The snippet must be on the page it cites.
2. **Terms.** The snippet must use the language of the claim. An `mcp` claim whose
   snippet never says MCP is not evidence of anything.
3. **Support.** One small isolated model call per claim: given only the claim and
   the snippet, does this snippet support this exact claim. The judge does not see
   the page, the other claims, or the record, so it cannot talk itself into
   agreeing with the extraction it just produced.

**An explicit no nulls the field and records why.** `rejected: {field: {value, reason}}`
keeps what was claimed and what happened to it. A null with a rejection attached is
more useful than a bare null, and counting them gives the verification section a
real number: how often the support check overruled the extractor. A call that
fails or does not answer marks the evidence weak instead, because a check that did
not run is not a check that passed.

**URL fields skip the support check.** Asking a model whether a sentence supports
the claim that the docs live at a URL is close to meaningless. `api_docs_url`,
`mcp_url`, and `gate.application_url` are evidenced by the page being what it
claims to be, which is provenance, and Loop B confirms they resolve.

**Confidence starts at 1.0 and only goes down.** Weak evidence costs more than a
null, deliberately: a wrong filled field costs a reader more than a blank one.
Fields are weighted by how much they decide the stage verdict, so `access`, `auth`,
`api_type`, and `prod_requires_review` cost full freight while `api_breadth`,
`mcp`, and `api_docs_url` cost less. Weighting also means adding an eighth field
later cannot dilute the penalties on the four that matter. Every deduction is
recorded by name in `notes`, so a row can show its own arithmetic.

**`prod_requires_review` earned its own column.** Gated is defined operationally:
a developer cannot get working credentials alone, in under an hour, without paying
or waiting on a human. But many apps are self-serve to build on and reviewed
before production. Slack, Meta, Google, and Salesforce all work this way. That
does not change `stage`, it gets its own boolean, because "buildable today, but
shipping it needs app review" is the distinction that matters most for a connector
catalogue. Gating hangs off `access`, not `stage`, for the same reason.

---

### Bugs found and fixed, in the order they bit

1. **The cache served failures as hits.** A 403 with empty text from an early dry
   run became a permanent cache entry, so later runs skipped the page instead of
   retrying with a tool that by then worked. Failed fetches are recorded but never
   served back. One bad afternoon should not poison the cache forever.
2. **The CLI crashed printing a record.** Windows consoles default to cp1252 and a
   Salesforce page contained a non-breaking hyphen. Output is forced to UTF-8.
3. **A single-app test run was freezing the baseline.** `pass1` is now only written
   by a full run, so shaking out the pipeline cannot become the thing every
   accuracy number is measured against.
4. **Search results are not reproducible.** The same query returns a different set
   next week, so an unpinned pipeline quietly produces a different dataset every
   run. A real Salesforce finding, that creating connected apps is restricted as of
   Spring 2026, appeared in one run and vanished in the next. Page sets are now
   pinned per app in `data/urls/<id>.json`, reruns read the pin instead of
   searching, `--research` forces a fresh search, and new URLs are **merged** into
   the pin rather than replacing it, so a page that search surfaced once is never
   lost. This goes on the page in one sentence, because it is true of every
   research agent and most people never notice.
5. **URL selection starved two of the three questions.** The page budget is six.
   Taking the docs hint plus all three results from the auth query first meant the
   MCP query never got a page into a single app, so `mcp` came back null every
   time and it looked like a model problem when it was a queue problem. Now the
   three searches are taken round robin.
6. **Small models paraphrase, and strict matching threw away correct claims.**
   Instead of loosening the rule, a paraphrased snippet is snapped to the closest
   real sentence on the page and that sentence is what gets stored. The record
   still quotes the page verbatim, because the stored text comes from the page and
   not from the model. Nothing close enough on any page is still a rejection.
7. **My own term filter was rejecting good evidence.** "You can view and manage
   your API keys in the Stripe Dashboard" is a perfectly good reason to believe an
   app is self-serve, and the first term list rejected it for not containing the
   word "free". The list is now deliberately broad, because it is a filter for
   egregious mismatches, not the real gate. The support check is the real gate.
8. **The judge was answering the wrong question.** Shown `access = free self-serve`
   it demanded proof of price and rejected "You can create a personal access token
   in your developer settings", which is exactly the evidence we want. It now
   judges the operational conclusion, in words, rather than matching an enum label.
   That took it from 4/7 to 7/7 on a controlled set that includes the trap case:
   a 14-day trial does not support free self-serve.

---

### The retrieval step

**The problem was never the model's judgment, it was the haystack.** Asking a
small model to find one sentence about pricing inside six pages of documentation
is a needle hunt in 70,000 characters. So for the four fields the first pass
could not evidence, we now score sentences from the same cached pages by that
field's keywords, hand the model the top twelve, and ask one narrow question.
Picking from twelve is a question a 20b model can answer.

Candidates are ranked by keyword hits, with decisive phrases like "no credit
card" or "contact sales" weighted triple, because raw hit counting ranked a
sentence stuffed with generic words above the one that actually settles it.

**Retrieval disagreeing with the support check is treated as a disagreement, not
a rejection.** This is a deliberate difference from the first pass. There, the
model picks its own snippet out of everything, so an explicit no means it grabbed
something arbitrary and the field is nulled. In retrieval the sentence came from
a keyword shortlist and the model had to point at one of twelve, so a no is a
judgment call at the boundary rather than evidence of fabrication. The claim
stands, the evidence is marked weak, confidence takes the hit, and the
disagreement is written into `notes`. CLAUDE.md already says disagreements get
flagged rather than silently resolved, which is exactly this situation.

The case that forced the decision: Stripe, the sentence "In both test mode and
live mode, you can create as many restricted API keys as you need." Retrieval
called that free self-serve. The support check, shown it in isolation, said no
three times out of three. Both readings are defensible, and `free self-serve` is
a claim about the absence of barriers, which one sentence rarely proves outright.

**`api_breadth` now defaults to `unknown` rather than null.** "unknown" asserts
nothing about the world, it records that the pages did not say, so it needs no
evidence. It also reads better in the table than a blank cell, which looks like
we forgot to look.

### Where it stands, honestly

On a six app pilot, Salesforce, Slack, Shopify, GitHub, Stripe, Mermaid CLI:

| field | before retrieval | after retrieval |
| --- | --- | --- |
| access | **0/6** | **6/6** |
| api_breadth | 0/6 | 6/6 |
| stage set | **0/6** | **5/6** |
| mcp | 3/6 | 5/6 |
| auth | 5/6 | 5/6 |
| api_type | 5/6 | 5/6 |
| api_docs_url | 5/6 | 5/6 |
| prod_requires_review | 0/6 | 2/6 |

Both columns are `openai/gpt-oss-20b`. 120b was tested and was not better, which
is worth stating because the instinct is to spend money on the problem.

Spot checks on the pilot look right. Mermaid CLI lands on `no viable API` via
CLI-only, which is one of the hard rows. GitHub and Stripe are `ready to build`.
Shopify is `admin approval` and `needs OAuth app review`.

**Two things still to be honest about.**

`prod_requires_review` is 2/6. It fills in where the answer is yes and stays null
otherwise, which is defensible, since most pages simply do not discuss production
review. But it means the column is sparse.

**Runs are not deterministic, even at temperature 0.** Slack came back
`admin approval` in one run and `free self-serve` in the next, off identical
cached pages. Groq does not guarantee determinism, so the same input can produce
a different row. Page pinning fixed the input drift; this is output drift and it
is a real limitation. It is also the strongest argument for Loop A, which exists
to catch exactly this by extracting twice and flagging the disagreement.

---

## Session 2, 2026-09-17

pass1 frozen, retrieval extended, verification loops built and run.

### The baseline is frozen

100 records, all schema valid, in `data/pass1/`. One app failed validation on the
first attempt: Gladly claimed an MCP server with no URL. Fixed at the source
rather than by hand editing the record. An mcp claim now backfills its URL from
the page its evidence came from, and if there is no evidence either then the
claim does not survive.

A resume bug surfaced mid run. `research_app` returned early for apps already in
`data/raw/` and skipped the freeze, so a run that resumed past app 47 would have
left the first 46 out of the baseline entirely.

### Retrieval extended to api_type and auth

`api_type` was the real bottleneck, not `access`. Stage derives from both, and at
62/100 `api_type` was capping stage at 53/100. Adding both fields to the retrieval
pass was the single biggest lift of the session.

### Verification, and what each loop is actually worth

| | baseline | verified |
| --- | --- | --- |
| stage determined | 53 | **82** |
| api_type | 62 | 94 |
| auth | 73 | 100 |
| access | 87 | 86 |
| median confidence | 0.69 | 0.78 |

**Loop A earns its place.** 135 field level disagreements across 86 of 100 rows,
using a different prompt and a different model on the same cached pages. Nothing
is silently resolved; every disagreement is written into `notes`.

**Loop B, as built, is close to worthless and that needs saying.** Zero failures
across 100 rows. Not because the evidence is perfect, but because it re-checks
snippets against the same cached pages they were snapped out of, so it can only
ever confirm itself. A real Loop B has to re-fetch the page and see whether the
vendor still says it. The cache rule and the freshness check are in direct
tension, and right now the cache rule wins.

**Loop C was built but not run.** It needs `--browser` and it is capped, because
a browser agent per row is the most expensive check we have.

### The failure the checks cannot see

Every gate we have asks whether a snippet is real, on the page, and about the
right topic. None of them asks whether the page is about the right **app**.

Sherlock is the clearest case. Its six pinned pages cover four different products
called Sherlock: the real one, a CloudFerro service, `sherlocks.ai`, and
`usesherlock.ai`, plus a community MCP wrapper. Its `auth`, `access`, and
`api_type` all came from the wrong ones. The verified row reads "ready to build,
REST, Bearer" when the real Sherlock is a CLI tool with no API. Every check
passed.

Measured across the set: **34 of 100 rows have at least one core claim evidenced
from a host carrying neither the app's name nor its own domain.** Some of those
are legitimate, `developers.facebook.com` for WhatsApp Business and `twilio.com`
for SendGrid are the right sources. Many are not: zoominfo, revenueflow,
salestools.club, apis.io, getmacha.com, unipile.com, blotato.com. Third party
content marketing pages describing someone else's API.

A domain match cannot separate these. `docs.slack.dev` and `pipedrive.readme.io`
are correct and off domain; `pipeline.zoominfo.com` for HubSpot is wrong and off
domain. The fix is a subject gate at the page level: one small call per page
asking whether the page is primarily about this app, run before extraction, so a
contaminated page never enters the evidence pool.

### Loop D sample

`scout sample` writes `data/sample_plan.json` and a blank
`data/human_sample.template.json`. The ten named hard apps, plus a seeded draw of
one per category from the remaining ninety, seed 20260916. The template is
deliberately blank. Prefilling it with our own answers would invite agreement
instead of checking, and `data/human_sample.json` is the one file the agent is
not allowed to write, because a ruler you drew yourself measures nothing.

---

## Session 3, 2026-09-17

The missing pieces: analyze, the page, the README, and Loop C actually running.

### Loop C works now, and it was worth building

The first run failed on all 12 rows with "did not return a task id". The cause
was mine: `BROWSER_TOOL_CREATE_TASK` returns `watch_task_id`, and I had guessed
`taskId`, `task_id`, and `id`. Read the response instead of guessing, fixed it,
cleaned the twelve bad notes out of the records, re-ran.

**It contradicted 3 of the 12 rows it checked.** A real browser on the live page:

- Amazon Selling Partner, we said free self-serve. The page says API access needs
  a Professional selling plan at $39.99 a month plus identity verification.
- Waterfall.io, we said free self-serve. The site only offers "Request API key"
  and "Book a Call".
- QuickBooks, we said paid plan. Intuit's own docs say the Builder tier is free
  and every app starts there.

They are flagged on the page, not corrected. A browser agent writing prose is a
second opinion, not a quoted sentence, and overwriting a schema field with it
would be exactly the unevidenced edit this project exists to avoid.

Loop C is also now a separate command, `scout verify --browser-only`. Re-running
A and B just to reach it would burn hundreds of model calls and re-introduce the
run-to-run variance for no gain.

### What each loop is actually worth, now that all three have run

Loop A found disagreements on 86 of 100 rows. Loop B found nothing, and that is a
finding about Loop B. Loop C checked 12 rows and overturned 3. The most expensive
check is the only one that found errors, which is not a coincidence: it is the
only one that looked at something the first pass had not already seen.

### The page

`site/index.html`, one static file, 360KB, no CDN and no build step. All 100 rows,
the crosstabs and the charts are inlined, so it works opened from disk with no
network. Checked in a real browser: the table filters and sorts, 27 of 100 come
back for gated only, and there are no console errors.

The seven headline claims are written by hand. A sentence generated from a
crosstab reads like a sentence generated from a crosstab.

The verification section carries the things that went wrong, at the same weight
as the things that went right: the Loop C contradictions, Loop B being hollow,
the Sherlock name collision, and the 34 of 100 rows with a core claim from a
third party host.

### Still open

Per-field accuracy is not on the page, because `data/human_sample.json` does not
exist yet and the agent is not allowed to write it. Coverage and loop counts are
shown instead, clearly labelled as not being accuracy.

The subject check, one call per page asking whether the page is about this app,
is specified and not built. It is the highest value thing left.

---

## Session 4, 2026-09-17

Loop E, the subject check, and a correction to something I claimed earlier.

### Loop E

Two layers. The domain check is free: the app's own site and docs domain come
from apps.csv. On code hosts the comparison is the repo path, not the host,
because `github.com/sherlock-project/sherlock` and
`github.com/someone-else/sherlock-mcp` are not the same project. Anything else
is third party, which is not disqualifying, so those pages get one small call
asking whether the page is primarily about this app.

**Verdicts aggregate per host, not per page.** The first run showed why: the
checker said no to one `docs.cloudferro.com` page and yes to another, and that
yes let a different product's REST API through into Sherlock's row. One no now
condemns the host for that app.

Loop E is folded into `scout verify --all`, so one command runs A, B and E from
`data/raw`. It also exists standalone as `scout verify --subject`.

### What it found

97 third-party evidence items confirmed as genuinely about their app, 403 from
the app's own domain, and **12 items dropped across 5 rows** as another
product's documentation: Meta Ads, LinkedIn Ads, SE Ranking, Sherlock, Mermaid CLI.

Sherlock now has zero evidence, every field null, and confidence 0.37. That is
the correct answer. Six pages covering four different products called Sherlock
taught us nothing reliable about the real one, and the row now says so instead
of claiming "ready to build, REST, Bearer".

### A correction to session 2

I reported that "34 of 100 rows have a core claim from a third-party host" and
strongly implied most of those were contamination. The host heuristic was right
about the count and wrong about the meaning. When an actual subject check ran,
only 5 of those rows were reading another product's docs. The rest were
legitimate third-party sources: integration docs, MCP registries, vendor
documentation on a different domain. The lesson is the one this whole project
keeps relearning: a heuristic that flags something is not a finding until
something checks it.

### Final numbers, frozen baseline against verified

| | pass1 | verified |
| --- | --- | --- |
| stage determined | 53 | 79 |
| api_type | 62 | 91 |
| auth | 73 | 98 |
| access | 87 | 83 |
| median confidence | 0.69 | 0.74 |

access and mcp went slightly down, and that is Loop E working: four rows lost an
access value that came from the wrong product's pricing page. A number going down
because a check removed something false is the system behaving correctly.

### What is left

`data/human_sample.json`. Twenty apps are chosen and the template is waiting.
Until a human fills it in there is no per-field accuracy on the page, and the
page says so rather than quietly showing coverage and calling it accuracy.

### Loop E, second attempt

The first version aggregated subject verdicts per host, and that was too blunt.
`npmjs.com/package/@mermaid-js/mermaid-cli` is Mermaid CLI's own package and
`npmjs.com/package/@mermaidchart/cli` is a different product, so a host level
verdict let the second condemn the first and Mermaid CLI lost a correct
`api_type`. Verdicts are now keyed by project, not host, on the registries and
code hosts that carry many unrelated projects. Everywhere else the host is still
the right unit.

Final Loop E result: **11 evidence items dropped across 5 rows** as another
product's documentation. Meta Ads and blotato.com, LinkedIn Ads and postzen.dev,
SE Ranking and blogarama.com, Sherlock and both CloudFerro and a stranger's
sherlock-mcp repo, Mermaid CLI and @mermaidchart/cli.

Sherlock now has zero evidence and every field null. That is the right answer:
six pages covering four products called Sherlock taught us nothing reliable
about the real one, and the row says so instead of claiming "ready to build".

### One more rule bug, found by Mermaid CLI

After Loop E stripped its access evidence, Mermaid CLI had `api_type: CLI-only`
and no stage, because `derive_stage` returned early whenever access was null.
That was wrong. If there is nothing to call, how you would have signed up does
not matter. The no-viable-API rules now run before the null check, which is what
CLAUDE.md said all along.
