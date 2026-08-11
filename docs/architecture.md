# Architecture

## Boundary

MuseDashChartExtractor reads a user-owned local installation and writes only to
an explicitly selected output directory outside the game tree. It does not
launch Muse Dash, install mods, inject code, read runtime memory, or mutate game
files.

```text
local installation
  -> deterministic inventory and fingerprint
  -> Unity / Addressables metadata
  -> StageInfo candidate evidence
  -> strict Odin Binary parser
  -> evidence-backed record grouping
  -> ALBUM / Addressables song-chart index
  -> Canonical Chart 1.1.0
  -> validation
  -> batch manifest and JSON / CSV exporters
```

## Evidence gates

Each stage consumes reproducible artifacts from the previous stage. Batch
extraction requires a complete grouping census whose fingerprint, candidate
count, source count, raw-parse count, and grouping count all match. Candidate
IDs must exactly equal indexed IDs plus explicitly unresolved IDs.

The formal parser is selected by the complete installation fingerprint. An
unknown fingerprint may be scanned and probed by default. Deeper candidate or
structure research requires the explicit `--allow-unsupported-research` CLI
opt-in and remains nonformal. A research-only batch is permitted only after
the candidate, index, source fingerprint, and complete grouping-census gates
all agree; its manifest is marked `formal_support=false`. No research command
can mutate the profile registry. Registration is a reviewed source change made
only after full extraction and independent audit evidence exists.

## Data ownership

`diagnostics/` contains metadata-only evidence. `experimental/`, `extracted/`,
and `exports/` contain local official-derived data and are Git ignored. The
package, sdist, and wheel contain only source, docs, and synthetic fixtures.

## Parser layers

- `scanner.py`: path validation, magic detection, hashes, combined fingerprint.
- `unity/`: Unity bundle metadata, Addressables compact catalog, strict Odin
  wire reader.
- `discovery/`: StageInfo evidence, raw extraction, grouping, note config, and
  song/chart relationships.
- `charts/`: canonical model, lossless projection, validation.
- Canonical raw evidence uses one `raw_records` table and one `record_groups`
  table. Events retain only raw indices; derived Phase 5 `logical_objects` are
  omitted only after exact reconstruction equality succeeds.
- `batch.py`: one-load-per-bundle orchestration and deterministic manifest.
- `batch_audit.py`: independent file/hash/schema/index-closure audit for a
  completed local batch.
- `installation.py`: supported-version gate and public Python facade.
- `exporters/`: neutral destination protocols and JSON/CSV views.

## Failure model

Unknown or malformed wire data reports an offset, tag, and field context.
Per-chart failures remain manifest rows; global evidence contradictions stop
before a new manifest is written. Reused output trees are accepted only when
all existing files belong to the current complete plan, and the final file set
must exactly equal this run's successful rows.
