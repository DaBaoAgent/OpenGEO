# Company Knowledge Base Grounding

How a draft gets its facts from the company's own records instead of from model
memory. Companion to [MCP-INTEGRATION.md](MCP-INTEGRATION.md), which covers the
server configuration; this document covers the workflow.

## Why it exists

A writer with no company corpus has two options for a product specification,
and both are the same defect: recall a number and hope it is right, or lift one
from a competitor's page. Neither survives contact with a customer. The
knowledge base replaces both with a provenance trail: every claim carries a
`doc_id`, and every image carries the `sha256` it was downloaded with.

## The two transports

| Transport | Endpoint | Carries |
|---|---|---|
| MCP over HTTP | `http://192.168.1.79:8788/mcp` | Text: search, read, list, stat, write |
| File service | `http://192.168.1.79:8789/api` | Binaries: images, video, originals |

The MCP surface carries no bytes. When a document names a photo, the photo
itself comes from the file service. Both use `COMPANY_KB_TOKEN`.

Library names (`kb`): `ayh-product`, `jks-product`, `sop` (read-only),
`aftersale`, `training`, `common`, `marketing`.

## One command

```bash
python3 scripts/kb_client.py ground \
  --query "轮椅租赁 价格 流程" --query "昆山 苏州 门店" \
  --kb jks-product,marketing --top-k 6 \
  --out drafts/<NN>-<slug>/kb --pull-assets --resolve-assets --asset-limit 12
```

Output:

```
drafts/<NN>-<slug>/kb/
  kb-evidence.json        queries, documents, assets, provenance
  documents/<doc_id>.md   full text of every matching document
  assets/<source path>    downloaded binaries, source path preserved
```

Per document: `doc_id`, `title`, `kb`, `version`, `chars`, `sha256`,
`local_path`, `matched_queries`, `assets`. Per asset: `root`, `source_path`,
`local_path`, `bytes`, `sha256`. A reference that could not be fetched is
kept under `unresolved_assets` with its reason, and an asset recovered by
name resolution also records the `reference` and `resolved_by`.

An `原件：` path belongs to whichever tree the document was created in, which
is often not the file-service tree, so it 404s verbatim. `--resolve-assets`
indexes the tree once and retries each failed reference against a
normalised basename. A name shared by several files is reported as
`ambiguous` and left undownloaded: pulling the wrong product photo is worse
than pulling none.

The `sha256` values are the point. They let a reviewer confirm that the image
published next to a claim is the image the knowledge base holds, and notice
when a source changed underneath a draft.

## Output location

A delivered article occupies two sibling folders that share one sequence number
`NN` and one slug.

- `drafts/<NN>-<slug>/` is the gated working package: the article, the numbered
  publish images, `hero.jpg`, the rendered `.html` and `.pdf`, the `kb/` evidence
  bundle, `preview/`, `review.md`, `preflight-report.json` and
  `capabilities.json`. This is the delivery contract's draft folder `D`, and
  `ground` writes into `D/kb/`.
- `out/<NN>-<slug>/` is the publish set and nothing else: the article `.md` plus
  five numbered `.jpg` files. `01-cover.jpg` is the cover, `02` to `05` are the
  body images in the order the article uses them. It carries no HTML, no PDF, no
  evidence bundle and no review, because the platform takes text and images.

Take the next free number and never renumber a delivered package. Build the
publish set from the working package rather than copying files by hand:

```bash
python3 scripts/export_publish.py --draft drafts/<NN>-<slug>
```

The exporter refuses a draft folder that is not named `<NN>-<slug>`, an image
reference that is not flat and numbered in ascending article order, and an
existing publish folder holding anything it did not create, unless `--force` is
passed. Renderer placeholders such as `[TOC]` are dropped from the published
copy only, because the draft keeps them for `blog_render.py`.

Moving a package after it has been reviewed breaks the review binding: the
Gate 4 verifier state is keyed on the absolute folder path, so a relocated
package reports a missing verifier state and needs a fresh
`blog_preflight.py --init-review-nonce` and a fresh `blog-reviewer` dispatch
before it passes again.

