# CLI Reference

本文档记录 MuseDashChartExtractor 的完整本地工作流。所有游戏资源访问均为只读；
`diagnostics/`、`experimental/`、`extracted/`、`exports/` 和
`MuseDashChartStore/` 是本地输出，不得提交或再分发。

## 准备环境

需要 Python 3.10+ 和用户自行拥有的 Windows 版 Muse Dash。

安装稳定版 GitHub Release `v0.1.0`：

```powershell
python -m pip install "https://github.com/DDZmumo/MuseChartExtractor/releases/download/v0.1.0/musedash_chart_extractor-0.1.0-py3-none-any.whl"
```

该 release 包含第一个正式资源 profile 和当时发布的 Phase 1-9 命令。第二个正式 profile、
未知 fingerprint 的完整 research batch 和独立批量审计属于当前 `main` 的 Unreleased 能力。

安装当前源码：

```powershell
git clone https://github.com/DDZmumo/MuseChartExtractor.git
cd MuseChartExtractor
python -m pip install -e ".[dev]"
```

除明确标注 `v0.1.0` 的内容外，本文档描述当前 `main`。

后续示例统一使用变量，不假定 Steam Library 位于固定盘符：

```powershell
$GameDir = "D:\SteamLibrary\steamapps\common\Muse Dash"
$Diagnostics = "diagnostics"
```

查看命令：

```powershell
musedash-chart-extractor --help
musedash-chart-extractor scan --help
```

从未安装的源码目录直接运行时，可使用：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m musedash_chart_extractor --help
```

## 完整批量提取流程

对正式支持的 fingerprint，按顺序运行：

```powershell
musedash-chart-extractor scan --game-dir $GameDir --output-dir $Diagnostics
musedash-chart-extractor probe --game-dir $GameDir --output-dir $Diagnostics
musedash-chart-extractor candidates --game-dir $GameDir --output-dir $Diagnostics
musedash-chart-extractor index --game-dir $GameDir --output "$Diagnostics/song_chart_index.json"
musedash-chart-extractor grouping-census --game-dir $GameDir --output-dir $Diagnostics
musedash-chart-extractor extract-all --game-dir $GameDir --output extracted
```

`extract-all` 会重新计算安装 fingerprint，并要求 candidates、song index、bundle inventory
和 grouping census 属于完全相同的资源集。manifest 最后原子写入；失败或 uncertain 都是显式
结果，不会被静默跳过。

长期保存推荐改用 Compact Store，而不是保留展开 JSON：

```powershell
musedash-chart-extractor extract-store `
  --game-dir $GameDir `
  --output MuseDashChartStore

musedash-chart-extractor audit-store `
  --store MuseDashChartStore `
  --game-dir $GameDir `
  --report "$Diagnostics/store_audit.json"
```

`extract-store` 复用同一 candidates/index/inventory/census 门禁，但没有
`--allow-unsupported-research`；它只接受源码注册的正式 fingerprint。

## scan - 资源 Inventory

```powershell
musedash-chart-extractor scan `
  --game-dir $GameDir `
  --output-dir $Diagnostics
```

输出：

- `diagnostics/resource_inventory.jsonl`
- `diagnostics/resource_summary.json`

每条 inventory 记录包含相对路径、大小、SHA-256 与 magic 分类。summary 的组合
fingerprint 由稳定排序的相对路径、文件大小和各文件 SHA-256 生成，与安装盘符无关。

## probe - Unity / Addressables Metadata

```powershell
musedash-chart-extractor probe `
  --game-dir $GameDir `
  --output-dir $Diagnostics
```

输出：

- `diagnostics/bundle_inventory.jsonl`
- `diagnostics/serialized_file_inventory.jsonl`
- `diagnostics/object_type_summary.json`
- `diagnostics/addressables_index.json`

probe 只枚举 Unity metadata、对象类型、名称、容器和依赖关系，不递归导出对象内容。
正式完整 probe 不应使用 `--max-sources`；该选项仅用于开发期局部探测。

## candidates - StageInfo 候选

```powershell
musedash-chart-extractor candidates `
  --game-dir $GameDir `
  --output-dir $Diagnostics
```

输出 `diagnostics/chart_candidates.jsonl`。每条记录包含：

