# MuseDashChartExtractor Roadmap

> 独立、只读、离线的 Muse Dash 官方谱面提取项目路线图
> 目标：从用户本机合法安装的 Windows 版 Muse Dash 资源中，定位、解析并导出官方谱面数据。
> 项目不依赖 MusePlay，不实现 AutoPlay，不读取运行时内存，不使用 ModLoader，不修改游戏文件。

---

## 0. 项目总目标

本项目最终要解决的问题只有一个：

> **Muse Dash 当前版本的官方谱面，在磁盘上究竟以什么形式存在，以及如何稳定、可验证、可批量地离线提取成开放的结构化数据。**

最终工作流：

```text
用户本机 Muse Dash 安装目录
        ↓
资源扫描
        ↓
Unity / Addressables 资源解析
        ↓
谱面候选发现
        ↓
谱面格式恢复
        ↓
歌曲 / 难度索引恢复
        ↓
统一内部 Chart Model
        ↓
验证
        ↓
JSON / CSV / 第三方自定义 Exporter
```

项目核心只负责：

- 读取本地游戏资源
- 定位谱面
- 解析谱面
- 验证谱面
- 导出标准结构化数据
- 提供通用转换接口

项目核心**不负责**：

- MusePlay
- YOLO
- AutoPlay
- 键盘输入
- 游戏控制
- 运行时内存读取
- DLL 注入
- MelonLoader
- Harmony Hook
- 作弊功能
- 官方谱面或资源再分发

---

# 1. 不可破坏的设计原则

后续所有实现、PR 和重构都必须服从以下原则。

## 1.1 只读

工具只能读取用户指定的 Muse Dash 安装目录。

禁止：

- 修改 AssetBundle
- 修改 catalog
- 覆盖游戏资源
- 修改配置以改变游戏行为
- 自动写入游戏目录

默认输出目录必须位于项目工作目录或用户显式指定目录。

---

## 1.2 完全离线

谱面提取不需要启动 Muse Dash。

目标路径：

```text
disk resources
    ↓
offline parser
    ↓
chart data
```

不是：

```text
game runtime
    ↓
GetMusicData()
    ↓
chart data
```

---

## 1.3 不假设格式

任何结论必须来自实际文件证据。

禁止先假定：

- 谱面一定是 JSON
- 谱面一定是 TextAsset
- 谱面一定叫 `MusicData`
- 所有 type 永远是 1–8
- AssetBundle 一定加密
- AssetBundle 一定没加密
- Addressables 一定包含直接可读名称
- 当前版本与历史版本格式一致

原则：

> **先观察，再建模。**

---

## 1.4 Fail Loudly

未知版本、未知字段、未知类型、解析异常不能被静默吞掉。

不允许：

```python
try:
    ...
except Exception:
    pass
```

解析失败必须产生：

```text
status
reason
source file
object/path id
exception
diagnostic context
```

---

## 1.5 Provenance First

任何成功解析出来的数据，都必须知道它来自哪里。

每张谱至少保存：

```text
source file
bundle
object/path id
asset name
object type
resource fingerprint
extractor version
```

否则不能算“稳定解析”。

---

# 2. 项目阶段总览

完整路线划分为 11 个阶段（Phase 0–10）：

```text
Phase 0   Repository Bootstrap
Phase 1   Resource Inventory
Phase 2   Unity / Addressables Recon
Phase 3   Chart Candidate Discovery
Phase 4   Serialized Structure Recovery
Phase 5   First Real Chart Extraction
Phase 6   Song / Difficulty Index Recovery
Phase 7   Canonical Chart Model
Phase 8   Validation & Cross-checking
Phase 9   Batch Extraction
Phase 10  Public API / Exporter / Open-source Hardening
```

一个阶段没有通过验收门槛，不进入下一阶段。

