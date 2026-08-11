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

Finite negative `configData.time` values are legal raw evidence. They occur in the
current installation (observed `-0.482` and `-0.232` pre-roll values) and are retained
with `negative_raw_time_preserved`; no offset is invented to force them to zero.
