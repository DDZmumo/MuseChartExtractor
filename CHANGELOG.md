# Changelog

All notable project changes are recorded here. The project follows semantic
versioning for its Python package and versions the canonical chart schema
separately.

## [Unreleased]

No unreleased changes.

## [0.1.0] - 2026-08-11

First public alpha. Publishing still requires a green CI run for the tagged
revision and a matching GitHub Release; this heading alone is not publication
evidence.

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
