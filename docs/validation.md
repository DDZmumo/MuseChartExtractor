# Validation Scope

Phase 8 validation deliberately separates three questions:

1. **Structural**: exact decimal values parse and times are ordered,
   durations and end times agree, source bundle SHA-256 matches, and every retained raw
   record index is accounted for exactly once by an event or observed sentinel.
2. **Semantic summary**: type, air/ground, hold, multi, unknown ratios, and the pinned
   static `addCombo` projection are reported without hard event-count limits.
3. **Independent reference**: the current references contain only final combo counts.
   They do not contain an event stream.

Consequently, `M7-achieved` means that multiple charts passed structural and source
checks and their aggregate references matched. It does **not** mean that every event's
time, type, lane, air/ground status, or duration was independently compared.

The JSON and Markdown reports always include these event-level categories:

```text
matched
missing_offline
extra_offline
timing_delta
type_mismatch
lane_mismatch
duration_delta
```

Until an event-level independent reference is explicitly supplied, every category is
`not_compared`. The production extractor does not depend on a runtime export, Mod,
injection, or game process.

## Optional indexed event reference

Current source accepts an optional event stream inside each aggregate reference:

```json
{
  "chart_id": "example_map1",
  "expected_combo": 123,
  "source": {"kind": "visible-final-combo"},
  "event_reference": {
    "schema_version": "event-reference-v1",
    "scope": "complete-indexed-sequence",
    "source": {"kind": "independent-event-export"},
    "time_tolerance_sec": "0.010",
    "duration_tolerance_sec": "0.010",
    "events": [
      {
        "index": 0,
        "time_sec": "1.250",
        "type_id": 1,
        "is_air": false,
        "duration_sec": null
      }
    ]
  }
}
```

The reference must describe the complete sequence with contiguous integer indices
starting at zero. `time_sec` is required and uses an exact decimal string. `type_id`,
`is_air`, and `duration_sec` are optional per event; a category is compared only when
the reference supplies that field somewhere. Omitted categories remain
`not_compared` rather than being inferred from canonical output.

Comparison is deterministic by explicit index, not by greedy timestamp alignment.
This makes missing and extra events visible instead of allowing a matcher to hide
them. Time and duration tolerances are non-negative exact decimal strings. Reports
include counts and at most ten bounded details per category; `matched` means that all
fields supplied for that indexed row agree within tolerance.

`event_reference.source.kind` is required so synthetic fixtures, manual review,
runtime exports, and other independent sources cannot be confused. Supplying a
partial-field or synthetic reference does not establish full semantic accuracy. Reference
files containing official-derived event streams are local research artifacts and
must not be committed or redistributed.

The retained real validation references currently contain aggregate combo counts
only. Event-reference support is therefore implemented and synthetic-tested, but it
does not retroactively advance the project's M7 evidence claim.

For a complete schema `1.1.0` batch, `tools/audit_extracted_batch.py`
independently reopens every successful file. It checks the manifest path,
size, and SHA-256; the single raw-table layout; raw index uniqueness; gameplay
and sentinel groups; event/base references; and exact set equality between all
event/sentinel references and the retained raw table. It also recomputes the
Phase/M8 state, row counts, status aggregates, phase gate, source count, event
count, and unique chart IDs instead of trusting the manifest header. This proves
structural closure and deterministic storage, not gameplay semantics. The
report is metadata-only and can be kept after deleting local official-derived
outputs.

Finite negative `configData.time` values are legal raw evidence. They occur in the
current installation (observed `-0.482` and `-0.232` pre-roll values) and are retained
with `negative_raw_time_preserved`; no offset is invented to force them to zero.
