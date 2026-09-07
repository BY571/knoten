# knoten stage: idea

An idea is a direction you might test, derived from something you read (a `source`) or
something you found (a `finding`). It is not yet a claim. Status `open`.

Frontmatter it needs (the graph refuses it otherwise):

```yaml
type: idea
status: open
links:
  - {rel: prov:wasDerivedFrom, to: <the question this serves>}
  - {rel: prov:wasDerivedFrom, to: <the source or finding it came from>}
```

Body, in this order:

# <the direction, one sentence>

## Where it came from
The source or finding, and the one thing in it that suggested this.

## Why it might hold
The mechanism, or an honest "because I think so".

## Why it might not hold
The strongest case against it, written before any experiment has a stake.

## What would make it false
The observation that would end this direction. Without one it is a preference.

If you have no idea to file, do not invent one: go back to sources. Read
`prompt-source.md`, which says where to look and how to file what you read.