Both folders are ignored by Git. A package holds company originals, the raw
source documents behind each claim and rendered previews, and all of it is
either re-downloadable or rebuildable. `kb-evidence.json` is the recipe.
Tracking either folder would also fail `scripts/check_secrets.py`, because the
rendered HTML carries `blog-source-sha256` and `blog-body-sha256` meta tags that
its entropy detector flags with no adjudication rule.


## Workflow

1. **Probe.** `python3 scripts/kb_client.py probe` before a run, not after a
   failure. It prints the server identity and the tool list, and exits nonzero
   when either transport is down.
2. **Scope.** Run `stat` once, then choose libraries from the topic. Product
   specifications start in `jks-product` or `ayh-product`; service promises add
   `aftersale`; voice and positioning add `marketing`.
3. **Ground.** Two to four query variants, varied vocabulary rather than one
   phrasing repeated. The corpus mixes human documents with auto-generated
   index files, so a term in the product sheet may not appear in the summary
   that indexes it.
4. **Separate sources from indexes.** Many documents open with an `原件：` line
   and end with a `MEMORY_FIELDS` block. Those are pointers. A summary confirms
   a document exists and names its terms; only the source carries the numbers.
   When the two disagree, the source wins and the disagreement is reported.
5. **Extract the citable set.** Build claim and `doc_id` pairs before drafting.
   Anything that cannot be paired leaves the draft.
6. **Resolve images.** Prefer a downloaded company asset over a stock or
   generated image for any image-backed marker. Keep `source_path` and
   `sha256` for the credit line and the review.
7. **Find an image when none is named.** `find --keyword K --ext jpg,png,webp`
   searches the file tree by path keywords, and `--out` downloads the
   matches. It sorts by path length, so the curated top-level asset wins
   over a per-post copy deeper in the tree.

## Evidence discipline

- Every specification, price, address, model number and promise carries its
  `doc_id`. An unsourced number is removed, not softened.
- A field the corpus marks `待补充` stays pending. Publishing a plausible figure
  there is fabrication even when the figure is correct in the real world,
  because no company record supports it.
- Prices and commercial terms are dated. Quote the document's update date, not
  the draft date.
- Regulated claims are limited to what the record states. Report the standard
  the company documents name and attribute it, rather than asserting
  compliance.
- Absence is a finding. "The knowledge base does not record a published
  warranty period" is usable; a guess is not.

## Composition

| Skill | What it takes from the bundle |
|---|---|
| `blog-brief` | The citable claim set and the gaps, before outlining |
| `blog-write` | Facts, `doc_id` citations and resolved image markers |
| `blog-geo` | Which entities the post can back with a first-hand source |
| `blog-schema` | `Product`, `Offer` and `Organization` fields the bundle supports |
| `blog-factcheck` | The `sha256` and `doc_id` trail as the verification baseline |

The bundle does not replace `blog-researcher`. The knowledge base answers what
the company says and holds; the open web answers what the market and the
regulators say. A post citing a company price and a national standard needs
both, and the standard is never sourced from the company corpus.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `probe` returns 401 or 403 | Stale token | Refresh `COMPANY_KB_TOKEN`; the network is fine |
| `probe` times out | Proxy intercepting the LAN host | Add the host to `NO_PROXY` |
| No hits | Vocabulary mismatch, or the corpus lacks the subject | Vary the query terms, then report the gap rather than filling it from memory |
| Only index files in the hits | Query matched summaries, not sources | Raise `--top-k` and search a term that appears in the source (a model code, an address, `型号`) |
| An asset 404s | The document outlived its file, or its `原件：` path belongs to another tree | The reference is kept under `unresolved_assets`; retry with `--resolve-assets`, then use `find` when resolution reports `ambiguous` or `missing` |
| `pull` reports skips | Unreadable or oversized paths | Skips are printed with a reason and kept in the manifest under `unresolved`; report the unresolved count as well as the download count |

## Security

- The token is a credential. It is read from the environment and never printed,
  never written into the bundle, and never placed in a draft, a report or a
  commit. `kb-evidence.json` records endpoints and digests, not the key.
- Downloads are written only under `--out`. A path that would escape it is
  refused rather than sanitised.
- `write` is gated behind `--allow-write` because it modifies shared company
  records. Updating an existing document also requires `expected_version`, so a
  concurrent edit fails loudly instead of being overwritten.
- The `sop` library is read-only.