- candidate rank、score、evidence 与 counterevidence；
- source 相对路径、大小与 SHA-256；
- container path、PathID、Unity object type 与 byte size；
- MonoScript class、namespace、assembly；
- StageInfo metadata 和 Odin payload 的大小/哈希，不包含 `SerializedBytes`。

score 表示已观察结构信号的比例，不是“属于谱面的概率”。

## inspect-stageinfo - 严格结构探针

针对 candidates 中的一条真实记录运行，下面仅展示已验证样本：

```powershell
musedash-chart-extractor inspect-stageinfo `
  --game-dir $GameDir `
  --source "MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/music_urban_magic_assets_all.bundle" `
  --path-id 8668625138739021960 `
  --output-dir $Diagnostics
```

输出 `diagnostics/field_hypotheses.jsonl`。解析器只接受实际字节已证明的 Odin tag、
类型和字段顺序；未知 tag、截断、非法长度、TypeID、计数或节点失配会报告 offset 与字段
路径后停止该 candidate，不会猜测长度后继续。

## extract - 单张本地实验恢复

```powershell
musedash-chart-extractor extract `
  --game-dir $GameDir `
  --source "MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/music_urban_magic_assets_all.bundle" `
  --path-id 8668625138739021960 `
  --output experimental/first_chart.json
```

这是 Phase 5 本地研究输出，不是稳定公开格式。它保留完整 StageInfo envelope、原始
Odin records、实际使用的 note config、unknown 字段和 provenance。原始 record index
不会丢失。无媒体内容的验证摘要应写入 `diagnostics/first_chart_validation.json`。

## index - Song / Difficulty 关系

```powershell
musedash-chart-extractor index `
  --game-dir $GameDir `
  --output "$Diagnostics/song_chart_index.json"
```

索引器使用：

- Addressables StageInfo primary key 作为 `chart_id`；
- ALBUM `uid` 作为 `song_id`；
- chart 名末尾 `_mapN` 作为 `difficulty_id`；
- 唯一 bundle dependency 与 source SHA 作为来源校验。

真实 ALBUM TextAsset 包含注释和尾逗号，因此先以 JSON5 读取，再做严格字段验证。
无法连接的 chart 保留在 `unresolved_charts`。

## canonicalize - Canonical Chart 1.1

```powershell
musedash-chart-extractor canonicalize `
  --raw-chart experimental/first_chart.json `
  --song-index "$Diagnostics/song_chart_index.json" `
  --validation-report "$Diagnostics/first_chart_validation.json" `
  --output experimental/first_chart_canonical.json `
  --report "$Diagnostics/canonicalization_report.json"
```

schema `1.1.0` 使用一张 `raw.experimental_chart.raw_records` 表；event 仅保存
`base_raw_record_index` 和 `raw_record_indices`，不再重复嵌入 record body 或派生
`logical_objects`。转换前会执行 exact reconstruction checks，unknown/raw 信息继续保留。
详见 [schema.md](schema.md)。

## validate - 结构与独立参考验证

```powershell
musedash-chart-extractor validate `
  --chart experimental/urban_magic_map1_canonical.json `
  --chart experimental/urban_magic_map2_canonical.json `
  --chart experimental/first_chart_canonical.json `
  --game-dir $GameDir `
  --reference-file "$Diagnostics/validation_references.json" `
  --output "$Diagnostics/validation_report.json" `
  --markdown-output "$Diagnostics/validation_report.md"
```

验证器检查 exact Decimal、事件顺序、duration/end、source SHA-256，以及 raw record
index 是否由 gameplay event 与 observed sentinel 精确覆盖。当前独立参考主要是 aggregate
combo，因此以下逐事件分类会明确为 `not_compared`，不会用数量一致冒充逐事件一致：

```text
matched
missing_offline
extra_offline
timing_delta
type_mismatch
lane_mismatch
duration_delta
```

如有独立、完整且已经按 logical index 对齐的事件流，可以在同一 reference row 中加入：

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
      {"index": 0, "time_sec": "1.250", "type_id": 1, "is_air": false}
    ]
  }
}
```

上例是 synthetic 格式示例。实际 reference 必须列出从 0 开始、
无缺口的完整事件序列；比较按 index 进行，不做贪心时间对齐。`time_sec` 必填，其他字段只在
明确提供时比较，省略类别保持 `not_compared`。事件 reference 必须保留独立来源 provenance，
含官方衍生事件流的文件只能留在本地，不能提交或再分发。

详见 [validation.md](validation.md)。

## grouping-census - 全库分组门禁

```powershell
musedash-chart-extractor grouping-census `
  --game-dir $GameDir `
  --candidate-file "$Diagnostics/chart_candidates.jsonl" `
  --output-dir $Diagnostics
```

