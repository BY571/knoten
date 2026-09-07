# knoten stage: source

A source is something you read before you reason from it: a paper, a blog post, a book
chapter, a doc page, a dataset, a forum thread, or your own head (`source-own-intuition`).
Every idea descends from one, so the graph can always answer "why did we try this".

When to do a source round: when `knoten frontier` shows nothing open you can act on, no
reopen condition that holds, and no untested gate. Then, in this order:

- papers: arXiv, Semantic Scholar, the venue of the last good paper you cited
- the docs and changelogs of anything you depend on
- blog posts and engineering write-ups from people who ship this
- books and surveys, for the settled parts
- forums, issue trackers, discussion threads, for what breaks in practice
- a general web search last, for what the above missed

File each thing you actually read, then derive ideas from the sources, not from memory.

Frontmatter it needs:

```yaml
type: source
status: alive
origin: <url, doi, or path>     # required: someone must be able to go back to it
```

Body, in this order:

# <what it is, one line>

## What it says
In your own words. Quoting it back is not reading it.

## Why it is here
The question it might answer, in a line or two. A direction, not a hypothesis.

## What it is not
What it does not cover, so it is never cited for something it cannot bear.
