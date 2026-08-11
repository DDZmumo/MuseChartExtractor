# Supported Resource Versions

Support is keyed by a complete content fingerprint, not by a marketing version
string or Steam build ID alone.

## Formally supported

| Inventory fingerprint | Addressables | Build result hash | Status |
|---|---|---|---|
| `sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5` | `1.21.20` | `9ecc2d74a4045582f2aabf0f64c83581` | M8 achieved on one local installation |

For this fingerprint the extractor observed 5,218 files, 5,094 UnityFS
bundles, 2,331 StageInfo charts in 733 sources, 2,330 resolved song/chart
relationships, and one explicitly unresolved tutorial chart. Two complete
Canonical schema `1.0.0` batch runs produced identical manifests. Schema
`1.1.0` has one complete real-resource refresh plus an independent per-file
hash/layout/reference audit; synthetic tests cover current-layout determinism.

This table does not claim that every installation with a similar game version
is compatible. DLC ownership and updates can change the full fingerprint.

## Unknown fingerprints

`MuseDashInstallation.open()` still permits inventory inspection, but
`extract_charts()` raises `UnknownGameVersionError`. Start with the explicit
`scan` and `probe` research commands. Candidate parsing and a metadata-only
grouping census require the explicit `--allow-unsupported-research` opt-in;
their output is diagnostic-only and must not be treated as proof that the
known parser applies. Compare the resulting structure, document
counterexamples, and only then register a new profile.

Never bypass the gate by editing a diagnostic fingerprint string. Formal
support requires current-file hashes and repeatable parser evidence.