当前源码已在两个 exact fingerprint 上完成 Phase 1–9：
`sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5`
和 `sha256:d9108183177ac7c4821b466d28e0920d8a4a9bcd490a0edde956be3681233222`。
第一个 fingerprint 还已通过 Phase 0–10，M0–M9 已达到。`v0.1.0` 对应 revision `9158640`；
main push 与 tag 的两次真实 GitHub Actions 均通过完整测试/package 门禁，tag
workflow 随后创建了包含已审计 wheel/sdist 的公共 GitHub Release。该发布物只含第一个
profile；第二个 profile 是当前源码的 Unreleased 变更。这个边界不表示其他游戏 fingerprint
自动兼容，不把 M7 的 aggregate 验证夸大为全库逐事件 100% 准确。

---

# Phase 0 — Repository Bootstrap

## 目标

建立最小、可测试、可扩展但不过度设计的 Python 项目。

## 必做任务

建立：

```text
musedash-chart-extractor/
├── pyproject.toml
├── README.md
├── LICENSE
├── ROADMAP.md
├── src/
│   └── musedash_chart_extractor/
│       ├── __init__.py
│       ├── cli.py
│       ├── scanner.py
│       └── diagnostics.py
├── tests/
└── docs/
```

首批依赖尽量少：

```text
UnityPy
rich / typer（二选一或都不用）
pytest
```

不要一开始加入：

- GUI
- Web UI
- 数据库
- ML 框架
- Mod 依赖
- 大型插件系统

## CLI 最小骨架

```text
musedash-chart-extractor scan
musedash-chart-extractor probe
```

## 产物

- 可安装 Python package
- CLI 可正常启动
- `--game-dir` 参数可用
- 对不存在目录给出明确错误

## 验收门槛

以下命令成功：

```bash
python -m musedash_chart_extractor --help
```

且：

```bash
python -m musedash_chart_extractor scan --game-dir "不存在目录"
```

必须明确失败，而不是 traceback 淹没用户。

---

# Phase 1 — Resource Inventory

## 目标

搞清楚当前 Muse Dash Windows 安装目录的真实资源布局。

**这一阶段不解析谱面。**

## 重点目录

优先调查但不局限于：

```text
MuseDash_Data/
MuseDash_Data/StreamingAssets/
MuseDash_Data/StreamingAssets/aa/
MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/
```

递归发现：

```text
*.bundle
*.assets
*.resource
*.resS
catalog.json
settings.json
global-metadata.dat
GameAssembly.dll
```

以及所有未知但体积/文件头可疑的文件。

## Scanner 输出

每个文件记录：

```json
{
  "relative_path": "...",
  "size": 123456,
  "suffix": ".bundle",
  "magic": "UnityFS",
  "sha256": "...",
  "category": "unity_bundle_candidate"
}
```

## 文件头识别

至少识别：

```text
UnityFS
UnityWeb
UnityRaw
JSON
PE
unknown
```

不要把压缩数据直接叫“加密”。

## 输出

```text
diagnostics/resource_inventory.jsonl
diagnostics/resource_summary.json
```

## 必须统计

- 总文件数
- 总大小
- bundle 数量
- UnityFS 数量
- catalog 数量
- assets 数量
- unknown 大文件数量

## 验收门槛

能够针对用户真实安装目录生成稳定 inventory。

重复运行时，相同游戏版本产生基本一致结果。

---

# Phase 2 — Unity / Addressables Recon

## 目标

确认 Muse Dash 当前版本到底使用哪些 Unity 资源机制，以及哪些文件可以被标准工具直接解析。

---

## 2.1 AssetBundle 探测

用 UnityPy 尝试打开扫描发现的 Bundle。

对每个 bundle 记录：

```text
parseable
unity version
container count
object count
object type distribution
```

例如：

```json
{
  "bundle": "...",
  "parseable": true,
  "objects": {
    "MonoBehaviour": 481,
    "TextAsset": 12,
    "Texture2D": 37
  }
}
```

---

## 2.2 对象枚举

重点统计：