输出：

- `diagnostics/grouping_census.jsonl`
- `diagnostics/grouping_census_summary.json`

census 对每个 candidate 严格解析并分组，但不导出完整事件。正式 batch 要求
`complete=true`，并要求每个 candidate 都得到明确 parsed/grouped 状态。

当前证据支持的 grouping 规则包括：

- `configData.id < 0` 的记录逐条独立；
- neutral base 的两个 long-press state flag 均为 false；
- 不使用已被真实反例推翻的 `endIndex == 0` 作为通用 base 条件。

## extract-all - 全量 Canonical 输出

```powershell
musedash-chart-extractor extract-all `
  --game-dir $GameDir `
  --output extracted
```

输出结构：

```text
extracted/
├── manifest.json
└── charts/
    └── <song_id>/
        └── <chart_id>.json
```

manifest 分类每个 candidate 的 `success`、`failed` 或 `uncertain`，并记录 source、
文件大小、SHA-256、schema、event/raw counts 与 warnings。当前 fingerprint 的实盘结果是
2,330 success、1 uncertain、0 failed；manifest 总 logical events 为 1,204,898，其中正式
文件导出 1,204,824 events。

当前 2,330 个文件约 13.1 GiB。相同版本的重复验证应复用同一输出目录原地运行；manifest
包含逐文件路径、大小和 SHA-256，可在不保留双份文件树的情况下证明确定性。

## extract-store - Compact Odin Store

```powershell
musedash-chart-extractor extract-store `
  --game-dir $GameDir `
  --output MuseDashChartStore `
  --candidate-file "$Diagnostics/chart_candidates.jsonl" `
  --song-index "$Diagnostics/song_chart_index.json" `
  --bundle-inventory "$Diagnostics/bundle_inventory.jsonl" `
  --grouping-census-summary "$Diagnostics/grouping_census_summary.json"
```

输出：

```text
MuseDashChartStore/
├── store.json
├── index.sqlite3
├── payloads/
│   └── sha256/<first-two-hex>/<full-sha256>.odin
└── audit/
    └── store_audit.json  # audit-store 生成；也可写到 diagnostics
```

每个 `.odin` 逐字节等于 StageInfo 的原始 `SerializedBytes`。文件名使用 payload
SHA-256，重复内容只保存一次；SQLite 不保存 BLOB。writer 会：

- 重新计算并要求正式支持的完整 installation fingerprint；
- 要求 candidate、song index、bundle inventory 和完整 grouping census 同属该 fingerprint；
- 核对 bundle、PathID、对象类型、payload size/SHA；
- 对每个 payload 严格解析到 EOF，并重算 grouping counts；
- 完整保留去掉 `SerializedBytes` 后的 StageInfo envelope；
- 将全局 notedata 保存一次，chart 只保存 UID 引用；
- 显式记录每个 success、uncertain 或 failed candidate；
- 使用 `.building`、临时文件和 SQLite 事务，清理前拒绝 `.staging` 内任何 symlink/junction，
  并最后原子发布 `store.json`。

同一目录重跑会逐个验证并复用已存在的内容寻址 payload，不创建第二份 Store。stale 或
extra payload 不会被自动隐藏；writer 拒绝发布不完整结果，`audit-store` 也会将其报告为失败。

## audit-store - 独立 Store 审计

```powershell
musedash-chart-extractor audit-store `
  --store MuseDashChartStore `
  --game-dir $GameDir `
  --report "$Diagnostics/store_audit.json"
