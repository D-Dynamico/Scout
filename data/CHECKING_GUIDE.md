# How to check the 20 by hand

You are building the ruler. The agent's rows get measured against this, so the
one rule that matters more than speed is: **do not look at the agent's answer
first.** Fill your value, then compare. Otherwise you will anchor on it and the
accuracy number becomes theatre.

Work in `data/human_sample.template.json`, save as `data/human_sample.json`.

Budget 4 to 5 minutes an app. If you run short, the first four fields are the
ones that decide the stage verdict. Leave the rest null, null is a legitimate
entry and `scout score` skips anything you did not check.

---

## Where to look, in order

For each app, open two tabs:

1. **The developer docs landing page.** Usually `developers.<domain>`,
   `docs.<domain>`, `<domain>/developers`, or `<domain>/docs/api`. The starting
   URLs below are what the pipeline used, and they resolved when we swept them.
2. **The pricing page.** Usually `<domain>/pricing`. This is where the access
   answer usually lives, and it is the field the agent is worst at.

If a docs landing page exists, the auth page and the endpoint list are almost
always one click from it.

---

## The fields, and what settles each one

### access, the most important one

**The question: can a developer get a working API credential themselves, in
under an hour, without paying and without a human approving it?**

Not "is there a free trial of the product". Not "can I make an account". Can you
get a *key that works*.

- `free self-serve` you sign up and create a key in a dashboard. No card, no
  approval. Most developer-first tools.
- `free trial` the only way to a key is a time-limited trial.
- `paid plan` you must be on a paid tier before the API is switched on. Look for
  a plan comparison table with "API access" ticked only on higher tiers.
- `admin approval` a workspace admin, an app review, or a verification step
  stands between you and a working token.
- `partner or contact-sales` there is no self-serve path at all. "Request access",
  "Talk to sales", "Book a demo", a form.
- `no public API` there is no API for outside developers.

**Where the answer usually is:** the pricing page footnotes, or a line on the
docs landing page saying who can use this. If the pricing page has an API row in
the plan table, that row is your answer.

### auth

Open the docs' authentication page. Decide by what you would actually put in the
request:

- `OAuth2` there is an authorize URL, a redirect, a token exchange, scopes.
- `API key` a key you generate in a dashboard, often in a custom header.
- `Bearer` a static token in `Authorization: Bearer ...` with no OAuth flow.
- `Basic` HTTP basic auth, username and password or key as username.
- `custom` a signing scheme, HMAC, or anything that is none of the above.
- `none` no authentication needed.

If the app supports several, record the one the docs lead with for a
server-to-server integration.

### api_type

The API reference index tells you in a few seconds.

- `REST` HTTP endpoints and paths.
- `GraphQL` a single endpoint and a schema.
- `SDK-only` the only supported way in is a client library.
- `CLI-only` a command line tool with nothing to call over a network.
- `none` no programmatic surface.

If both REST and GraphQL exist, pick the one the docs lead with.

### prod_requires_review

**This is about shipping, not building.** Can you build against it today and
still need the vendor to approve your app before other people's accounts can use
it?

Search the docs for: app review, submit for review, verification, production
access, public distribution, marketplace listing, "development mode".

`true` if such a review exists, `false` if there is genuinely none, `null` if the
docs do not say. Null is common and honest here.

### mcp

Search `<app> MCP server`. Then check *whose* it is.

- `official` the vendor ships it. On the vendor's own domain or their GitHub org.
- `community` a third party built it.
- `none` you looked and there is not one.

Check the GitHub owner. `vendor/thing-mcp` is official, `somebody-else/thing-mcp`
is community. This is where the agent is most likely to be over-confident.

### api_breadth

Count sections in the endpoint reference. Under 30 is `small`, 30 to 150 is
`medium`, over 150 is `large`. If you cannot see a list, `unknown` is the right
answer and not a failure.

### stage

Leave it null unless you disagree. It is derived from access and api_type by
code, so filling it in by hand mostly tests the derivation rather than the data.
If you do think the derived stage is wrong, that is worth recording.

---

## Recording it

- Put the value in the field, and the URL where you saw it in `<field>_source`.
  That URL is your evidence and goes on the page next to the agent's.
- When the agent turns out to be wrong, write one line in `notes`: what it said,
  what is true, and your best guess why it went wrong. That becomes the misses
  log, and the "why" is the part a reviewer will actually read.
- `checked_by` is just your name, so the page can say who checked it.

---

## The 20, with starting URLs

The hard half first. These are hard on purpose: several have no obvious docs,
and "there is no public API" is a correct and valuable answer for some of them.

| # | App | Start here |
| --- | --- | --- |
| 58 | Sherlock | github.com/sherlock-project/sherlock. Careful: several unrelated products are also called Sherlock. Confirm you are on the OSINT username tool. |
| 98 | Mermaid CLI | github.com/mermaid-js/mermaid-cli. Note npm has a separate `@mermaidchart/cli` which is a different product. |
| 84 | Paygent Connect | No domain known, and we never established which company this is. Search "Paygent Connect API" and "Paygent NMI". If you cannot identify the company, record that: an app you cannot even identify is a real finding. |
| 85 | iPayX | ipayx.ai, then ipayx.ai/docs |
| 50 | fanbasis | fanbasis.com. Look for a developers or API link in the footer. |
| 59 | Waterfall.io | waterfall.io. Check whether the only path is "Request API key". |
| 91 | NotebookLM | notebooklm.google, then cloud.google.com/gemini. The question is whether NotebookLM itself has an API or only the enterprise Gemini surface does. |
| 94 | Consensus | consensus.app. Check for a developer or API page, and whether access is by request. |
| 90 | PitchBook | pitchbook.com. Look for the research or data API and whether it is client-only. |
| 10 | DealCloud | api.docs.dealcloud.com |

The random half, one per category, seed 20260916.

| # | App | Start here |
| --- | --- | --- |
| 1 | Salesforce | developer.salesforce.com/docs. Watch for the Developer Edition org, and note that connected app creation changed in Spring 2026. |
| 17 | Plain | plain.com/docs/api-reference/graphql |
| 23 | Zoho Cliq | zoho.com/cliq/help/restapi/v2/ |
| 32 | Meta Ads | developers.facebook.com/docs/marketing-apis. Expect development mode versus advanced access to matter here. |
| 46 | Squarespace | developers.squarespace.com |
| 54 | MrScraper | docs.mrscraper.com |
| 70 | Sentry | docs.sentry.io/api/ |
| 78 | Coda | coda.io/developers/apis/v1 |
| 88 | Brex | developer.brex.com |
| 97 | higgsfield | higgsfield.ai/cli |

---

## When you are done

```
scout score
```

It writes `data/accuracy.json` and `data/misses.json`, per field and per half,
comparing both the frozen baseline and the verified set against your answers.
Then the per-field before-and-after table goes on the page.