```text
TextAsset
MonoBehaviour
ScriptableObject
AssetBundle
GameObject
AudioClip
Texture2D
```

这一阶段只做 metadata inventory，不做大规模完整 dump。

---

## 2.3 Addressables

如果存在：

```text
catalog.json
```

解析：

```text
primary key
internal id
provider
resource type
dependencies
bundle location
```

目标建立：

```text
logical resource
    ↓
bundle
    ↓
asset
```

的可查询关系。

---

## 2.4 资源名称搜索

仅作为线索，搜索：

```text
music
song
stage
chart
map
level
difficulty
note
battle
```

禁止把“没搜到字符串”等价为“资源不存在”。

---

## 输出

```text
diagnostics/bundle_inventory.jsonl
diagnostics/object_type_summary.json
diagnostics/addressables_index.json
```

---

## 验收门槛

必须回答：

1. 当前 Muse Dash 安装是否存在标准 Unity Bundle；
2. UnityPy 能否直接打开其中一部分或全部；
3. 是否存在 Addressables；
4. 哪些 bundle 类型最值得继续分析。

在回答不了这四个问题前，不进入下一阶段。

---

# Phase 3 — Chart Candidate Discovery

## 目标

从大量 Unity 对象中找出最可能是“谱面原始数据”的对象。

这一阶段仍然**不要求已经知道谱面格式**。

---

## 3.1 TextAsset 探测

对 TextAsset 判断：

```text
UTF-8 text
JSON-like
CSV-like
XML-like
binary
compressed
unknown
```

二进制 TextAsset 仅记录：

```text
size
magic
entropy
printable strings
limited hex preview
```

禁止把整文件打印进日志。

---

## 3.2 MonoBehaviour / ScriptableObject

如果有 TypeTree：

递归读取字段结构。

重点寻找模式，而不是只找字段名。

高价值特征：

```text
大型重复数组
递增的数值字段
小整数 enum-like 字段
bool 字段
持续时间字段
对其他 music/stage 对象的引用
```

---

## 3.3 字段名只是加分项

可关注：

```text
tick
time
note
type
length
duration
isAir
isMul
bpm
music
stage
difficulty
```

但这些字段缺失时不能直接放弃。

---

## 3.4 Candidate Scoring

实现候选评分。

示例：

```text
+0.25 large repeated array
+0.20 monotonic numeric field
+0.15 enum-like small integers
+0.10 booleans in repeated records
+0.10 duration-like values
+0.10 music/stage references
+0.10 suggestive names
```

最终生成：

```json
{
  "source": "...",
  "path_id": 12345,
  "object_type": "MonoBehaviour",
  "score": 0.86,
  "evidence": [...]
}
```

---

## 输出

```text
diagnostics/chart_candidates.jsonl
```

---

## 验收门槛

必须得到一组**有排序、有证据**的谱面候选。

禁止通过人工直觉直接 hardcode 某个 PathID 为谱面。

---

# Phase 4 — Serialized Structure Recovery

## 目标

恢复候选对象的实际字段语义。

---

## 4.1 优先走 TypeTree

如果 UnityPy 能读：

```python
obj.read_typetree()
```

则优先分析 TypeTree。

建立：

```text
field path
value type
sample values
distribution
```

---

## 4.2 无 TypeTree 时

如果候选对象无法恢复字段名：

进入静态 Il2Cpp metadata 调查。

检查：

```text
GameAssembly.dll
global-metadata.dat
```

目标仅是恢复：

```text
class
field
enum
serialization structure
```

不是运行时 Hook。

---

## 4.3 允许外部静态工具

如 Python 无法独立解析 Il2Cpp metadata，可以使用外部静态分析工具作为**开发期辅助**。

要求：

- 工具独立安装
- 不把第三方二进制直接塞进仓库
- 检查许可证
- 保存分析结果而不是依赖运行时注入

---

## 4.4 ManiaInMuse 作为结构参考

