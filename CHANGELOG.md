# Changelog

All notable project changes are recorded here. The project follows semantic
versioning for its Python package and versions the canonical chart schema
separately.

## [Unreleased]

### Added

- Add optional `event-reference-v1` validation input for complete indexed event
  streams. It compares exact time/type/lane/duration fields without greedy
  alignment, reports bounded per-category differences, and leaves omitted
  fields explicitly `not_compared`.
- Add physical Chart Store schema `1.0.0`: exact Odin Binary payloads are
  content-addressed by SHA-256 while standard-library SQLite stores source,
  chart, StageInfo, song, and shared note-config indexes without payload BLOBs.
- Add the lazy `ChartStore` API, formal-profile-gated `extract-store` command,
  fail-closed `audit-store`, and a streaming Canonical-tree equivalence checker.
- Add synthetic coverage for every byte value, payload deduplication, unknown
  fields/types, uncertain charts, corruption, FK/manifest mismatches, traversal,
  casefold collisions, interrupted writes, and deterministic reruns.

### Changed

- Rework the README into a release-oriented project overview and move the
  complete evidence-gated command workflow to `docs/cli-reference.md`.
- Record the completed `v0.1.0` CI/release evidence and direct GitHub wheel
  installation path in the public documentation.
- Add formal support evidence for Steam depot manifest `241392741196033182`,
  keyed by its complete `d9108183...33222` inventory fingerprint.
- Permit explicit nonformal `extract-all` research runs for unknown
  fingerprints only after the full candidate/index/grouping-census gates pass;
  their manifests cannot claim formal support.
- Add a full-batch schema `1.1.0` auditor that verifies every file hash and the
  exact raw-record/event/sentinel index closure, recomputes fail-closed manifest
  integrity, and reports malformed UTF-8 as structured audit failures.
- Re-run the latest fingerprint's full schema `1.1.0` batch in place and verify
  a byte-identical manifest without retaining a duplicate chart tree.
- Compile and smoke-test packaged tools in CI, and reject release tags that do
  not match the package version.
- Make Compact Store the recommended long-term format while keeping Canonical
  schema `1.1.0` and JSON/CSV exporters unchanged and available on demand.
- Harden Store reruns and audits against nested symlink/junction cleanup,
  stale payloads, report-path overwrite, parser/schema drift, dangling note UID
  references, false phase-gate passes, and incomplete status-set comparisons.
- Validate the latest real fingerprint as a 1,101,577,861-byte Store: 2,331
  payloads, 2,330 success, one uncertain, zero failed, zero audit mismatches,
  2,330/2,330 Canonical equivalence, and byte-identical second-run manifest and
  SQLite index.

## [0.1.0] - 2026-08-11

First public alpha, published from tagged revision `9158640` after its test,
package, and release workflows passed. The matching GitHub Release contains the
audited wheel and sdist; this heading alone is not publication evidence.

### Added

- Deterministic read-only resource inventory and Unity/Addressables metadata
  probes.
- Evidence-scored StageInfo discovery for 2,331 chart candidates on the first
  supported resource fingerprint.
- Strict bounded Odin Binary parsing for MusicData and non-null dialog events.
- Evidence-backed logical grouping, song/difficulty indexing, Canonical Chart
  schema 1.1.0, and multi-chart validation reports.
- Sequential batch extraction with complete success/failed/uncertain manifest,
  stale-output protection, and reproducible per-file SHA-256 values.
- `MuseDashInstallation`, `ChartExporter`, `JsonExporter`, and `CsvExporter`
  public APIs.
- Canonical `1.1.0` single raw-record table with index-only event references,
  exact Phase 5 reconstruction checks, and duplicate-payload rejection.
- Explicit unknown-version probe-only default with a diagnostic-only research
  opt-in, local-game tests, packaging exclusions, and CI.
- Allowlisted wheel/sdist auditing that rejects links, renamed payloads, and
  metadata/version inconsistencies; CI retains the exact audited artifacts.

### Known limitations

- Formal support is limited to one exact resource fingerprint.
- Event-level video/runtime reference data is not available for all charts;
  M7 is a partial validation claim.
- `is_air` remains unknown in canonical output.
