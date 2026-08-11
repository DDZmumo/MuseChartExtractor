# Canonical Chart Schema

当前 canonical schema 版本为 `1.1.0`。它描述离线恢复的逻辑 gameplay object，不等价于“每个对象必定增加一次 combo”，也不承诺所有 raw 字段已经解释。`1.1.0` 相比 `1.0.0` 为 source provenance 增加了 `extractor_version`，并在首次发布前改用单一 raw-record table，避免每个 event 再嵌入相同的完整 MusicData payload。

## 精确数值

`.NET decimal` 来源的秒值使用十进制字符串，而不是 JSON float：

```json
{
  "time_sec": "3.1168831168831168831168831168",
  "duration_sec": "0.3896103896103896103896103896"
}
```

这样不会丢失 Odin payload 中的十进制精度。原始 decimal bits 和 hex 仍保留在 `raw`。

## Chart

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | string | 当前为 `1.1.0` |
| `chart_id` | string | Addressables StageInfo primary key |
| `song` | object | ALBUM song identity 和完整 raw metadata |
| `difficulty` | object | `_mapN` difficulty ID、显示等级 raw 值和 StageInfo raw difficulty |
| `source` | object | bundle、container、PathID、对象类型及 fingerprints |
| `timing` | object | 当前采用的时间字段、单位、BPM/delay raw 值和理解状态 |
| `event_count` | integer | `events` 长度 |
| `events` | array | 按 exact time、config ID、base raw index 稳定排列的逻辑对象 |
| `validation_status` | string | 默认继承 raw extraction；只有匹配的 M4 报告才能提升 |
| `canonicalization_status` | string | 当前为 `canonicalized-with-raw-evidence` |
| `warnings` | array[string] | 未完成语义与 source cross-check warning |
| `raw` | object | 去重但可重建的 Phase 5 证据、Phase 6 song/chart row 和可选验证报告 |

`source` 至少包含：

```text
extractor_version
bundle
bundle_sha256
container_path
path_id
object_type
payload_sha256
game_fingerprint
catalog_sha256
```

## ChartEvent

| 字段 | 类型 | 含义 |
|---|---|---|
| `index` | integer | canonical 逻辑顺序，连续从 0 开始 |
| `time_sec` | decimal string | `MusicData.configData.time` |
| `end_time_sec` | decimal string / null | 仅在已解释 duration 的类型上计算 |
| `duration_sec` | decimal string / null | 当前只解释静态 type 3 (`Press`) 和 8 (`Mul`) |
| `type_id` | integer / null | `notedata.json.type` 的原始整数 |
| `type_name` | string / null | 本地 IL2CPP `NoteType` enum 名；未知 ID 为 null |
| `type_status` | `known` / `unknown` | 是否存在静态 enum 名称 |
| `is_air` | boolean / null | 当前未完成形式化，输出 null，不猜 false |
| `extra` | object | 已理解但尚未提升为核心 schema 的字段 |
| `raw` | object | `base_raw_record_index` 与 `raw_record_indices`；引用顶层唯一 raw-record table |

已静态恢复的 `NoteType` 名称为 0–17：`None`、`Monster`、`Block`、`Press`、`Hide`、`Boss`、`Hp`、`Music`、`Mul`、`SceneChange`、`AutoOn`、`AutoOff`、`DisappearOn`、`DisappearOff`、`DisappearBossOn`、`DisappearBossOff`、`SceneHideOn`、`SceneHideOff`。

任何其他整数仍是合法数据：

```json
{
  "type_id": 99,
  "type_name": null,
  "type_status": "unknown"
}
```

## 无损边界

Canonicalization 不删除 Phase 5/6 中已经恢复的信息，但不再复制可精确重建的项目派生结构。当前输出中：

- `raw.experimental_chart.raw_records` 是唯一完整 MusicData raw-record table；
- `events[].raw.raw_record_indices` 和 `base_raw_record_index` 只引用该表，不嵌入 record body；
- `raw.experimental_chart.record_groups` 是唯一完整分组证据；
- Phase 5 的 `logical_objects` 是 `record_groups` 去除 `observed-sentinel` 后按顺序加连续 `index` 的纯派生副本，因此不再嵌入；
- `raw.layout.strategy` 为 `single-raw-record-table-v1`，并明确列出省略的派生路径；
- `reconstruct_experimental_chart(chart["raw"])` 必须与原 Phase 5 mapping 结构相等，否则 canonicalization 失败；
- `raw.indexed_song` / `raw.indexed_chart` 保留 ALBUM raw row、Addressables dependency、StageInfo metadata 和 warnings；
- 如果显式提供匹配的 M4 报告，`raw.validation_report` 与该报告结构相等；
- sentinel 不伪装成 event，但仍在 `raw.experimental_chart` 中。

validator 要求所有 event 引用与 sentinel index 的并集精确等于原始 index 集合，且每个 event 的引用必须与对应 gameplay record group 完全一致。重新出现 `events[].raw.music_data_records` 或 `events[].raw.group` 会被视为重复 payload 并导致结构验证失败。

因此 schema 的“无损”表示所有已经恢复的信息可回溯并可重建，不表示已完全理解游戏语义，也不表示未来 schema 永远不增加字段。Odin 原始字节、未知 StageInfo 字段、raw record body 和 note config 都仍保留；本次只移除可证明等价的项目派生副本。

## 生成命令

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor canonicalize `
  --raw-chart experimental/first_chart.json `
  --song-index diagnostics/song_chart_index.json `
  --validation-report diagnostics/first_chart_validation.json `
  --output experimental/first_chart_canonical.json `
  --report diagnostics/canonicalization_report.json
```

`experimental/first_chart_canonical.json` 含本机官方数据，必须保持 Git ignored，不得提交或再分发。
