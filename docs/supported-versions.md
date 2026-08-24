# Supported Resource Versions

Support is keyed by a complete content fingerprint, not by a marketing version
string or Steam build ID alone.

## Formally supported

| Inventory fingerprint | Addressables | Build result hash | Status |
|---|---|---|---|
| `sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5` | `1.21.20` | `9ecc2d74a4045582f2aabf0f64c83581` | M10 Compact Store achieved on one local installation |
| `sha256:d9108183177ac7c4821b466d28e0920d8a4a9bcd490a0edde956be3681233222` | `1.21.20` | `f4759f2e039525793e62c59c15df44c6` | M8 achieved on Steam depot manifest `241392741196033182` |

For the first fingerprint the extractor observed 5,218 files, 5,094 UnityFS
bundles, 2,331 StageInfo charts in 733 sources, 2,330 resolved song/chart
relationships, and one explicitly unresolved tutorial chart. Two complete
Canonical schema `1.0.0` batch runs produced identical manifests. Schema
`1.1.0` now has two complete in-place real-resource runs with identical
manifests. The first run passed the 15-category per-file
hash/layout/group/reference/raw-accounting audit; the second passed its
16-category fail-closed superset, which also recomputes manifest integrity.
The same resources now also have a complete physical Store `1.0.0`: all 2,331
payloads are retained, independent Store audit reports zero mismatches,
2,330/2,330 lazy Canonical reconstructions equal the old JSON objects, and a
same-directory second build produced identical manifest and SQLite bytes.

Current source verifies this registered Store with a Store-first sequence:
same-directory rebuild, source-aware `audit-store`, and Store-only `digest-store`.
The digest path reconstructs one Canonical chart at a time and can compare explicit
historical counts/digests without generating the former 14 GiB expanded JSON tree.
This does not broaden the fingerprint table or the M7 semantic claim.

For the second fingerprint the extractor observed 5,193 files, 5,069 UnityFS
bundles, and 2,305 StageInfo charts in 725 sources. It resolved and exported
2,304 charts while preserving `tutorial_v2_map1` as the same explicit
unresolved result. All 725 shared StageInfo sources and both note-data bundles
are byte-identical to the first profile. Two complete Canonical schema `1.1.0`
runs produced the same 2,577,100-byte manifest with SHA-256
`d893ca25bbb86683d3b27cdf016c594afc3406be9fd1432e5b2398298a0d94d2`.
Independent audits of both runs were byte-identical and reported zero file,
schema, layout, group, event-reference, or raw-accounting mismatches.

The public `v0.1.0` artifact predates the second profile and formally supports
only the first fingerprint. The second row describes the current source tree
and will become part of a later release. Its `GameAssembly.dll` and
`global-metadata.dat` differ from the first profile, so static offsets are not
shared even though the proven disk parser family is shared.

The Store writer uses the registered parser/grouping family and therefore has
a formal gate for both rows. Only the first row was rebuilt as a Store in the
2026-08-12 acceptance run. The second depot had already been removed to save
space, so no new Store result is claimed for it.

This table does not claim that every installation with a similar game version
is compatible. DLC ownership and updates can change the full fingerprint.

## Unknown fingerprints

`MuseDashInstallation.open()` still permits inventory inspection, but
`extract_charts()` raises `UnknownGameVersionError`. Start with the explicit
`scan` and `probe` research commands. Candidate parsing and a metadata-only
grouping census require the explicit `--allow-unsupported-research` opt-in;
their output is diagnostic-only and must not be treated as proof that the
known parser applies. After candidates, index, and a complete grouping census
agree on the same fingerprint, `extract-all --allow-unsupported-research` may
produce a nonformal M8 evidence run only together with the separate
`--allow-expanded-json` large-output opt-in. Its manifest contains
`profile_support.formal_support=false`; the flag never edits the registry or
turns that run into formal support. Compare the resulting structure, document
counterexamples, independently audit the full batch, and only then register a
new profile.

`extract-store` deliberately has no research override. An unknown fingerprint
must complete the research evidence chain and be registered before it can be
used as a long-term Store source.

Never bypass the gate by editing a diagnostic fingerprint string. Formal
support requires current-file hashes and repeatable parser evidence.