可以研究：

```text
https://github.com/SanwuQian/ManiaInMuse
```

用途仅限：

> 了解 Muse Dash 运行时最终有哪些谱面语义。

例如可作为调查线索的概念：

```text
time / tick
type
air / ground
duration
multi
BPM
```

但禁止：

```text
disk format == runtime MusicData
```

这种未经验证的假设。

真正要找的是：

```text
disk chart source
    ↓
game loader
    ↓
runtime music data
```

---

## 输出

```text
docs/reverse-engineering-notes.md
diagnostics/field_hypotheses.jsonl
```

每个字段假设必须写：

```text
confidence
evidence
counter-evidence
```

---

## 验收门槛

至少对一个高分候选对象恢复出：

- 一个时间相关字段
- 一个事件类型/行为相关字段或结构
- 一个事件数组结构

否则不能宣称已经找到谱面。

---

# Phase 5 — First Real Chart Extraction

## 目标

**从磁盘资源中成功恢复一张真实官方谱面。**

这是整个项目的第一个关键里程碑。

---

## 5.1 只选一首

不要一开始批量。

选择一张：

- 用户熟悉
- 容易在游戏里验证
- 事件数量适中

的官方谱面。

---

## 5.2 最小事件模型

第一版只要求：

```text
event index
time
raw type
raw fields
```

能恢复更多再加：

```text
air/ground
duration
end time
multi
```

---

## 5.3 不要伪造字段

无法确定：

```json
"is_air": null
```

而不是：

```json
"is_air": false
```

---

## 5.4 临时输出

```text
experimental/first_chart.json
```

此时暂时不承诺稳定 schema。

---

## 5.5 实际验证

人工选若干事件：

```text
开始
中段
密集段
长按
特殊对象
结尾
```

与真实游戏画面/录像比对。

至少确认：

```text
事件时间
事件顺序
大致事件数量
特殊事件存在性
```

---

## 关键验收门槛

只有满足以下条件，才能进入 Phase 6：

> **能够从未启动游戏的本地资源中，解析出至少一张官方谱面的真实事件序列，并能用实际游戏画面证明其对应关系。**

这一阶段失败时：

- 不重构
- 不做 GUI
- 不做批量
- 不做插件系统

继续回到 Phase 3/4 找格式。

---

# Phase 6 — Song / Difficulty Index Recovery

## 目标

从“能解析一张谱”升级到“知道这张谱属于哪首歌、哪个难度”。

---

## 6.1 稳定 ID 优先

先恢复：

```text
song_id
chart_id
difficulty_id
```

显示名可以后补。

---

## 6.2 建立关系

目标：

```text
song metadata
    ↕
difficulty
    ↕
chart asset
```

---

## 6.3 可选 metadata

尽可能恢复：

```text
song title
artist
difficulty name
difficulty level
BPM
```

但不是第一优先级。

---

## 输出

```text
diagnostics/song_chart_index.json
```

---

## 验收门槛

至少连续正确识别：

- 3 首不同歌曲
- 每首至少 2 个难度

且不能靠手写 PathID 映射。

---

# Phase 7 — Canonical Chart Model

## 目标

在已经理解真实格式后，再定义稳定内部数据模型。

---

## 7.1 Chart

建议：

```text
Chart
├── schema_version
├── song
├── difficulty
├── source
├── timing
└── events[]
```

---

## 7.2 ChartEvent

建议：

```text
index
time_sec
end_time_sec
duration_sec
type_id
type_name
is_air
extra
raw
```

其中：

```text
extra
```

用于保存已理解但非核心字段。

```text
raw
```

用于保留版本相关或未知字段。

---

## 7.3 Unknown 永远合法

例如：

```json
{
  "type_id": 9,
  "type_name": null,
  "raw": {
    "type": 9
  }
}
```

未知类型不能被丢弃。

---

## 7.4 Source Provenance

至少：

```text
bundle
asset/path id
object type
resource fingerprint
game fingerprint
```

