# Contributing

MuseDashChartExtractor is a read-only, offline reverse-engineering project.
Changes must preserve the Disk-to-Parser boundary described in `ROADMAP.md`.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -m "not local_game" -q
```

Linux and macOS contributors can use the corresponding `bin/python` path.

## Evidence requirements

- Link format conclusions to a relative source path, PathID, object type,
  field, and observed value or bounded experiment.
- Add important findings and counterexamples to
  `docs/reverse-engineering-notes.md`.
- Do not generalize a rule from one chart when a full census is required.
- Unknown tags, fields, types, and versions must fail loudly or remain explicit.
- Keep game access read-only. Runtime hooks, mods, injection, and memory reads
  are outside project scope.
- Keep full-library work Store-first. Do not run `extract-all` as a routine
  validation step; it requires explicit `--allow-expanded-json` because it can
  materialize about 14 GiB of official-derived JSON. Prefer Store rebuild,
  source-aware audit, and Store-only `digest-store`.

## Tests and fixtures

Use synthetic fixtures for unit tests. Never commit official charts, bundles,
audio, textures, DLC data, full dumps, or generated files from `extracted/`
or `exports/`. Never commit `MuseDashChartStore/`, `.odin`, Store SQLite files,
payload directories, or Store manifests/audit outputs derived from a game.

Tests requiring a user-owned installation use the `local_game` marker:

```powershell
$env:MUSEDASH_GAME_DIR = "E:\SteamLibrary\steamapps\common\Muse Dash"
.\.venv\Scripts\python.exe -m pytest -m local_game -q
```

Before opening a change, run:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not local_game" -q
.\.venv\Scripts\python.exe -m compileall -q src tests tools
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe tools/audit_release_archives.py dist/*
```

For Store changes, also run targeted synthetic Store tests. Real full-library
acceptance should reuse one `MuseDashChartStore/` directory, then run
`audit-store` and `digest-store` to unique metadata-only reports before and after
a second rebuild. Do not create an expanded Canonical corpus unless the user has
approved a specific independent need that Store streaming cannot cover.

Inspect wheel and sdist contents. They must not contain `diagnostics/`,
`experimental/`, `extracted/`, `exports/`, or any game-derived event data.
They must also exclude `MuseDashChartStore/`, payload directories, `.odin`,
`.sqlite3`, `store.json`, and `store_audit.json`.