```

`--game-dir` 可省略；提供时会额外重新校验 source bundle SHA、PathID、对象类型、原始
payload 和 StageInfo envelope。无论是否提供，审计器都会独立执行：

- `PRAGMA integrity_check` 和 `foreign_key_check`；
- manifest、SQLite metadata、logical digest、candidate/chart/source/payload ID 集合；
- payload exact file set、规范路径、symlink、size 与 SHA-256；
- 每个 Odin stream 的严格 EOF parse；
- StageInfo 不重复保存 `SerializedBytes`，且保留 `SerializedFormat`、`sceneEvents`；
- raw record、record group、logical event、sentinel counts 的重新计算。

报告只包含 metadata、hash、count 和最多 10 条 mismatch 摘要，不包含事件或 payload。
命令在零 mismatch 时返回 0，审计不通过时返回 1，输入/IO/domain 错误返回 2。
报告可以写到 Store 外的 diagnostics；若写在 Store 内，则只能位于 `audit/`，不能覆盖
`store.json`、`index.sqlite3`、payload 或构建暂存路径。

当前 fingerprint 的实盘 Store 为 1,101,577,861 bytes（含审计报告），是迁移前
14,088,644,042-byte JSON 基线的 7.8189%。2,331 个 payload 总计 1,053,670,885
bytes；2,330 success、1 uncertain、0 failed。完整审计 13 类 mismatch 均为 0。

## 独立批量审计

该工具仅位于当前 `main`（Unreleased）的源码仓库中，`v0.1.0` 不包含它。请在仓库根目录运行：

```powershell
python tools/audit_extracted_batch.py `
  --output-dir extracted `
  --report "$Diagnostics/batch_audit.json"
```

审计器不会信任 manifest 自报状态。它会重新计算 Phase/M8 gate、candidate/source/event
counts、status aggregates 和唯一 chart IDs，并重新打开每个 successful 文件，核对：

- path、size 与 SHA-256；
- canonical schema 与 chart identity；
- 单一 raw table 和 index 类型/唯一性；
- gameplay/sentinel groups 与 base/member 关系；
- event references 与 groups 的逐项一致；
- event + sentinel 对 raw index set 的精确闭包；
- 禁止残留重复 `logical_objects` payload。

报告只包含 fingerprint、计数、哈希与 mismatch 摘要，可在删除旧版本本地输出后保留。

## 未知 Fingerprint 研究

未知安装默认只能安全运行 `scan` 与 `probe`。正式提取会抛出
`UnknownGameVersionError`，而不是静默复用已知 parser。

`v0.1.0` 只为部分研究命令提供 opt-in；未知 fingerprint 的完整
`extract-all --allow-unsupported-research` 流程仅适用于当前 `main`（Unreleased）。

确需继续研究时，`candidates`、`inspect-stageinfo`、`extract`、`index`、
`grouping-census` 和 `extract-all` 提供显式 `--allow-unsupported-research`。这不会降低
同 fingerprint candidates/index/census 的完整门禁，也不会修改正式支持表。research batch
manifest 会写入：

```json
{
  "profile_support": {
    "formal_support": false,
    "status": "unsupported-fingerprint-research"
  }
}
```

只有完整 Phase 1–9 证据、全量审计、反例记录和源码 review 完成后，才能通过代码变更
注册新 profile。详见 [supported-versions.md](supported-versions.md)。

## Python API

```python
from musedash_chart_extractor import ChartStore, CsvExporter, JsonExporter

with ChartStore.open("MuseDashChartStore") as store:
    refs = list(store.iter_charts())
    chart = store.load_chart("urban_magic_map3")

JsonExporter(indent=None).export(chart, "exports/urban_magic_map3.json")
CsvExporter().export(chart, "exports/urban_magic_map3.csv")
```

`iter_charts()` 只读 SQLite metadata，不解析全部 payload；`read_payload(chart_id)` 返回经过
size/SHA 校验的原始 bytes；`load_chart(chart_id)` 才解析一张 Odin stream、连接共享
note config/song index 并生成 Canonical `1.1.0`。没有无界缓存。

`MuseDashInstallation.extract_store()` 是 CLI 的正式 fingerprint-gated facade；旧的
`extract_charts()` 仍可按需生成完整 JSON 树，但不再是推荐的长期数据库格式。
`JsonExporter` 保留完整 model；`CsvExporter` 是扁平事件视图，不替代内部 raw/unknown 数据。

## 本地测试

不需要游戏资源：

```powershell
python -m pytest -m "not local_game" -q
```

显式启用只读实盘测试：

```powershell
$env:MUSEDASH_GAME_DIR = $GameDir
$env:MUSEDASH_EXTRACTED_DIR = (Resolve-Path extracted).Path
python -m pytest -m local_game -q
```

CI 不拥有、下载或上传任何 Muse Dash 资源。