---

## 输出

```text
src/.../charts/models.py
docs/schema.md
```

---

## 验收门槛

Phase 5/6 已成功解析的谱面能够无损转换为 Canonical Model。

“无损”的含义：

> 已解析出的信息不能因为统一模型而消失。

允许把可精确重建的项目派生副本替换为引用，但必须有正式不变量和测试证明。
当前 `single-raw-record-table-v1` 布局只保存一份完整 raw records 与 record
groups；event 使用原始 record index 引用，Phase 5 `logical_objects` 可从
非 sentinel record groups 精确重建。未知字段、原始字节和官方数据不得借此
优化丢弃。

---

# Phase 8 — Validation & Cross-checking

## 目标

建立系统化验证，避免“看起来像谱面”的错误结果进入正式输出。

---

## 8.1 Structural Validation

检查：

```text
time 是有限精确值；在时间原点未恢复前，负 raw pre-roll time 保留并 warning
duration >= 0
end_time >= time
event ordering
reasonable event count
source exists
```

---

## 8.2 Semantic Validation

检查：

```text
type distribution
air/ground distribution
hold duration
multi duration
unknown type ratio
```

不能使用过于严格的 hardcode，避免未来版本直接失效。

---

## 8.3 Reference Cross-check

允许将 ManiaInMuse 作为**独立验证参考**：

```text
offline extractor output
vs
ManiaInMuse runtime export
```

比较：

```text
event count
time
type
air/ground
duration
```

但：

> 正式 extractor 运行不得依赖 ManiaInMuse。

---

## 8.4 差异报告

输出：

```text
matched
missing_offline
extra_offline
timing_delta
type_mismatch
lane_mismatch
duration_delta
```

---

## 输出

```text
validate command
diagnostics/validation_report.json
diagnostics/validation_report.md
```

---

## 验收门槛

至少对多张谱完成验证，并能够解释主要差异来源。

在没有足够证据时，不宣称 100% 精确。

---

# Phase 9 — Batch Extraction

## 目标

从单谱解析升级到完整本地库批量导出。

---

## CLI

```bash
musedash-chart-extractor extract-all \
  --game-dir "..." \
  --output extracted
```

---

## 输出结构

```text
extracted/
├── manifest.json
├── charts/
│   ├── <song_id>/
│   │   ├── <chart_id>.json
│   │   └── ...
│   └── ...
└── diagnostics/
```

实际资源只证明了稳定的 `chart_id` 和 `_mapN` difficulty slot，尚未证明
`easy/hard/master/hidden` 是全库稳定命名，因此输出文件不得猜测这些名称。

---

## 批量前结构 Census

在循环单谱提取逻辑前，必须对全部已识别 StageInfo 做 metadata-only census：

```text
strict raw parse status
grouping shape / family
repeated or sentinel config ids
base/state record signals
logical time ordering
failure reason
```

已在少量谱面成立的分组规律不得直接推广到全库。Odin raw parse 成功但逻辑分组
尚未恢复时，状态应为 `uncertain`，不能伪装成 `success`，也不能静默消失。

---

## Manifest

必须记录：

```text
game fingerprint
extractor version
schema version
song id
chart id
difficulty
event count
status
warnings
source
raw_parse_status
canonical_status
validation_status
```

---

## Failed 也是结果

解析失败不能消失。

例如：

```json
{
  "chart_id": "...",
  "status": "failed",
  "reason": "unsupported serialized structure"
}
```

---

## 幂等性

同一游戏版本、同一 extractor 版本重复运行：

- 输出排序稳定
- ID 稳定
- 不产生随机差异

---

## 验收门槛

能够对本地全部已识别谱面完成：

```text
success
failed
uncertain
```

分类，并生成完整 manifest。

