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

## Tests and fixtures

Use synthetic fixtures for unit tests. Never commit official charts, bundles,
audio, textures, DLC data, full dumps, or generated files from `extracted/`
or `exports/`.

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

Inspect wheel and sdist contents. They must not contain `diagnostics/`,
`experimental/`, `extracted/`, `exports/`, or any game-derived event data.
