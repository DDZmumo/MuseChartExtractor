# MuseChartExtractor agent instructions

## Scope and source of truth

These instructions apply to the whole repository.

- Read `ROADMAP.md` first, then `README.md`, `docs/architecture.md`, `docs/schema.md`, `docs/supported-versions.md`, `docs/validation.md`, and the relevant sections of `docs/reverse-engineering-notes.md` before changing parser, schema, milestone, compatibility, or acceptance status.
- Treat current code, exact local artifacts, reproducible commands, and independently checked hashes as evidence of what exists. Treat roadmap targets as intended gates, not implementation proof.
- Follow the project rule: prove before abstracting, parse one chart before all charts, preserve raw evidence before transforming it, and keep the core neutral for downstream consumers.

## Standalone project boundary

- MuseChartExtractor is a standalone, read-only, offline Disk-to-Parser project. Its core owns resource inventory, Unity/Addressables/Odin parsing, candidate evidence, song/chart indexing, Canonical schema, Compact Store, audit, and neutral exporters/APIs.
- Do not add MusePlay, AutoMuseDash, YOLO, autoplay, keyboard input, model training, frame labeling, gameplay control, runtime hooks, DLL injection, memory reading, or project-specific adapters to this repository.
- Any bridge from extracted charts to AutoMuseDash or a training workflow must be implemented under `D:\Projects\PythonP\AutoMuseDash` (the AMD workspace), or another downstream workspace explicitly chosen by the user. The dependency direction is always `downstream -> MuseChartExtractor`; never introduce a reverse import or shared ownership here.
- Do not place bridge prototypes under this repository's `experimental/`, `tools/`, tests, docs, or package namespace. This repository may expose only generally useful, consumer-neutral APIs or exporters.

## Current state and evidence boundary (2026-08-24)

- Public `v0.1.0` contains the Phase 1-9 extractor for the first exact resource fingerprint. Current source also registers a second exact fingerprint whose Phase 1-9 evidence is complete but not part of `v0.1.0`.
- Canonical schema is `1.1.0`. Physical Compact Store schema is `1.0.0`.
- Phase 0-11 and M0-M10 are implemented for the first exact fingerprint. Compact Store acceptance covers 2,331 payloads, 2,330 resolved charts, one explicit uncertain chart, zero failed charts, zero independent audit mismatches, 2,330/2,330 Canonical equivalence, and deterministic same-directory rebuild hashes.
- M7 remains partial: structural/source/aggregate checks are not full event-level timing, type, lane, air/ground, or duration ground truth. `is_air` remains uninterpreted and `tutorial_v2_map1` remains unresolved/uncertain.
- Do not advance semantic accuracy, compatibility, or milestone claims from code presence, tests, matching counts, or storage equivalence alone.

## Store-first output and acceptance policy

- Compact Store is the default long-term format and the default full-library acceptance format. Do not generate an expanded Canonical JSON corpus for routine Store verification.
- The normal full-library sequence is `extract-store` -> source-aware `audit-store` -> Store-only `digest-store` -> a second same-directory Store rebuild -> audit/digest/determinism comparison.
- `extract-all` is a legacy compatibility and explicit research command. It must fail closed unless the caller supplies `--allow-expanded-json`, and its warning must state the approximate 14 GiB output risk, Store-first alternative, and official-derived redistribution boundary.
- JSON/CSV export is normally limited to one chart or an explicitly requested small set through `ChartStore.load_chart()` and neutral exporters.
- Generate a full Canonical JSON tree only after the user explicitly approves it and a concrete independent need cannot be covered by Store audit, Store-only Canonical digest, or Store-to-Store comparison.
- `digest-store` must reuse the same stable Canonical encoder and corpus framing as historical equivalence, load only one resolved chart at a time, retain no unbounded corpus cache, and emit metadata/hashes/counts plus at most 10 bounded failures. It must never write chart JSON or payload bytes.

## Repository ownership