当前 fingerprint 的实盘验收结果：2,331 candidates 全部 classified，2,330
success、1 explicit uncertain、0 failed，1,204,898 logical events。两轮完整
schema `1.0.0` 运行的 manifest 均为 2,607,371 bytes，SHA-256 均为
`2d989e36722966d1e04698dfe0d94c253b097a932435ed3adcc3bdcb9bf2425a`；
2,330 个 chart 文件集合、大小与逐文件 SHA 均精确匹配 manifest。

发布前的 schema `1.1.0` 单一 raw-record table 首次全库刷新得到：manifest
为 2,606,521 bytes、SHA-256
`20d1bcd8f9a733614d9e0ab968abe855220c34e7e4bb2d2f390916d3426db4ea`；
chart 总字节从 24,737,874,119 降至 14,086,037,521（减少 43.059%）。
独立逐文件审计的 missing、extra、size、SHA、schema、layout 和 event-reference
mismatch 全为 0。当时仍明确区分旧 schema 双跑与新 schema 单跑；下面记录的后续
实盘双跑才补齐 schema `1.1.0` 的确定性证据。**M8 持续达到。**

2026-08-11 又对 Steam depot manifest `241392741196033182` 的独立完整资源集
重复 Phase 1–9。其 fingerprint 为
`sha256:d9108183177ac7c4821b466d28e0920d8a4a9bcd490a0edde956be3681233222`：
5,193 files、5,069 UnityFS、2,305 candidates、2,304 success、1 uncertain、
0 failed。两次 schema `1.1.0` 完整运行的 2,577,100-byte manifest 均为 SHA-256
`d893ca25bbb86683d3b27cdf016c594afc3406be9fd1432e5b2398298a0d94d2`；
两次增强审计也逐字节相同，file/schema/layout/group/event-reference/raw-accounting
mismatch 全为 0。共享的 725 个 StageInfo source 与两个 note-data bundle 均逐字节
相同，因此复用已证实的 disk parser/grouping family；GameAssembly 和 metadata 已变化，
静态 offset 不复用。第二 profile 达到 **M8**，M7 仍保持部分验证口径。

随后对最新 `1821...f0ab5` fingerprint 也补齐 schema `1.1.0` 第二次原地全量运行。
两次 manifest 均为 2,606,521 bytes、SHA-256
`20d1bcd8f9a733614d9e0ab968abe855220c34e7e4bb2d2f390916d3426db4ea`；
第一轮独立审计的 15 类 mismatch 全为 0，第二轮使用新增 fail-closed manifest
完整性检查后的 16 类审计，2,330 个文件、1,817,952 个导出 raw records 仍全部为 0。
双跑通过覆盖同一输出树完成，不保留重复库。

---

# Phase 10 — Public API / Exporter / Open-source Hardening

## 目标

在核心解析稳定以后，再把它整理成真正适合开源和第三方调用的项目。

---

## 10.1 JSON Exporter

官方推荐格式。

---

## 10.2 CSV Exporter

至少：

```text
index
time_sec
time_ms
end_time_sec
end_time_ms
duration_sec
duration_ms
type_id
type_name
is_air
```

扩展字段允许附加。

---

## 10.3 通用 Exporter API

例如：

```python
class ChartExporter(Protocol):
    def export(self, chart, destination):
        ...
```

核心只内置：

```text
JsonExporter
CsvExporter
```

---

## 10.4 通用 Transform API

如确有需要：

```python
class ChartTransform(Protocol):
    def transform(self, chart):
        ...
```

保持中立。

不内置：

```text
MusePlayAdapter
YOLOAdapter
AutoPlayAdapter
```

第三方项目自己实现。

---

## 10.5 Python API

目标：

```python
from musedash_chart_extractor import MuseDashInstallation

game = MuseDashInstallation.open(path)
charts = game.extract_charts()

for chart in charts:
    ...
```

CLI 只是 API 的薄封装。

---

## 验收门槛

