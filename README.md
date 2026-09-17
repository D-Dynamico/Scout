# Scout

An agent researched 100 apps and worked out what it actually takes to build a
connector for each one: how you authenticate, what stands between you and a
working API key, what the API looks like, whether an MCP server exists, and
whether you could start building today.

Every claim is backed by a sentence quoted from a page we fetched. Claims that
could not be evidenced are blank rather than guessed. The misses are on the page
too, because a tracker you cannot check is just a list.

The output is one static HTML file: `site/index.html`.

## Run it

```
pip install -e .
cp .env.example .env        # add COMPOSIO_API_KEY and GROQ_API_KEY
scout probe                 # confirm the tools and the model really exist
scout research --all        # 100 apps, writes data/raw, freezes data/pass1
scout verify --all          # loops A and B, writes data/verified
scout verify --browser-only # loop C, a real browser on the lowest confidence rows
scout analyze               # crosstabs into data/patterns.json
scout build-page            # renders site/index.html
```

A full run takes about fifteen minutes at six workers. It is resumable: every
app is one JSON file, so a crash at app 47 costs you app 47.

`scout probe` is worth running first. It asks Composio and Groq what actually
exists on your account rather than trusting anything in `.env`, and it will tell
you if your Composio key is missing the `tool_execution` scope, which is the
failure that looks like everything being broken.

## How it works

Composio provides the tools: web search, page fetching, the browser agent, and
the toolkit registry. The orchestration is plain Python we own, in `scout/`.
Nothing runs inside anyone's agent framework.

Per app:

1. **Search.** Three questions, one about auth, one about pricing and access, one
   about MCP. Results are taken round robin so all three questions get pages,
   not just the first one.
2. **Fetch.** Up to six pages, cached in `cache/` forever. A failed fetch is
   recorded but never served back as a hit.
3. **Extract.** One model call over the fetched pages, output strictly to schema,
   a quoted snippet per claim.
4. **Retrieve.** For anything the first pass could not evidence, score sentences
   from the same pages by that field's keywords, hand the model the top twelve
   and ask one narrow question. Finding one sentence in 70,000 characters is a
   needle hunt. Picking from twelve is not.
5. **Check, three gates.** The snippet must be on the page it cites. It must use
   the language of the claim. And a separate isolated call, shown only the claim
   and the snippet, has to agree the snippet supports it.
6. **Score.** Confidence starts at 1.0 and only goes down. A field we could not
   settle costs a little; a field resting on weak evidence costs more, because a
   wrong filled field costs a reader more than a blank one. Filling a field never
   raises the score. Every deduction is named in the row's notes.
7. **Registry.** Ask Composio whether it already ships a toolkit for this app.

## Verification

`data/pass1/` is the frozen baseline, written once and never modified.
`data/verified/` is what the loops produced. Comparing them is the point.

- **Loop A**, self consistency. A second extraction with a different prompt and a
  different model on the same pages. Disagreements are written into `notes`,
  never silently resolved.
- **Loop B**, evidence integrity. Every URL must resolve and every snippet must
  still be there. It currently finds nothing, and that is a finding about Loop B:
  it re-checks against the same cached pages the snippets came from, so it can
  only confirm itself. A real freshness check has to re-fetch.
- **Loop C**, browser check. A real browser on the live signup or pricing page for
  the rows we trust least. It contradicted 3 of the 12 rows it checked.
- **Loop D**, the human sample. `scout sample` picks twenty apps, ten hard ones
  named in the brief plus a seeded draw of one per category from the rest, and
  writes a blank template. `scout score` refuses to run until a human fills it in.
  The agent never writes `data/human_sample.json`, because a ruler you drew
  yourself measures nothing.

## What it gets wrong

Worth reading before you trust a row.

Nothing in the pipeline checks that a page is about the right **app**. Sherlock's
six pages covered four different products called Sherlock, and its auth, access
and api_type all came from the wrong ones. Across the set, 34 of 100 rows have at
least one core claim from a host carrying neither the app name nor its own
domain. Some of those are correct, `developers.facebook.com` is the right source
for WhatsApp Business. Many are third party content marketing. The fix is a
subject check per page before extraction, and it is not built.

The model is not deterministic even at temperature 0, so the same cached pages
can produce a different row on a different day. Page sets are pinned per app in
`data/urls/` to stop the input drifting as well.

## Layout

```
scout/        pipeline code, one module per step
data/pass0/   the scaffold-era run, kept as the before picture
data/pass1/   frozen baseline, 100 records
data/raw/     current run
data/verified/ after the verification loops
data/urls/    the pinned page set per app
cache/        every page we fetched, gitignored
site/         index.html
apps.csv      the 100 apps, category, and a docs hint
SESSIONS.md   every decision and why, including the bugs
```

`data/verified.json` is all 100 rows with their evidence in one file, for an
agent to consume.