- `src/musedash_chart_extractor/scanner.py`: deterministic inventory and installation fingerprint.
- `src/musedash_chart_extractor/unity/`: Unity bundles, Addressables catalog, and strict Odin wire parsing.
- `src/musedash_chart_extractor/discovery/`: candidates, structure recovery, grouping census, note data, and song/chart indexing.
- `src/musedash_chart_extractor/charts/`: Canonical models, lossless projection, and validation.
- `src/musedash_chart_extractor/store/`: Store schema, writer, lazy reader, independent audit, and streaming equivalence.
- `src/musedash_chart_extractor/store/canonical_digest.py`: Store-only, one-chart-at-a-time Canonical corpus digest and expected-baseline verification; no expanded chart output.
- `src/musedash_chart_extractor/installation.py`: supported-fingerprint gate and public facade. Unknown fingerprints remain probe-only unless an explicit research command and all evidence gates permit more.
- `src/musedash_chart_extractor/exporters/`: neutral JSON/CSV interfaces. Do not add downstream-specific exporters.
- `tests/`: synthetic fixtures and regression coverage. `local_game` tests require a user-owned installation and never run in CI.
- `diagnostics/`: local metadata-only evidence. Preserve it unless the user explicitly identifies a disposable artifact.
- `experimental/`, `extracted/`, `exports/`, `MuseDashChartStore/`, `build/`, and `dist/`: local/generated outputs with separate ownership and release rules. Official-derived data, `.odin`, SQLite, Store manifests, and full dumps must remain Git ignored and must not enter wheel/sdist archives.

## Safety and data handling

- Read only from the user-selected Muse Dash installation. Never modify game resources or write inside the game directory.
- Inspect `git status --short` before and after edits. Existing tracked/untracked changes and all ignored research artifacts belong to the user.
- Do not delete or overwrite `diagnostics/`, `experimental/`, `MuseDashChartStore/`, extracted resources, historical hashes, or research evidence without an exact, user-approved target and a verified recovery/retention boundary.
- Before recursive cleanup, resolve the exact literal path, prove it is within the intended output root, and reject symlinks, junctions, and other reparse points. Never weaken the Store `.staging` protections.
- Unknown tags, fields, object types, versions, candidates, or identities must fail loudly or remain explicit `unknown`/`uncertain`; never silently skip, guess, or fabricate a mapping.
- Do not commit or redistribute official charts, bundles, audio, textures, DLC content, payloads, or outputs capable of reconstructing them.

## Development and documentation rules

- Prefer small evidence-backed changes and focused pure functions. Preserve stable CLI and Python API behavior unless a breaking change is explicitly approved and versioned.
- Add or update synthetic regression fixtures for every parser, grouping, Store, path-safety, schema, or failure-boundary change. Do not make automated tests depend on ignored local outputs.
- Keep `README.md`, `ROADMAP.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, relevant `docs/`, and module docstrings synchronized in the same change. Record important format conclusions and counterexamples in `docs/reverse-engineering-notes.md`.
- Distinguish implemented, synthetic-tested, local-game-tested, independently audited, deterministic, storage-equivalent, aggregate-validated, and event-level semantically validated claims.
- Write Git commit subjects and detailed bodies in Chinese by default. Preserve paths, commands, identifiers, schema names, and established technical terms in their original form when clearer. Use another language only when the user explicitly requests it.
- A detailed commit message must state intent, implementation, documentation/artifacts, commands/tests, real dataset or fingerprint when applicable, metrics/hashes, and remaining unverified boundaries.

## Verification

For ordinary code/documentation changes, run at minimum:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not local_game" -q
.\.venv\Scripts\python.exe -m compileall -q src tests tools
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe tools/audit_release_archives.py dist/*
```

For Store changes, also run the focused Store suites:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_store.py tests/test_store_audit.py tests/test_store_cli.py tests/test_store_equivalence.py -q
```

Use real installation checks only when `MUSEDASH_GAME_DIR` is explicitly set to a user-owned installation. Full extraction, Store audit, deterministic rebuild, and Canonical equivalence can take tens of minutes; a short process timeout is not evidence of failure. Preserve source/output paths and exact configuration in the handoff.

Every handoff must report files changed, commands and results, fingerprint/dataset and configuration, before/after evidence, generated artifact ownership, and everything still requiring independent semantic or new-version validation.