- `JsonExporter`、`CsvExporter` 与通用 `ChartExporter` Protocol 可用；
- `MuseDashInstallation.open()` 对完整安装 fingerprint 做正式支持门禁；
- unknown fingerprint 默认只能 scan/probe，不能进入正式 parser；
- CLI `extract-all` 通过同一 Python API；
- README、schema、architecture、supported versions、contributing 和 changelog
  与实现一致；
- 无游戏资源测试、真实 `local_game` 测试、sdist/wheel 构建及内容审计通过；
- 发布包不包含 diagnostics、experimental、extracted、exports 或官方资源数据。

当前实现满足以上条件，Canonical Chart schema 为 `1.1.0`，Python package
版本为 `0.1.0`。公共仓库、真实 CI、`v0.1.0` tag 和 Release 均已建立，
**M9 达到**。项目没有宣称已上传 PyPI；当前公共制品由 GitHub Release 提供。

---

# 3. 推荐最终仓库结构

当项目进入稳定阶段后：

```text
musedash-chart-extractor/
├── pyproject.toml
├── README.md
├── ROADMAP.md
├── LICENSE
├── CHANGELOG.md
├── src/
│   └── musedash_chart_extractor/
│       ├── __init__.py
│       ├── cli.py
│       ├── installation.py
│       ├── scanner.py
│       ├── fingerprints.py
│       │
│       ├── unity/
│       │   ├── bundles.py
│       │   ├── serialized.py
│       │   ├── addressables.py
│       │   └── typetree.py
│       │
│       ├── discovery/
│       │   ├── candidates.py
│       │   └── scoring.py
│       │
│       ├── charts/
│       │   ├── models.py
│       │   ├── parser.py
│       │   ├── indexing.py
│       │   └── validator.py
│       │
│       ├── exporters/
│       │   ├── base.py
│       │   ├── json_exporter.py
│       │   └── csv_exporter.py
│       │
│       └── diagnostics/
│           ├── reports.py
│           └── logging.py
│
├── tests/
│   ├── fixtures/
│   └── ...
│
└── docs/
    ├── architecture.md
    ├── schema.md
    ├── reverse-engineering-notes.md
    ├── supported-versions.md
    └── contributing.md
```

---

# 4. CLI 最终目标

```text
musedash-chart-extractor scan
musedash-chart-extractor probe
musedash-chart-extractor candidates
musedash-chart-extractor extract
musedash-chart-extractor extract-all
musedash-chart-extractor validate
```

---

# 5. 版本兼容策略

Muse Dash 更新后资源结构可能变化。

因此必须：

## 5.1 使用 Fingerprint

不要只依赖游戏版本字符串。

记录关键资源 SHA-256 或组合 fingerprint。

---

## 5.2 Parser 分层

例如：

```text
common parser
    ↓
version-specific decoder
```

版本差异放在隔离层。

---

## 5.3 Unknown Version

未知版本默认：

```text
probe mode
```

而不是强行套旧 parser。

---

## 5.4 禁止固定 offset 驱动整个系统

除非确认某段二进制格式确实稳定，并且必须记录对应版本。

---

# 6. 测试策略

由于仓库不能包含完整官方谱面：

## 单元测试

使用人工构造 fixture。

测试：

```text
scanner
magic detection
candidate scoring
schema
validation
exporter
```

---

## 集成测试

允许用户本机运行：

```text
pytest -m local_game
```

这些测试：

- 不进 CI
- 需要真实 Muse Dash 安装
- 不上传资源

---

## Snapshot

可以保存：

```text
object type counts
field names
small metadata summaries
```

不要保存可还原完整谱面的真实数据。

---

# 7. 开源与版权边界

仓库可以包含：

- extractor 源码
- schema
- 解析说明
- 人工 fixture
- 小规模不可还原结构样例

仓库不能包含：

- 官方歌曲音频
- 官方完整 AssetBundle
- 官方完整谱面 dump
- DLC 解锁内容
- 破解授权逻辑

README 明确：

> 用户必须自行拥有并指定本机 Muse Dash 安装。

