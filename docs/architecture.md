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
  -> validation
  -> content-addressed raw Odin Store 1.0.0 + SQLite index
  -> lazy Canonical Chart 1.1.0
  -> on-demand JSON / CSV exporters
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
`exports/`, and `MuseDashChartStore/` contain local official-derived data and
are Git ignored. `.odin`, SQLite Store indexes, Store manifests, and payload
directories are also rejected by the release archive auditor. The package,
sdist, and wheel contain only source, docs, and synthetic fixtures.

## Parser layers

- `scanner.py`: path validation, magic detection, hashes, combined fingerprint.
- `unity/`: Unity bundle metadata, Addressables compact catalog, strict Odin
  wire reader.
- `discovery/`: StageInfo evidence, raw extraction, grouping, note config, and
  song/chart relationships.
- `charts/`: canonical model, lossless projection, structural/aggregate
  validation, and optional provenance-bearing indexed event-reference comparison.
- `charts/event_reference.py`: strict `event-reference-v1` input validation and
  deterministic per-index difference accounting, isolated from the main chart validator.
- Canonical raw evidence uses one `raw_records` table and one `record_groups`
  table. Events retain only raw indices; derived Phase 5 `logical_objects` are
  omitted only after exact reconstruction equality succeeds.
- `batch.py`: one-load-per-bundle orchestration and deterministic manifest.
- `batch_audit.py`: independent file/hash/schema/index-closure audit for a
  completed local batch.
- `store/schema.py`: physical Store `1.0.0` DDL, content-address paths,
  containment, atomic writers, and logical digest.
- `store/writer.py`: formal-profile-gated, one-load-per-bundle extraction of
  exact `SerializedBytes`, transactional SQLite construction, and manifest-last
  publication.
- `store/reader.py`: metadata-only chart iteration, SHA-verified raw reads, and
  single-chart lazy Canonical `1.1.0` reconstruction without an unbounded cache.
- `store/audit.py`: independent SQLite/FK/ID/file-set/Odin/grouping/StageInfo
  audit with optional source bundle and PathID revalidation.
- `store/equivalence.py`: one-chart-at-a-time semantic comparison with a legacy
  Canonical JSON tree; reports only counts and hashes.
- `installation.py`: supported-version gate and public Python facade.
- `exporters/`: neutral destination protocols and JSON/CSV views.

## Store transaction boundary

The writer creates `.building` before reading candidate objects. A reader
rejects any Store with this marker, even if an older manifest is still present.
Before either cleanup point, the writer recursively rejects symlinks and Windows
junctions anywhere in `.staging`, so recovery cannot traverse outside the Store.
New payloads and SQLite are written to same-filesystem temporary paths; SQLite
uses one transaction with foreign keys and full synchronization. Existing
content-addressed payloads are reused only after exact size/SHA verification.
`index.sqlite3` is replaced after all rows pass integrity checks, and
`store.json` is written atomically last.

Payload files are addressed only as
`payloads/sha256/<prefix>/<sha256>.odin`. Reader and auditor reject traversal,
non-canonical paths, symlinks, wrong shard/name, size or digest. The auditor
compares the exact indexed and on-disk payload sets, so stale/extra payloads are
visible failures rather than silent retained state.

`ChartStore.iter_charts()` queries only SQLite. `read_payload()` reads and
hashes one file. `load_chart()` parses one Odin stream, joins only its referenced
global note configs and indexed song, and then invokes the existing grouping and
canonicalization path. The Store layer has no MusePlay-specific dependency;
downstream dependency direction remains `consumer -> MuseDashChartExtractor`.

## Failure model

Unknown or malformed wire data reports an offset, tag, and field context.
Per-chart failures remain manifest rows; global evidence contradictions stop
before a new manifest is written. Reused output trees are accepted only when
all existing files belong to the current complete plan, and the final file set
must exactly equal this run's successful rows.

For a Store, every candidate is represented as `success`, `uncertain`, or
`failed`, and every recovered payload remains addressable even when song
identity is unresolved. Audit reports contain metadata, hashes, counts, and
bounded mismatch descriptions only; they never embed payload bytes or events.
