# pass0, the scaffold-era run

Not a baseline. This is the before picture.

This version of the pipeline checked that every quoted snippet really appeared on the
page it cited. It never checked that the snippet supported the claim. So a real quote
could be attached to a claim it said nothing about, and the record looked fully evidenced.

`1.json` is the Salesforce row it produced. Two things to look at:

- `mcp: "official"` backed by the snippet "REST API Developer Guide | Salesforce Developers",
  which does not mention MCP at all. A false claim with verbatim evidence.
- `confidence: 1.0`, on a record with two wrong fields. The old formula rewarded filling
  fields, so a worse record scored higher than an earlier, more honest one that scored 0.57
  and correctly left `access` null.

What changed after this: a term filter, a per-claim support check by a second model call,
and a confidence score that starts at 1.0 and only goes down.