项目不提供资源下载和再分发。

---

# 8. 开发决策记录

遇到重要格式结论，必须写 ADR 或 reverse-engineering notes。

例如：

```text
ADR-001: Why UnityPy is the default backend
ADR-002: How chart identity is derived
ADR-003: Unknown event preservation
ADR-004: Game version fingerprinting
```

这样未来游戏更新后能知道旧设计为什么存在。

---

# 9. Agent 工作规则

如果使用 Codex / Claude Code / OpenCode 等 agent 开发：

## 必须

- 优先检查真实文件
- 给出具体路径、对象、字段证据
- 每个阶段产出诊断结果
- 小步提交
- 不覆盖已有探索结果
- 保留失败样本描述

## 禁止

- 根据经验直接“猜格式”
- 没有证据就说“加密”
- 没有解析成功就大规模重构
- 在 Phase 5 前做 GUI
- 将项目改成 Mod
- 将项目改成运行时读取器
- 把下游 AI 逻辑写进核心库

---

# 10. 项目真正的里程碑

## M0 — Skeleton

CLI 和 scanner 可运行。

---

## M1 — Inventory

完整识别当前安装资源布局。

---

## M2 — Unity Parsed

确认可解析的 Unity / Addressables 资源集合。

---

## M3 — Candidate Found

发现有证据支持的谱面候选对象。

---

## M4 — First Chart

**成功离线恢复一张真实官方谱面。**

这是第一个真正意义上的成功。

---

## M5 — Indexed Charts

恢复歌曲 / 难度 / chart 关系。

---

## M6 — Canonical Schema

定义并稳定统一数据模型。

---

## M7 — Verified

与实际游戏或独立参考数据交叉验证。

---

## M8 — Extract All

批量导出本地全部支持谱面。

---

## M9 — Public Release

文档、测试、许可证、API、兼容策略完成。

当前状态：技术内容、受版本控制 revision、公共项目 URL、真实 CI、
`v0.1.0` tag 和 GitHub Release 均完成，M9 达到。

---

# 11. 当前最优先任务

M9 已完成，第二个真实 fingerprint 的 Phase 1–9 兼容证据链也已闭合。当前首要研究工作
不是增加 GUI 或下游适配器，而是扩大独立逐事件参考覆盖、恢复
`tutorial_v2_map1` 的 song identity，并在获得第三个真实 fingerprint 时重复同一证据链。
未知版本默认继续保持 probe-only；显式 research batch 也必须通过完整证据门禁并标记为
非正式，不能通过降低门禁换取表面兼容。

---

# 12. Definition of Done

项目达到第一个可公开发布版本时，应满足：

- [x] 不启动 Muse Dash 即可工作
- [x] 不依赖 ModLoader
- [x] 不读取运行时内存
- [x] 不修改游戏文件
- [x] 自动扫描本地安装
- [x] 能识别支持的游戏资源布局
- [x] 能定位官方谱面
- [x] 能解析歌曲 / 难度
- [x] 能输出统一 Chart Model
- [x] 支持 JSON
- [x] 支持 CSV
- [x] 未知字段不会被静默丢失
- [x] 未知类型不会导致事件消失
- [x] 所有输出具备 provenance
- [x] 有 validate 子命令
- [x] 有 manifest
- [x] 游戏更新时能明确 fail，而不是静默产生错误数据
- [x] 仓库不包含官方完整资源或完整谱面 dump
- [x] 下游项目可以通过通用 API / Exporter 使用结果
- [x] 核心代码不绑定 MusePlay、YOLO 或 AutoPlay
- [x] 首次受版本控制的 revision 已通过真实 CI
- [x] 公共仓库 URL、`0.1.0` changelog、tag/release 已建立

---

# 13. 一句话开发准则

> **先证明，再抽象；先解析一张，再解析全部；先保真，再做转换；核心只负责谱面提取，不替任何下游项目做决定。**
