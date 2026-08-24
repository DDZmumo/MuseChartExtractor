# Reverse Engineering Notes

本文件只记录可复现实验和有来源的格式结论。运行时 Hook、内存读取和未经证实的格式猜测不属于本项目证据。

## 2026-08-10 — Repository bootstrap baseline

### 调查范围

- 工作目录：`D:\Projects\PythonP\MuseChartExtractor`
- 初始文件清单：仅 `ROADMAP.md`
- 初始 Git 状态：不存在 `.git`，没有可分析的提交历史
- 初始实现、测试、文档、诊断产物：均不存在

### 发现

- 当前阶段确定为 **Phase 0 — Repository Bootstrap**。
- 在完成并验证 CLI、目录错误处理和最小扫描骨架前，Phase 1 尚未达到进入条件。
- 尚未对任何 Muse Dash 游戏文件、Unity Bundle 或序列化对象作出格式结论。

### 当前理解

- 状态：repository-bootstrap
- 置信度：高（来自工作目录完整文件枚举）

### 尚未解释的问题

- 本机 Muse Dash 安装资源布局与 fingerprint。
- 是否存在标准 Unity Bundle、Addressables 或可由 UnityPy 解析的对象。
- 官方谱面在磁盘上的实际对象和字段位置。

## 2026-08-10 — Phase 1 real resource inventory

### 游戏资源 fingerprint

- Steam app manifest：`E:\SteamLibrary\steamapps\appmanifest_774171.acf`
- manifest `appid` / `name`：`774171` / `Muse Dash`
- manifest build ID：`24198001`
- 游戏目录：`E:\SteamLibrary\steamapps\common\Muse Dash`
- manifest `SizeOnDisk`：`4,791,937,135` bytes
- inventory 总大小：`4,791,937,135` bytes（与 manifest 精确一致）
- inventory fingerprint：`sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5`

### 可复现实验

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath "src").Path
python -m musedash_chart_extractor scan `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output-dir diagnostics
```

连续运行两次后：

- `diagnostics/resource_inventory.jsonl`：两次 SHA-256 均为 `fb2116940ece1fc85deb41794e600610d5d5c77535f3acadb4da04fd0eaa121b`
- `diagnostics/resource_summary.json`：两次 SHA-256 均为 `e09b924f43a8f407b1fea4e25ebfafc066382392bed05a51721a8171945dd809`

### 发现与证据

- 共扫描并逐文件计算 SHA-256：`5,218` files。
- `5,094` 个 `.bundle` 文件的实际文件头均为 `UnityFS`；它们位于 `MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/`。这是标准 UnityFS signature 证据，但尚不等价于 UnityPy 解析成功。
- Addressables 入口存在：
  - `MuseDash_Data/StreamingAssets/aa/catalog.json`，`12,674,185` bytes，JSON magic，SHA-256 `61059d3983d68b9b9e06ca580155c56bdc378882d68ed6c4acd6894ce58d6242`
  - `MuseDash_Data/StreamingAssets/aa/settings.json`，`1,605` bytes，JSON magic，SHA-256 `7dfad052f7bb4c3cd8aae77f6deadd3bf131d5bab7fdeaad21aefb1f0c258107`
- Unity serialized-file candidates：
  - `MuseDash_Data/globalgamemanagers.assets`
  - `MuseDash_Data/resources.assets`
  - `MuseDash_Data/sharedassets0.assets`
- 资源伴随文件：`level0.resS`、`resources.assets.resS`、`resources.resource`、`sharedassets0.assets.resS`。
- IL2CPP 静态调查输入存在：
  - `GameAssembly.dll`，SHA-256 `35f554fda30ac99e65fdd530167d341ffc063b58962f9a2ad2ad977454811d86`
  - `MuseDash_Data/il2cpp_data/Metadata/global-metadata.dat`，SHA-256 `6bbf4b5b86d7f6f15be0cccb7cae64a388f5790cf72737cfa5b89a24adf5df2a`
- 以 `16 MiB` 为阈值，`unknown` 分类的大文件数量为 `0`。这只说明当前 magic/category 规则下没有未分类大文件，不说明所有小文件格式均已理解。

### 当前理解

- Phase 0 / M0：验收通过。
- Phase 1 / M1：验收通过；真实 inventory 可稳定复现。
- 下一阶段：**Phase 2 — Unity / Addressables Recon**。
- 置信度：高（逐文件哈希、总大小交叉核对、双次稳定性验证）。

### 明确未证明

- 尚未用 UnityPy 打开任何 Bundle，因此没有 `parseable`、Unity version、container count、object count 或 object type distribution 结论。
- 尚未解析 Addressables catalog 的 key、internal ID、provider、dependency 关系。
- 文件名中的 `song`、`stage`、`config` 等词目前只属于名称线索；没有任何对象被认定为谱面。
- 尚未提取第一张真实谱面；M4 未达到。

## 2026-08-10 — Phase 2 Unity / Addressables recon

### 工具与范围

- 游戏 inventory fingerprint：`sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5`
- UnityPy：`1.25.3`
- Python：`3.13.11`
- metadata probe：全量、串行、只读；未调用 TypeTree、未导出对象内容。
- Unity 资源版本：所有 5,114 个 serialized asset file 均报告 `2019.4.41f1`（部分 Bundle 内含多个 serialized file，因此大于 source 数量）。

### 全量 UnityPy 结果

- 候选 source：`5,097`
  - UnityFS bundle：`5,094`
  - `.assets` serialized file：`3`
- parseable：`5,097`
- failed：`0`
- object 总数：`311,497`
- container entry 总数：`29,722`
- 主要对象类型：
  - `MonoBehaviour`: `76,800`
  - `GameObject`: `65,430`
  - `TextAsset`: `2,250`
  - `AudioClip`: `2,924`
  - `AssetBundle`: `5,094`
- `peek_name()` 错误：`0`
- UnityPy warning：`0`
- container 中有 `10` 个无法 dereference 的 `PathID=0` 占位 PPtr；均已在 JSONL 中保存路径和 `ValueError`，不计作 source parse failure。其中 9 个是 scene path，另 1 个是 `SpeedLineCore.hlsl`。

最初 smoke probe 曾把部分 `env.container` 值误当作 ObjectReader，实际 UnityPy 1.25.3 在这些 Bundle 返回 `PPtr`。正式实现改为显式 `deref()`，并对 `PathID=0` 保留 resolution error；修复后 smoke 与全量 probe 均通过。

### Addressables 格式证据

- `settings.json` 的 `m_AddressablesVersion`：`1.21.20`。
- `catalog.json` 本身没有这个版本字段；没有把 binary catalog 分支的 `kVersion=1` 误标为 JSON 格式版本。
- 格式依据为 Unity Addressables tag `1.21.20`、commit `d7b49efac2e1ba2e1faa673e104c2eec84acf529`：
  - [`ContentCatalogData.cs`](https://github.com/needle-mirror/com.unity.addressables/blob/d7b49efac2e1ba2e1faa673e104c2eec84acf529/Runtime/ResourceLocators/ContentCatalogData.cs)
  - [`SerializationUtilities.cs`](https://github.com/needle-mirror/com.unity.addressables/blob/d7b49efac2e1ba2e1faa673e104c2eec84acf529/Runtime/Utility/SerializationUtilities.cs)
- 上游源码使用 Unity Companion License。本项目只依据可观察格式独立实现 Python decoder，没有复制或再分发上游源码/二进制。
- 四个 Base64 字段属于 compact JSON catalog，未使用 binary catalog 的 `BinaryStorageBuffer`。

全量严格解码：

- key / bucket：`50,312 / 50,312`
- entry：`33,965`
- bucket → entry reference：`80,580`
- internal ID：`27,608`
- provider：`3`
- resource type：`84`
- extra object：`5,094`
- internal ID prefix：`0`
- stream bytes：
  - KeyData：`1,774,422`
  - BucketData：`724,820`
  - EntryData：`951,024`
  - ExtraData：`4,025,820`
- key tags：`50,293` ASCII string、`19` UTF-16LE string。
- extra tags：`5,094` JsonObject，全部为 `AssetBundleRequestOptions`；未知 tag 为 `0`。
- 所有 Base64、count、length、offset、index 与 EOF 检查通过。
- catalog 中解析出的本地 bundle path：`5,094`；Phase 1 inventory bundle path：`5,094`；matched：`5,094`；双方差集：`0 / 0`。

依赖语义已按实际 entry 验证。例如 entry `7309` 的 dependency key 是字符串 `-1203540875`，对应 bucket entries `[284, 2778, 3366]`，分别定位三个 bundle。该证据说明数字形式 dependency key 不能猜作 Entry ID。

### 高价值资源族

- `music_*` bundle：`733`
  - 其中 `732` 个包含 `Assets/Static Resources/Data/Configs/StageInfos/*.asset`。
  - 余下 1 个是 `hatsune_tenchi_kaibyaku_shinwa_long_music` 的纯额外音频 Bundle。
- 唯一非 `music_*` 但含 StageInfo 的 source：`tutorialasset_assets_all_7225650d06960453d88de8c373e6a8b8.bundle`。
- StageInfo source 合计：`733`；每个 source 的 StageInfo 数量分布：
  - 1 个：5 sources
  - 2 个：9 sources
  - 3 个：577 sources
  - 4 个：133 sources
  - 5 个：9 sources
- `song_*` bundle：`731`，对象聚合为试听 `AudioClip` 与封面 `Texture2D/Sprite`，StageInfo 数量为 `0`。

具体样本：

```text
Bundle:
MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/
music_urban_magic_assets_all.bundle

Bundle SHA-256:
027bcaa714e3d04b42f0c6752046d6e71b37d8c400d439840a75033368357594

Objects:
AssetBundle=1, AudioClip=1, MonoScript=1, MonoBehaviour=3

StageInfo objects:
urban_magic_map1  PathID=2982174055250368719  byte_size=290348
urban_magic_map2  PathID=3448044729589111705  byte_size=346220
urban_magic_map3  PathID=8668625138739021960  byte_size=481600

MonoScript:
StageInfo  PathID=5973899300630725692
```

这些事实支持 StageInfo 作为 Phase 3 最高价值候选，但尚未读取字段，因此不能宣称其包含谱面事件。

### 可复现诊断产物

- `bundle_inventory.jsonl`：`10,271,930` bytes，SHA-256 `192dd8fdfd13e083a30a89d0174c62857bc9d1c03512fd23628bcebcb49d39c7`
- `serialized_file_inventory.jsonl`：`400,125` bytes，SHA-256 `ff7036bc9ea7f220aee3440c3c869833f0a8df2796785275b426b6005af21bf9`
- `object_type_summary.json`：`2,232` bytes，SHA-256 `1eb3004eeebe70c2296d229305c95605381c2b71f1541f6a0e1194696b09150b`
- 规范化 compact `addressables_index.json`：`37,219,017` bytes，SHA-256 `742f4fb49aead5100732ab740cca5a6163de0b3d600f030601bc3bf772cc3be2`

Addressables 索引初版曾因在每条 Entry 中重复展开依赖、key、internal ID、resource type 和 extra data 而达到约 `1.69 GB`。这属于诊断表示问题，不是游戏 catalog 大小。正式产物改为规范化表和 index 引用，信息保留一次，避免组合爆炸。

### Phase 2 验收回答

1. 当前安装存在标准 Unity Bundle：**是，5,094 个 UnityFS**。
2. UnityPy 能否打开：**当前 fingerprint 下，5,097/5,097 候选可打开**。
3. 是否存在 Addressables：**是，版本线索为 1.21.20，compact catalog 已完整解码**。
4. 最值得继续分析的 bundle：**732 个含 StageInfo 的 `music_*` bundle，加 tutorial StageInfo bundle**。

因此 Phase 2 / M2 验收通过，当前进入 **Phase 3 — Chart Candidate Discovery**。

### 明确未证明

- StageInfo 的序列化字段尚未读取。
- 尚未确认时间字段、事件类型字段或事件数组。
- 尚未实施候选评分，也未生成 `chart_candidates.jsonl`。
- 尚未离线恢复真实事件序列；M3、M4 均未达到。

## 2026-08-10 — Phase 3 chart candidate discovery

### 假设与探针边界

假设：Phase 2 已定位的 `Assets/Static Resources/Data/Configs/StageInfos/*.asset` 可能包含重复的谱面相关结构。

探针没有重新遍历所有 Unity 对象，而是以 Phase 2 的 `bundle_inventory.jsonl` 为输入，只重新打开其中 733 个已证实含 StageInfo container 的 source，并按 PathID 定点读取 `MonoBehaviour` TypeTree。正式运行仍重新计算游戏 inventory fingerprint 和 source SHA-256，以避免把旧诊断套到已变化的安装上。

可复现命令：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor candidates `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output-dir diagnostics
```

### 全量结果

- 输入 source：733。
- StageInfo candidate：2,331。
- TypeTree 读取失败：0。
- silent skip：0；输出行数与 Phase 2 的 StageInfo container census 精确一致。
- 2,331 个对象全部是 `MonoBehaviour`，全部通过本 source 内的 PPtr 指向唯一的 `StageInfo` MonoScript。
- 脚本身份全部为 `Assets.Scripts.GameCore.StageInfo`，assembly 为 `Assembly-CSharp.dll`。
- 顶层字段形状全部一致：`m_GameObject`、`m_Enabled`、`m_Script`、`m_Name`、`serializationData`、`mapName`、`music`、`scene`、`difficulty`、`md5`、`bpm`、`sceneEvents`。
- `SerializedFormat` 全部为 `0`，`SerializedBytes` 全部非空；总 payload `1,053,670,885` bytes，单对象范围 `55,148..1,879,576` bytes。
- `difficulty` 原始分布：`1:726, 2:780, 3:823, 4:2`。
- 有 1 个对象的原始 BPM 为 `0`。
- 有 19 个对象的 `mapName` 是开发机绝对 `.bms` 路径，而不是 asset name；原始字符串已保留，未规范化或删除。

评分版本为 `stageinfo-signals-v1`。信号包括精确 container/type、精确 MonoScript 身份、TypeTree 形状、非空大型 payload、实际出现的 `GameLogic.MusicData` type descriptor、重复事件字段名以及 `sceneEvents` 数组。当前 2,331 条记录均得到 `1.0`，因为这整个已证实家族的结构一致；`score_interpretation` 明确写为“已观察到的 Phase 3 结构信号比例”，不是谱面概率，也不代表录像验证。

`bpm=0`、`difficulty=4` 和绝对 `mapName` 都没有被当作硬拒绝条件。候选诊断只写 payload 的 byte count、SHA-256 和有限字符串统计，未写出 `SerializedBytes`。

### 诊断产物

- `diagnostics/chart_candidates.jsonl`
- 行数：`2,331`
- 大小：`13,112,436` bytes
- SHA-256：`5b5f3a181d829a6ea969d80f6cee33e25baa050dd79827bce7b9097ee5ffd79f`

### 阶段判断

- Phase 3 验收通过：得到有稳定排序、来源、评分组成、证据和反证的候选集合。
- M3 — Candidate Found：达到。
- 这仍只证明一个高价值候选家族，不证明每条 candidate 都是完整谱面，也不排除将来存在其他磁盘表示。

## 2026-08-10 — Phase 4 Odin serialized structure recovery

### 格式来源与静态证据

`urban_magic_map1` 的 TypeTree 显示 `serializationData.SerializedFormat=0`，且 `SerializedBytes` 前缀包含 `musicDatas`、`System.Collections.Generic.List\`1[[GameLogic.MusicData, Assembly-CSharp]], mscorlib` 和 `GameLogic.MusicConfigData, Assembly-CSharp`。

本机静态文件同时提供独立线索：

- `global-metadata.dat` 中出现 `Sirenix.Serialization`、`OdinSerializeAttribute`、`BinaryDataReader`、`BinaryDataWriter`、`SerializedFormat` 和 `SerializedBytes`。
- `GameAssembly.dll` 中也出现 `Sirenix.Serialization`。

开发期只读参考为 Team Sirenix Odin Serializer commit `ba19025b3dc38de8cebb10f94a583d1ff303ad59`，许可证 Apache-2.0：

- [`BinaryEntryType.cs`](https://github.com/TeamSirenix/odin-serializer/blob/ba19025b3dc38de8cebb10f94a583d1ff303ad59/OdinSerializer/Core/DataReaderWriters/Binary/BinaryEntryType.cs)
- [`BinaryDataReader.cs`](https://github.com/TeamSirenix/odin-serializer/blob/ba19025b3dc38de8cebb10f94a583d1ff303ad59/OdinSerializer/Core/DataReaderWriters/Binary/BinaryDataReader.cs)
- [`BinaryDataWriter.cs`](https://github.com/TeamSirenix/odin-serializer/blob/ba19025b3dc38de8cebb10f94a583d1ff303ad59/OdinSerializer/Core/DataReaderWriters/Binary/BinaryDataWriter.cs)

这个 commit 只用于核对 wire format，**不是**游戏所用 Odin 精确版本的证据。格式判断来自实际 payload 的 tag、字符串、类型表、节点闭合和数值布局逐字节一致，而不是仅凭库名猜测。

### 已证实的 wire 子集

当前三张样本只出现：

```text
0x01 NamedStartOfReferenceNode
0x03 NamedStartOfStructNode
0x04 UnnamedStartOfStructNode
0x05 EndOfNode
0x06 StartOfArray
0x07 EndOfArray
0x13 NamedShort
0x17 NamedInt
0x23 NamedDecimal
0x27 NamedString
0x2b NamedBoolean
0x2d NamedNull
0x2f TypeName
0x30 TypeID
```

数值均为 little-endian。字符串 body 为 1-byte width flag、little-endian `i32` char count、随后单字节或 UTF-16LE 字符。`.NET decimal` 是 16 bytes，实际布局为 `<flags, high, low, middle>` 四个 `u32`；解析器保留 exact decimal text、bits 和 `raw_hex`，不转换为 float。

严格解析得到的重复记录结构：

```text
MusicData
├── objId: int16
├── tick: decimal
├── configData: MusicConfigData
│   ├── id: int32
│   ├── time: decimal
│   ├── note_uid: string | null
│   ├── length: decimal
│   ├── blood: bool
│   └── pathway: int32
├── isLongPressing: bool
├── doubleIdx: int32
├── sameTickNoteIdx: null   (当前三张样本)
├── isDouble: bool
├── isLongPressEnd: bool
├── longPressPTick: decimal
├── endIndex: int32
├── dt: decimal
├── longPressNum: int32
└── showTick: decimal
```

数组之后还有顶层 `delay: decimal` 与 `dialogEvents: null`，随后精确到 payload EOF。未知 tag、未定义 TypeID、截断、非法 decimal flags、负数或超限长度、节点/数组计数不符及额外尾字节都会抛出包含 offset、tag 和 node path 的 `OdinParseError`；解析器不会猜长度或跳过未知内容。

### 三张实际对象交叉检查

同一个 bundle：

```text
MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/
music_urban_magic_assets_all.bundle
```

bundle SHA-256：`027bcaa714e3d04b42f0c6752046d6e71b37d8c400d439840a75033368357594`

| asset | PathID | payload bytes | musicDatas count | 结果 |
|---|---:|---:|---:|---|
| `urban_magic_map1` | 2982174055250368719 | 290,156 | 503 | 精确消费到 EOF |
| `urban_magic_map2` | 3448044729589111705 | 346,028 | 600 | 精确消费到 EOF |
| `urban_magic_map3` | 8668625138739021960 | 481,388 | 835 | 精确消费到 EOF |

三份 payload 均使用相同三项类型表，无未知 tag，声明数组长度与实际解析条数一致。

Phase 5 选择 `urban_magic_map3`，其 payload SHA-256 为 `e311b3f4c640428bd31596d5d1b2c7851bcf25618f4c9aa51176f1e24b698a18`。

### 字段假设与反证

- `musicDatas[]`：835 条结构相同的 `MusicData` 记录，置信度高。
- `tick`：范围 `0..144.545`，834 个相邻对中 780 个非递减，但有 54 次下降；只标记为 time-related，不能称为严格排序时间轴。
- `configData.time`：范围 `0..144.545454...`，834 个相邻对中 751 个非递减，但有 83 次下降；同样只标记为 time-related。
- `isLongPressing`：`true=196, false=639`。
- `isDouble`：`true=54, false=781`。
- `isLongPressEnd`：`true=55, false=780`。
- `configData.id` 尚未证明是 type，不能按字段值猜 enum。

### 诊断产物与阶段判断

- `diagnostics/field_hypotheses.jsonl`
- 行数：`5`
- 大小：`16,893` bytes
- SHA-256：`4c4fa679a43cd26d7ad0fe6d08c139cbbeded3b5979155edd0973dc4a3d31e44`
- 只保留 2 条有限 record sample，不是完整官方谱面 dump。

Phase 4 验收通过：已从一个高分候选恢复事件数组、两个 time-related 字段以及行为相关 bool 结构，并为每项保存 confidence、evidence 和 counter-evidence。字段的游戏语义仍需 Phase 5 录像对照。

## 2026-08-10 — Phase 5 partial-validation checkpoint（已被后续结果取代）

### 本地原始提取

`extract` 命令从同一个经过 source/payload SHA-256 校验的 `urban_magic_map3` 对象生成：

```text
experimental/first_chart.json
```

- 本地文件大小：`13,222,041` bytes。
- 本地文件 SHA-256：`015bd947b7ba9e0da42a74ecf5b95329e902ac8b451ca99354b0c754350faeb1`。
- 原始记录：835。
- 状态：`raw-extracted`。
- `validation_status`：`unvalidated`；`canonicalized=false`。
- `raw_type=null` / `type_status=unknown`，没有把 `configData.id` 或 `note_uid` 强行解释为事件类型。
- 输出使用 `raw_records` / `raw_record_count`，不把尚未分组的记录命名为 canonical events。
- 每条记录保留完整 raw 字段、decimal bits/raw hex、offset 和 provenance。
- `stage_info_raw` 保留完整 TypeTree envelope，包括 Unity PPtr、`serializationData` 的全部 8 个字段和非空 `sceneEvents`；本样本的唯一 scene event 原始对象含 `uid` 字段。
- envelope 内 `SerializedBytes` 仍为原始 `481,388` bytes，其重新计算的 SHA-256 与 provenance 中的 `e311b3f4...98a18` 一致；因此导出没有为避免重复而丢掉原始载荷。

该文件包含本机官方谱面数据，已由 `experimental/.gitignore` 排除，不能提交仓库。上面的大小和哈希只用于本机复现，不构成可再分发 fixture。

### 公开视频对照

选用公开的 [`Urban Magic<7⭐>` 游戏录像（BV1ye4y127LM，第 146 P）](https://www.bilibili.com/video/BV1ye4y127LM/?p=146) 做人工抽样。页面对应 `cid=1520846730`；验证时使用的公开低清媒体长 `167,765 ms`、`11,747,341` bytes、SHA-256 `e5c36774a74c483cad8b289ec903ca44239440777c1925233ef2a667ecd4accd`。临时媒体和取帧图没有加入仓库。

当前样本支持近似对齐：

```text
video_time_sec ≈ configData.time + 14.942
人工取帧不确定度约 ±0.15 s
```

抽样证据：

- 前七个非 sentinel 的 `configData.time` 依次预测录像约 `16.500..17.864 s`，画面中对应开场七次命中，顺序一致。
- record 8/9 具有相同 `time=3.116883...`、`pathway=1/0`、`isDouble=true`；预测约 `18.058 s`，画面在 `18.0..18.125 s` 出现上下轨同时对象和命中。
- record 17 的 `time=5.064935...`、`length=0.389610...`，后续记录含 `isLongPressing=true`，record 21 含 `isLongPressEnd=true`；预测 `20.006..20.396 s`，对应画面显示长按进入判定线并持续。
- 结尾画面由 combo 557 上升到 568，随后显示 Full Combo。独立歌曲资料页也把 7 星难度物量列为 568：[`Urban Magic` — Muse Dash Wiki](https://wikiwiki.jp/musedash/Urban%20Magic)。

最重要的反证是：**835 条 raw `MusicData` record 不等于 568 个可见计分对象**。记录中包含 sentinel、长按子记录或其他状态数据；在恢复分组规则前，不能把每条 raw record canonicalize 成一枚 note。

可复现、无媒体内容的验证摘要写入：

- `diagnostics/first_chart_validation.json`
- 大小：`4,451` bytes
- SHA-256：`a753f25b90f262f6a5b27488ffdc401324b84e0d9f62258a92790f044730113e`
- 状态：`partially-validated`

### 当前判断

- 已证明选中磁盘对象包含真实、按一定规则排列的 gameplay event stream。
- 已用画面抽样支持 `configData.time`、`isDouble` 和长按相关字段与实际事件结构的对应关系。
- 尚未恢复 canonical event type、raw record → scoring note 的分组规则，也未对中段、密集段、结尾的全部事件进行逐项核对。
- 因“事件大致数量”尚不能从 835 raw records 正确还原为 568 scoring objects，Phase 5 尚未通过。
- **M4 — First Chart 尚未达到；不得进入 Phase 6。**

这一判断记录了当时的证据边界，已被下面 2026-08-11 的分组、静态语义和录像复验结果取代。

## 2026-08-11 — Phase 5 first real chart validated

### 资源 fingerprint 与本地输出

继续使用资源 inventory fingerprint `sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5`。选中对象仍为：

```text
bundle: MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/music_urban_magic_assets_all.bundle
bundle SHA-256: 027bcaa714e3d04b42f0c6752046d6e71b37d8c400d439840a75033368357594
container: Assets/Static Resources/Data/Configs/StageInfos/urban_magic_map3.asset
PathID: 8668625138739021960
type: MonoBehaviour / Assets.Scripts.GameCore.StageInfo
Odin payload: 481,388 bytes
payload SHA-256: e311b3f4c640428bd31596d5d1b2c7851bcf25618f4c9aa51176f1e24b698a18
```

修复逻辑顺序后重新生成的 `experimental/first_chart.json` 为 `15,473,296` bytes，SHA-256 `feab872fdfcaa7c3c050e509a3aeec3a52db85450a38e2299ccbe19058812462`。文件仍是 Git 忽略的本地官方数据，不得提交或再分发。它保留完整 StageInfo TypeTree envelope、原始 `SerializedBytes`、835 条原始记录以及所有 raw index；排序后的逻辑投影没有覆盖原始流。

### NoteConfig 磁盘关系

Phase 2 inventory 中的唯一精确 TextAsset：

```text
bundle: MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/
        config_others_assets_notedata_73f5a4dffa7fa71f762d891547b70539.bundle
bundle SHA-256: 28e322b84eec6a9f105c1f6d524a0658448878600ca17f2daab1080b2ff94e91
container: Assets/Static Resources/Data/Configs/others/notedata.json
PathID: 376546556692839827
content: 1,231,872 bytes
content SHA-256: 45d5c5149f8641506753b6e1dd6130962eebb1745b8a17f0f2a2c46794e959a6
```

JSON 有 2,363 行、2,053 个唯一 UID；重复 UID 保留全部候选而不覆盖。map3 使用 53 个非空 `note_uid`，全部通过 `MusicData.configData.note_uid → notedata.json.uid → notedata.json.type` 映射成功。该关系是磁盘字段 join；静态 loader 另按 `prefab_name` 查找 `NoteConfigData`，当前没有把两条路径宣称为同一个运行时调用。

### 原始记录分组与顺序

按 `configData.id` 严格分组得到 `0..583` 共 584 组，无缺号：

- id 0 是唯一 `note_uid=null` 且时间、长度和行为字段为零的 sentinel。
- 其余 583 组各有且仅有一条 `endIndex=0` base，且 base 是该组最先存储的记录。
- 每组的 `note_uid`、`configData.time`、`pathway` 恒定。
- 251 条非 base 记录全部 `endIndex>0`，且恰有一个 `isLongPressing` / `isLongPressEnd` 为真：196 条 pressing、55 条 end。

Odin 原始数组会穿插长按状态记录，按 group 首次出现顺序会产生 44 次时间回退，因此不能称为逻辑事件顺序。`configData.id` 升序在 map1、map2、map3 上都使 `configData.time` 回退数为 0。正式 Phase 5 投影现在按 id 排列 `logical_objects`，同时保留每组所有 raw record index；人工乱序 fixture 覆盖了这个回归。

三个难度的结构交叉检查均满足上述不变量：

| asset | raw records | groups（含 sentinel） | logical objects | expanded records |
|---|---:|---:|---:|---:|
| `urban_magic_map1` | 503 | 257 | 256 | 246 |
| `urban_magic_map2` | 600 | 431 | 430 | 169 |
| `urban_magic_map3` | 835 | 584 | 583 | 251 |

### 静态 IL2CPP 语义证据

开发期只读工具为 [Il2CppDumper v6.7.46](https://github.com/Perfare/Il2CppDumper/releases/tag/v6.7.46)（MIT）；工具、生成的 `dump.cs` / `script.json` 和游戏二进制都只在临时目录，未加入仓库。输入 SHA-256：

- `GameAssembly.dll`: `35f554fda30ac99e65fdd530167d341ffc063b58962f9a2ad2ad977454811d86`
- `global-metadata.dat`: `6bbf4b5b86d7f6f15be0cccb7cae64a388f5790cf72737cfa5b89a24adf5df2a`

恢复的 `GameLogic.MusicConfigData` / `MusicData` 字段 offset 与离线 Odin 顺序一致；`NoteConfigData` 明确含 `type` 和 `addCombo`。`NODE_TYPE_IS_ADD_COMBO` 的静态初始化 blob 位于 metadata offset `0x8a2d7e`，little-endian `u32` 为 `[1, 4, 3, 5, 8]`，`IsAddComboType` 逐项比较这个数组。`MusicData.isLongPressStart` 要求 `length>0 && type==3`；`TouchResult` 在调用 combo 逻辑前跳过 `isLongPressing` 中间态。

第二份真实磁盘对象提供独立核验：

```text
bundle: MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/
        globalconfigs_assets_notedatamananger_5f692007f3009b56564819683345ccdc.bundle
bundle SHA-256: a28ca81be3f05d37a24f78ca1931141c837517a9288ceeabe99f7a289a439598
PathID: 6845249668675236146
type: MonoBehaviour
Odin root: m_NoteDatas
payload: 2,095,249 bytes
payload SHA-256: 5d02772d8ee5e8ebac3365aecb84d68a52bb180a1482535074b0005de4fca440
```

临时严格探针完整消费 2,363 条、每条 25 字段至 EOF；`addCombo=true` 只出现在 type 1、3、4、5、8，与静态数组完全一致。这是格式证据，不会成为最终用户的外部工具依赖。

### map3 数量与录像闭环

583 个非 sentinel 逻辑对象的 raw type 分布：

```text
0:5, 1:438, 2:24, 3:55, 4:17, 5:1, 6:2, 7:39, 8:2
```

静态 add-combo 集合给出 513 个 base；55 个 type 3 长按各有一条 end，196 条 pressing 中间态不增加 combo，因此：

```text
438 + 55*2 + 17 + 1 + 2 = 513 + 55 = 568
```

该数字不是按终局硬拟合：公开视频中开场 type 7 对象加分但 combo 不变；长按起点和终点各加一；type 6 红心与 type 2 齿轮不变；第一个 type 8 显示 5 HITS 期间保持 214，完成后只到 215，第二个同样从 325 到 326；最后 type 5 令 567 变为 568，随后显示 Full Combo。此前的双轨、时间偏移和长按窗口抽样仍成立。

外部口径反例必须保留：相同静态规则在 map1 得 256；在本机 map2 得 406，而资料页写 506。随后抽样的 [Urban Magic 5 星 AP 录像](https://www.nicovideo.jp/watch/sm43719259) 结尾明确显示 `406 COMBO` 和 `FULL COMBO`，与本地投影一致，说明资料页的 506 不是这张谱的可见 full-combo 口径。临时 144p 媒体为 `3,121,096` bytes、SHA-256 `9f6637716f6a8fedc838a8b9fa8fa33b6dee8854d889f8573f5079e795f562ce`，验证后已删除。录像仍不能证明其游戏 build 与当前本地 fingerprint 完全相同，规则也未外推到其他歌曲。

### 诊断与阶段判断

- `diagnostics/first_chart_validation.json`
- 大小：`11,120` bytes
- SHA-256：`3afb736c2b0b68b8821d95008bc77f43fd51ef4749de18661906a0a119c7d098`
- 状态：`validated-first-chart`
- milestone：`M4-achieved`

Phase 5 验收通过：一个未启动游戏的本地 StageInfo 已被严格离线解析；逻辑顺序、时间抽样、双轨、长按、非 combo 特殊对象、multi-hit 与结尾总量均有可指向字段和画面的证据。完整 enum 名称、所有 raw 字段语义、其他谱面的 combo 规则和跨版本兼容性仍是 unknown，未被伪造为已完成。

当前进入 **Phase 6 — Song / Difficulty Index Recovery**，目标是在不手写 PathID 映射的前提下连续识别至少 3 首歌、每首至少 2 个难度。

## 2026-08-11 — Phase 6 song / difficulty index recovery

### Addressables 到 StageInfo 的全量关系

输入 catalog：

```text
path: MuseDash_Data/StreamingAssets/aa/catalog.json
SHA-256: 61059d3983d68b9b9e06ca580155c56bdc378882d68ed6c4acd6894ce58d6242
Addressables version: 1.21.20
build result hash: 9ecc2d74a4045582f2aabf0f64c83581
```

Catalog resource type index 44 是 `Assets.Scripts.GameCore.StageInfo`。2,331 个该类型 entry 与 Phase 3 candidate 逐一满足：

- `Addressables primary_key == candidate.metadata.asset_name`；
- primary key 等于 container basename；
- entry 有且仅有一个 bundle dependency；
- dependency 的 `internal_ids[].local_path == candidate.source`。

结果为 `2,331/2,331`，无失败；定位过程没有手写任一 PathID。全部 primary key 唯一并匹配末尾 `_mapN`。2,331 个 StageInfo `md5` 也都非空且唯一，但它只作为内容标识保留，不替代具有直接磁盘语义的 chart ID。

### ALBUM 元数据与 JSON5 反例

Phase 2 inventory 精确找到 100 个：

```text
Assets/Static Resources/Data/Configs/others/ALBUM<N>.json
```

合计 736 行、736 个唯一 `uid`、732 个唯一 `music`。`ALBUM44.json` 实际含 `//` 行注释，`ALBUM83.json` 实际含数组尾逗号；标准库 `json` 会在具体行列失败。因此正式实现使用 `json5` 的结构化 parser 支持这两种已观察语法，再严格校验 root、row、`uid`、`music`、`noteJson` 和来源 fingerprint；没有用正则删注释，也没有跳过两个文件。

ID 规则由磁盘关系决定：

```text
song_id       = ALBUM.uid
chart_id      = Addressables StageInfo primary key
difficulty_id = chart_id 末尾 _mapN 的整数 N
```

主连接 `album.noteJson + N == chart_id` 且存在 `difficultyN`，唯一匹配 `2,328/2,331`。另两张 tutorial chart 只能通过“StageInfo music 恰好匹配唯一 ALBUM 行”连接并带 warning；`tutorial_v2_map1` 没有 ALBUM 行，明确保留为 unresolved。

`StageInfo.difficulty` 不能当作 difficulty ID：实际有大量 `slot4/raw3`、`slot5/raw2`。19 个绝对开发机 `mapName` 同样不能作为 ID。`music` 有 4 个值对应多个 ALBUM song UID；StageInfo/ALBUM 还有 12 个 music、55 个 scene 和 131 个 BPM cross-check warning，因此这些字段只作为证据和 warning，不作硬拒绝。

### 验收样本

索引中的连续样本均由同一算法生成：

| song_id | song | chart_id | difficulty_id / level | PathID |
|---|---|---|---|---:|
| `73-0` | Urban Magic | `urban_magic_map1` | `1 / 3` | `2982174055250368719` |
| `73-0` | Urban Magic | `urban_magic_map2` | `2 / 5` | `3448044729589111705` |
| `0-11` | Lights of Muse | `lights_of_muse_map1` | `1 / 4` | `-7623476101228688515` |
| `0-11` | Lights of Muse | `lights_of_muse_map2` | `2 / 6` | `-694196948389303023` |
| `42-0` | Bad Apple!! feat. Nomico | `bad_apple_map1` | `1 / 1` | `7483908474274488411` |
| `42-0` | Bad Apple!! feat. Nomico | `bad_apple_map2` | `2 / 3` | `-5490260900572478073` |

### 诊断与阶段判断

- `diagnostics/song_chart_index.json`
- 大小：`5,939,890` bytes
- SHA-256：`660d144f5dbd5df5c8eec07b63cfff64ef25800908fd0f39bd1ccd485fce380c`
- song：736
- candidate chart：2,331
- indexed：2,330（exact 2,328；unique-music fallback 2）
- unresolved：1
- 至少有两张 indexed chart 的 song：731
- 同一输入连续运行两次 SHA-256 完全一致

Phase 6 验收通过，**M5 — Indexed Charts 达到**：远超过“3 首不同歌曲、每首至少 2 难度”的门槛，且关系由 Addressables、container、ALBUM 字段和 bundle dependency 推导，不依赖手写 PathID。一个 unresolved 是可见结果，不被吞掉。

当前进入 **Phase 7 — Canonical Chart Model**。Canonical 化必须保留 Phase 5/6 已恢复的完整 raw 数据、unknown、schema version 和 provenance，不能为了统一模型丢失 StageInfo envelope 或 ALBUM 字段。

## 2026-08-11 — Phase 7 canonical chart model

### 模型边界

新增 `charts/models.py` 和 `docs/schema.md`，schema version 为 `1.0.0`。Canonical Chart 包含 song、difficulty、source、timing、events、validation/canonicalization status、warnings 和 raw evidence。

每个 event 对应 Phase 5 已验证的 `configData.id` 逻辑组，而不是假设“一条 event 必加一次 combo”。核心 `time_sec` 使用 `configData.time` 的 exact decimal string，不转 float。type ID 通过本机 IL2CPP `NoteType` enum 提升 0–17 的名称；任何其他整数合法并输出 `type_name=null`、`type_status=unknown`。`is_air` 尚未形式化，当前 583 个 event 均为 null，不伪造 false。duration/end time 只对已有静态和录像证据的 type 3 (`Press`) 与 8 (`Mul`) 解释，共 57 个。

验证状态不由转换动作自动提升。没有 M4 report 时继承 Phase 5 的 `unvalidated`；显式报告必须逐项匹配 bundle、bundle SHA、PathID 和 payload SHA，且 milestone 为 `M4-achieved`，才能使用报告状态。错误报告会 fail closed。

### 无损实盘转换

可复现命令：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor canonicalize `
  --raw-chart experimental/first_chart.json `
  --song-index diagnostics/song_chart_index.json `
  --validation-report diagnostics/first_chart_validation.json `
  --output experimental/first_chart_canonical.json `
  --report diagnostics/canonicalization_report.json
```

本地官方输出（Git ignored）：

```text
experimental/first_chart_canonical.json
size: 28,461,597 bytes
SHA-256: cf201cffe001f7deb282dc49f71352f52d19c96a180f0769d8a071842d5c2181
```

结果为 song `73-0`、chart `urban_magic_map3`、difficulty 3、583 events。type 分布与 Phase 5 的 583 个逻辑对象完全一致；57 个 duration 被解释，583 个 `is_air` 保持 null。

无损检查均通过：

- `canonical.raw.experimental_chart` 与输入 Phase 5 JSON 结构相等；
- `canonical.raw.validation_report` 与输入 M4 report 结构相等；
- event group 中共保留 834 条 raw records，加 raw chart 中保留的 1 个 sentinel，等于原始 835；
- `raw.indexed_song` / `raw.indexed_chart` 保留 ALBUM raw 字段、Addressables dependency、StageInfo metadata 和 warning；
- 人工 type 99 fixture 保持 `type_id=99`、`type_name=null`，完整未知字段不丢失。

元数据诊断：

- `diagnostics/canonicalization_report.json`
- 大小：`1,721` bytes
- SHA-256：`b4b20d4aa41ed458044de2394d432336eac01121977e34c8e27073644fc4788a`
- 状态：`canonicalized-losslessly`
- milestone：`M6-achieved`

Phase 7 验收通过，**M6 — Canonical Schema 达到**。这里的“无损”是已恢复信息全部可回溯，不是所有游戏语义已经理解。

当前进入 **Phase 8 — Validation & Cross-checking**。下一门槛要求多张谱的结构/语义验证和可解释差异报告，不能只复用 map3 的单谱结论。

## 2026-08-11 — Phase 8 multi-chart validation

### 范围与方法

新增 `charts/validator.py` 与 `validate` CLI。结构检查包括 finite exact Decimal、非负 duration、`end=time+duration`、连续 index、时间顺序、source bundle 存在与 SHA-256，以及 raw record 的精确集合守恒。实际全库后来发现 `-0.482` / `-0.232` 的 finite raw pre-roll time，因此负 time 保真并产生 warning，不再作为结构错误。raw accounting 不是只比较数量：它从 `raw.experimental_chart.raw_records` 取得原始 index 集合，从 event raw records 与 `observed-sentinel` record groups 取得保留集合，并报告 missing、extra、duplicate 和 overlap。

语义部分输出 type、air/ground、hold、multi、unknown 分布，并使用本机静态 IL2CPP 与 NoteData 证实的 add-combo 类型 `{1,3,4,5,8}` 加 type 3 end record 做 aggregate combo 投影。没有使用任意“合理最大事件数”硬门槛。

公开参考均为 metadata-only：

- `urban_magic_map1`: 256，来源 `https://wikiwiki.jp/musedash/Urban%20Magic`；
- `urban_magic_map2`: 406，来源 `https://www.nicovideo.jp/watch/sm43719259` 的可见 Full Combo；
- `urban_magic_map3`: 568，来源 `https://www.bilibili.com/video/BV1ye4y127LM/?p=146` 的可见 Full Combo。

这些参考没有事件流。因此七类 event-level difference（matched、missing/extra、timing/type/lane/duration）全部明确为 `not_compared`；aggregate match 不能提升为 100% event accuracy。

### 实盘结果

三张谱均在 fingerprint `sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5` 上通过：

| chart | logical events | raw accounting | projected/reference combo | source SHA |
|---|---:|---:|---:|---|
| `urban_magic_map1` | 256 | 502 event records + 1 sentinel = 503 | 256 / 256 | verified |
| `urban_magic_map2` | 430 | 599 event records + 1 sentinel = 600 | 406 / 406 | verified |
| `urban_magic_map3` | 583 | 834 event records + 1 sentinel = 835 | 568 / 568 | verified |

`diagnostics/validation_report.json` 为 16,491 bytes，SHA-256 `c22260174ab2ebd6ab95f7024d77ead54998d13929a6ea1cc719d177cf002e2e`；Markdown companion 为 2,428 bytes，SHA-256 `6fb8f9d928747368572eb128a8898ad8f29ddd28f9a2ee90282c3f4a6a439a39`。状态为 `partially-validated-multiple-charts`、milestone 为 `M7-achieved`。

Phase 8 验收通过，**M7 — Verified 达到**。结论范围只覆盖当前 fingerprint 的三张 Urban Magic 谱及 aggregate reference；逐事件完整准确性、其他歌曲和其他版本仍未验证。

## 2026-08-11 — Phase 9 preflight grouping counterexamples

### 100-source 只读抽样

在进入批量导出前，按 source 路径均匀抽样 100 个不同 music bundle，每个读取第一个 StageInfo。严格 Odin parser 对 `100/100` payload 成功并消费到 EOF，但现有 `build_experimental_chart` 只通过 `7/100`：88 个失败于每个 `configData.id` 必须恰有一个 `endIndex==0` base 的假设，5 个失败于按 config id 排序后的时间回退。

具体反例：

- `brain_power_map3`: `configData.id=-1` 有三条独立普通记录，raw index `380/494/612`，time `72.648/83.679/94.845`，三条均 `endIndex=0`。因此 config id 不是全库唯一逻辑分组键。
- `heart_message_map3`: id 34 的普通 base 同时满足 `isLongPressing=false`、`isLongPressEnd=false`，但 `endIndex=875`；后续三个 pressing 和一个 end record 的 `endIndex=8980`。因此 `endIndex==0` 不是全库 base 判据。

### 路线调整

此证据不否定 StageInfo/Odin 原始格式恢复，但否定把 Urban Magic 分组规律直接推广到全库。ROADMAP Phase 9 已增加全量 metadata-only grouping census：先区分 raw parse 与 grouping family，再建立实证规则。Odin 成功但分组未知必须输出 `uncertain`；不得静默跳过，也不得生成错误 canonical event。

当前 Phase 为 **Phase 9 — Batch Extraction 的结构 census 子阶段**。M8 尚未达到。

## 2026-08-11 — Phase 9 full grouping census

### General grouping rule recovered

The 100-source counterexamples above led to two evidence-backed changes:

- a logical base is the unique record with both `isLongPressing=false` and
  `isLongPressEnd=false`; `endIndex==0` is not a valid general base test;
- every record with negative `configData.id` is an independent logical object,
  even when time, note UID, and pathway are identical. On this installation all
  7,782 negative-ID records are neutral base records and none is a pressing/end
  state.

For non-negative IDs, the grouping key retains exact decimal time, raw note UID,
and pathway in addition to the ID. State records must have exactly one of the two
long-press flags. Logical output is sorted by exact time with stable source-record
tie breakers; original record indices and order remain preserved as provenance.

The first complete census exposed two real negative raw times: `-0.482` in
`music_tyo_digital_tyo_detox_assets_all.bundle` (PathID
`4529025658330878100`) and `-0.232` in
`music_viyellas_scream_assets_all.bundle` (PathID
`-1816640552528157344`). These finite pre-roll values are retained with a
validation warning. They are not treated as corruption while the global timing
offset remains unknown.

### Non-null dialogEvents branch

The first complete pass strictly parsed 2,306 of 2,331 Odin payloads. All 25
failures stopped at the same proven branch: `dialogEvents` was a
`NamedStartOfReferenceNode` rather than `NamedNull`. A bounded structural probe
showed the same schema in all 25 payloads:

```text
Dictionary<string, List<Assets.Scripts.Structs.GameDialogArgs>>
```

Each `GameDialogArgs` contains `index`, exact decimal `time`, `dialogType`,
`dialogIndex`, nullable `text`, `textColor`, `bgColor`, float `speed`,
`fontSize`, `dialogSize`, `dialogState`, and `alignment`. Integer, decimal, and
float raw representations and all byte offsets are retained. The 25 payloads
contained 5,345,170 dialog bytes and 12,835 dialog arguments; the strict formal
reader consumed every payload to exact EOF with no unknown tag.

### Complete result

After adding the dialog branch and re-running from the real files:

- sources: 733;
- StageInfo candidates: 2,331;
- strict raw parses: 2,331 / 2,331;
- grouping successes: 2,331 / 2,331;
- raw MusicData records: 1,818,155;
- logical objects: 1,204,898;
- charts with an observed sentinel: 992;
- neutral bases whose `endIndex` is non-zero: 61,802.

The reproducible metadata-only artifacts are:

- `diagnostics/grouping_census.jsonl`: 2,226,817 bytes, SHA-256
  `ee2bfd68cc35579b42a8e85ffa34186f52f84a3b0d92b6576905d3e2e586c223`;
- `diagnostics/grouping_census_summary.json`: 719 bytes, SHA-256
  `5188af47279a62c87d72c8ccf604dfef35155c902cb69b2e426193760f9c9227`.

Grouping rule version is
`composite-neutral-base-negative-id-singleton-v2`. These diagnostics contain
counts, identities, hashes, and failure status only; they do not contain chart
events or serialized payload bytes.

The raw-parser/grouping gate for Phase 9 is now satisfied on fingerprint
`sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5`.
M8 is still **not achieved** until the deterministic batch engine produces a
complete manifest, preserves the unresolved `tutorial_v2_map1` as uncertain,
and passes two-run output/hash consistency checks.

## 2026-08-11 — Phase 9 deterministic batch extraction

### Batch gates

`extract-all` requires the completed grouping census rather than trusting the
length of an arbitrary candidate input. It verifies census status, complete
flag, fingerprint, grouping rule, candidate/source counts, and all-parsed /
all-grouped counters. Candidate chart IDs must exactly equal indexed IDs plus
explicit unresolved IDs. Every indexed bundle, SHA-256, container, PathID,
object type, Addressables primary key, and dependency path is cross-checked
against the independent candidate record.

The output tree is also fail-closed. Existing files outside the current full
plan stop the run. Before replacing the manifest, actual chart files must equal
the current success output paths exactly. A synthetic failed-rerun regression
proves an old complete manifest is not replaced while an old successful chart
would otherwise be stale.

### Two complete runs

Command:

```powershell
python -m musedash_chart_extractor extract-all `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output extracted `
  --grouping-census-summary diagnostics/grouping_census_summary.json
```

Both sequential runs processed 733 sources and classified all 2,331 candidates:

```text
success: 2330
uncertain: 1
failed: 0
strictly parsed: 2331
grouped: 2331
logical events: 1,204,898
```

`tutorial_v2_map1` is the only uncertain row: `song_id=null`, raw parse
`parsed`, grouping `grouped`, reason `song-identity-unresolved`. It has no
fabricated output path.

Each run took about 51.5 minutes. Both manifests are 2,607,371 bytes and have
the identical SHA-256
`2d989e36722966d1e04698dfe0d94c253b097a932435ed3adcc3bdcb9bf2425a`.
An independent post-run pass recomputed every successful file hash and found:

```text
expected files: 2330
actual files: 2330
missing / extra: 0 / 0
size mismatches: 0
SHA-256 mismatches: 0
total chart bytes: 24,737,874,119
```

These local official-derived outputs remain under Git-ignored `extracted/` and
must not be committed or redistributed. Phase 9 passes and **M8 is achieved**
for the exact current fingerprint.

## 2026-08-11 — Phase 10 public alpha boundary

Added the generic `ChartExporter` Protocol plus deterministic `JsonExporter`
and `CsvExporter`. JSON retains the complete canonical mapping. CSV is an
explicit flat view with exact Decimal-to-millisecond conversion and does not
replace raw/unknown evidence.

Added `MuseDashInstallation.open()`, which re-hashes the complete installation
and selects a parser only for a registered exact fingerprint. Unknown
fingerprints remain probe-only and raise `UnknownGameVersionError` on formal
extraction. `extract-all` now delegates through this public facade. Successful
chart files are exposed through a lazy `ExtractedChartCollection` iterator.

Canonical schema `1.1.0` adds `source.extractor_version`; all earlier bundle,
payload, game, catalog, Addressables, and raw provenance remains intact.

Release hardening includes CI on Python 3.10/3.13 and Windows/Linux, explicit
sdist exclusions for local artifacts, architecture/support/contribution docs,
CHANGELOG, and opt-in `local_game` tests. Verification at this point:

- non-local tests: 91 passed, 2 local tests deselected, 19 subtests passed;
- full suite with real local tests: 93 passed and 19 subtests passed in 28.84
  seconds, including a fresh full-install fingerprint and the anchored
  schema `1.1.0` 2,330-file M8 manifest/path/size/layout check;
- sdist and wheel built successfully; archive listings contain no
  `diagnostics/`, `experimental/`, or `extracted/` content.

Phase 10's local technical gate passes and the tree is **M9 release-ready**.
M9 itself is not yet achieved: Git reports no commits on `main`, all project
files are untracked, and there is no remote, real CI result, project URL, or
tag. Those repository-owner actions must not be fabricated. After they are
complete, the next research priority is a second real fingerprint and broader
independent event-level validation, not a GUI or downstream-specific adapter.

### Current-schema real-resource smoke

The two complete 24.7 GB M8 runs remain valid Phase 9 determinism evidence but
were generated under Canonical schema `1.0.0`. They were not relabelled as
`1.1.0`. Using the current code, the three retained Urban Magic raw charts were
canonicalized again from local evidence:

```text
urban_magic_map1: schema 1.1.0, 256 events
urban_magic_map2: schema 1.1.0, 430 events
urban_magic_map3: schema 1.1.0, 583 events, validated-first-chart
```

All three source objects were rechecked against the local installation. The
current validator reported three structural passes, three source SHA passes,
and three aggregate reference matches (`256`, `406`, `568` combo), with the
same explicit `not_compared` event-level categories. Each chart contains
`source.extractor_version=0.1.0`.

## 2026-08-11 — Canonical 1.1 single-table full refresh

### Duplication evidence and invariant

The retained Phase 5 document already contained the complete MusicData rows in
`raw_records` and complete grouping evidence in `record_groups`. Canonical
events duplicated both as `raw.music_data_records` and `raw.group`; Phase 5
`logical_objects` was itself exactly derivable by filtering non-sentinel
`record_groups` and assigning consecutive indices.

Schema `1.1.0` was still unreleased, so its pre-release layout was corrected
without another schema bump. The formal `single-raw-record-table-v1` invariant
is now:

```text
raw.experimental_chart.raw_records       one complete raw-record table
raw.experimental_chart.record_groups     one complete group table
events[].raw.base_raw_record_index       scalar reference
events[].raw.raw_record_indices           member references
```

`events[].raw.music_data_records`, `events[].raw.group`, and the derivable
embedded `raw.experimental_chart.logical_objects` are absent. Odin bytes,
unknown StageInfo fields, note configs, raw record bodies, index rows, and
validation evidence remain. Canonicalization reconstructs the original Phase
5 `logical_objects` and requires full mapping equality before writing output.
The validator rejects duplicate event payloads and requires event plus sentinel
indices to equal the original raw index set exactly.

### Three-chart regression

The human-readable Urban Magic canonical files changed as follows:

| chart | before bytes | after bytes | events |
|---|---:|---:|---:|
| `urban_magic_map1` | 16,472,552 | 10,760,652 | 256 |
| `urban_magic_map2` | 20,535,951 | 13,233,971 | 430 |
| `urban_magic_map3` | 28,461,631 | 18,356,855 | 583 |

All reconstruction, no-duplicate-payload, raw-index accounting, source SHA,
and aggregate reference checks passed.

### Complete refresh

The existing output tree was reused without deletion. One full current-schema
run completed in 2,657.7 seconds and reported:

```text
candidates: 2331
sources: 733
raw parsed: 2331
success / uncertain / failed: 2330 / 1 / 0
logical events: 1,204,898
canonical schema: 1.1.0
```

The new manifest is 2,606,521 bytes with SHA-256
`20d1bcd8f9a733614d9e0ab968abe855220c34e7e4bb2d2f390916d3426db4ea`.
The 2,330 chart files total 14,086,037,521 bytes, down 10,651,836,598 bytes
(`43.059%`) from the schema `1.0.0` tree.

An independent 297.9-second pass reread and hashed every chart. Missing,
extra, size, SHA-256, schema, layout, and event-reference-shape mismatch counts
were all zero. This is one complete real run of the new layout; the historical
two-run identical-manifest evidence belongs to schema `1.0.0`, while synthetic
tests cover current-layout determinism. The distinction is retained explicitly.

A separate full raw-accounting pass reconstructed the index relationships for
all 2,330 successful files. It accounted for 1,817,952 raw records as
1,204,824 gameplay events plus 991 sentinel records, with zero missing, extra,
duplicate, base-reference, group-reference, or count failures. The explicit
uncertain `tutorial_v2_map1` row contributes 203 raw records, 74 logical events,
and one sentinel. The complete manifest therefore closes exactly at 1,818,155
raw records, 1,204,898 logical events, and 992 sentinels without duplicating a
MusicData record body in an event.

## 2026-08-11 — M9 pre-publication gate audit

The target remote was confirmed as
`https://github.com/DDZmumo/MuseChartExtractor.git`, initially with no refs.
A pre-commit allowlist contained 63 text files and no game bundle, chart dump,
media, credential, or file larger than 1 MiB. Local `diagnostics/`,
`experimental/`, `extracted/`, `dist/`, and `build/` content remained ignored.

The audit found three release-boundary counterexamples before the initial
revision was created:

1. README exporter examples wrote to `exports/`, but that directory was not
   ignored. Git, sdist, and archive-audit guardrails now cover it, alongside
   common game-resource extensions and local credential files.
2. `candidates`, `inspect-stageinfo`, `extract`, `index`, and
   `grouping-census` could enter parser code on an unknown fingerprint even
   though formal batch extraction was gated. They now require the supported
   profile by default. `--allow-unsupported-research` is an explicit,
   diagnostic-only escape hatch for evidence collection. At the `v0.1.0`
   boundary, `extract-all` remained unavailable until profile registration;
   the later fail-closed research batch path is documented below.
3. The release archive checker used a suffix blacklist. It now uses separate
   wheel/sdist path allowlists, rejects links and special members, requires the
   license, and checks the distribution name/version in both metadata files.

CI now tests Python 3.10–3.13 on Windows and Linux. Packaging waits for that
matrix, audits and smoke-installs both artifacts, and retains those exact bytes
under the commit SHA. A `v*` tag only creates a GitHub Release after the same
test and package chain succeeds. These changes close local Phase 10 gaps; M9
still depends on the first pushed revision, its real CI result, and the
`v0.1.0` tag/Release.

Post-fix local verification completed with 98 tests and 26 subtests, including
both opt-in `local_game` checks against the 2,330-file schema `1.1.0` output.
`compileall`, dependency checks, `twine check`, allowlisted archive inspection,
and isolated wheel/sdist installs also passed. The installed wheel metadata
reported version `0.1.0` and the confirmed GitHub project URLs; both installed
CLI copies exposed the explicit unsupported-version research option.

### Public release evidence

The initial revision is
`91586403d07cfe95ccb325e5e9a9bc4a6fa9dcb0`. The `main` push workflow
`31459950516` completed successfully: all eight Windows/Linux and Python
3.10–3.13 test jobs passed, followed by the package job. Annotated tag
`v0.1.0` points to the same revision. Its independent workflow
`31460090699` again passed all eight tests, package, and release jobs.

GitHub Release `v0.1.0` was published at
`https://github.com/DDZmumo/MuseChartExtractor/releases/tag/v0.1.0` with the
exact artifacts from the successful tag workflow:

| artifact | bytes | GitHub/API SHA-256 |
|---|---:|---|
| `musedash_chart_extractor-0.1.0-py3-none-any.whl` | 91,205 | `b7b0c556c40a0cb68971a0c570e62be51727a9dab3b4cf9b0da288d63ef60cf8` |
| `musedash_chart_extractor-0.1.0.tar.gz` | 156,426 | `e51cacfbb84d948bb8bc8bb393201534df046715b750d46e95a260f39c3e4b3f` |

Both public files were downloaded again. Their local hashes matched the API
digests; `twine check`, the allowlisted member/type/license/name/version audit,
isolated installation, CLI version, and research-gate help smoke tests all
passed. This is external evidence for **M9 achieved**, not an inference from a
local build. No PyPI publication is claimed.

## 2026-08-11 — Second exact fingerprint, Phase 1–9 compatibility closure

### Acquisition and fingerprint boundary

Steam depot manifest `241392741196033182` for app `774171` / depot `774172`
was downloaded to Steam's separate content directory. Muse Dash was not
launched, the current installation was not overwritten, and no runtime memory,
mod, injection, or game-file mutation was used. Two complete scans were
byte-identical:

```text
inventory fingerprint:
sha256:d9108183177ac7c4821b466d28e0920d8a4a9bcd490a0edde956be3681233222
files:                 5,193
total bytes:           4,763,359,044
UnityFS:               5,069
resource inventory:    c04a389193d5db580608069c19216e555846a0fcddee66371acd2afa006927b4
resource summary:      f88abf76ce24fb086229e46c3a4b203ab35181d8725af3aa05a63be2f9156d50
```

The Addressables version remains `1.21.20`; the build-result hash is
`f4759f2e039525793e62c59c15df44c6`. A full Phase 2 probe opened all
5,072 sources with zero failures or warnings. The compact catalog contained
33,866 entries and 50,164 keys, and its 5,069 bundle IDs matched the inventory
5,069/5,069.

### Cross-build evidence and counterevidence

Compared with the newer `1821...f0ab5` fingerprint, this depot has 26 fewer
StageInfo charts in eight music sources. All 725 shared StageInfo sources are
byte-identical; none merely look structurally similar. The standalone
`notedata.json` bundle and global NoteDataManager bundle are also byte-identical
with SHA-256 values `28e322b8...4e91` and `a28ca81b...9598` respectively.

This is not evidence that the executables are identical. `GameAssembly.dll`,
`global-metadata.dat`, catalog, settings, and 47 logical config resources
changed. The second metadata still contains exact NUL-delimited Sirenix,
Odin, MusicData, MusicConfigData, NoteConfigData, grouping-field, and combo
identifiers, but their offsets moved. No first-build static address or method
offset is reused. The support decision is limited to the disk parser and
grouping family proven below.

### Phase gates

The second fingerprint independently passed the ordered gates:

```text
M1:  two stable full inventories
M2:  5,072 / 5,072 Unity sources parseable
M3:  2,305 scored StageInfo candidates in 725 sources
M4:  urban_magic_map3 strict EOF parse and byte-identity validation
M5:  2,304 indexed charts, 1 explicit unresolved chart
M6:  schema 1.1.0 lossless reconstruction checks all true
M7:  three Urban Magic charts passed source/structure/aggregate references
M8:  all 2,305 candidates classified by complete batch extraction
```

The selected M4 object is `urban_magic_map3`, PathID
`8668625138739021960`, in bundle SHA-256
`027bcaa714e3d04b42f0c6752046d6e71b37d8c400d439840a75033368357594`.
Its 481,388-byte Odin payload SHA-256 is
`e311b3f4c640428bd31596d5d1b2c7851bcf25618f4c9aa51176f1e24b698a18`.
Bundle, PathID, payload, and note configuration are byte-identical to the
previously video-validated object, so the earlier selected-frame evidence
applies to this exact disk chart. This transfer does not validate all changed
executable methods or every chart in the depot.

The complete grouping census has SHA-256
`17ebc944c12dd3272f84a31ab3c84ac27fef75a0fed5a10631c6237806e1c732`
for its summary and
`236b2d5ad6d171e7bbb679e19f444e0001a3ec4e65efa039e3fdd5b05137235e`
for its 2,305-row JSONL:

```text
strict raw parse:          2,305 / 2,305
grouping:                  2,305 / 2,305
raw records:               1,791,972
logical objects:           1,190,657
charts with sentinel:      987
negative config-id rows:   7,707
nonzero neutral endIndex:  60,146
```

### Schema 1.1 batch determinism

An explicit research-only batch path was added to break the evidence-gate
cycle without weakening the formal default. Unknown fingerprints still fail
formal extraction. `--allow-unsupported-research` requires the exact candidate,
song-index, source, and complete census fingerprint gates, and its manifest
states `profile_support.formal_support=false`. It cannot edit the support
registry.

Two complete runs overwrote the same output tree and produced the same bytes:

```text
candidates:                         2,305
success / uncertain / failed:       2,304 / 1 / 0
manifest logical events:            1,190,657
exported events:                    1,190,583
exported raw records:               1,791,769
chart files:                        2,304
chart bytes:                        13,884,429,834
tree bytes including manifest:      13,887,006,934
manifest bytes:                     2,577,100
manifest SHA-256:                   d893ca25bbb86683d3b27cdf016c594afc3406be9fd1432e5b2398298a0d94d2
```

The 74 logical objects and 203 raw records not present in exported files belong
to `tutorial_v2_map1`, which remains explicitly `uncertain` because its song
identity is unresolved. It is present in the manifest and census, not silently
discarded.

The strengthened auditor independently reopened and hashed all 2,304 files,
then checked the schema `1.1.0` raw layout, unique raw indices, gameplay and
sentinel groups, event/base references, and exact event-plus-sentinel set
closure. Both 1,961-byte audit reports were byte-identical with SHA-256
`319be38df3fc1079dd3638f4036e99ac83f9619c37caff0bb2ebe7cbe07d2713`.
All 15 mismatch categories were zero. This proves storage closure and
determinism; it does not promote M7 to full event-level semantic validation.

After this evidence closed, the exact fingerprint was registered with the same
observed Odin parser and grouping rule. The local old depot, its 12.933 GiB
canonical output, 0.074 GiB experimental files, and two interrupted partial
trees were removed to conserve disk space. The metadata-only diagnostics and
this record remain; no official chart or bundle is committed.

## 2026-08-11 — Latest schema 1.1 second-run closure

The current supported fingerprint
`sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5`
was extracted a second time in place, overwriting the existing chart tree rather
than retaining another 14 GB copy. The run completed in 2,297.1 seconds:

```text
candidates:                    2,331
sources:                       733
raw parsed:                    2,331 / 2,331
success / uncertain / failed: 2,330 / 1 / 0
manifest logical events:      1,204,898
canonical schema:             1.1.0
```

The second manifest is byte-identical to the manifest retained from the first
schema `1.1.0` run: 2,606,521 bytes, SHA-256
`20d1bcd8f9a733614d9e0ab968abe855220c34e7e4bb2d2f390916d3426db4ea`.
Because the manifest contains every output path, byte count, SHA-256, event
count, raw-record count, source identity, and status, this establishes exact
batch determinism without storing both output trees.

The second tree was then independently reopened with the strengthened auditor.
It covered 2,330 files / 14,086,037,521 bytes, 1,204,824 exported gameplay
events, and 1,817,952 exported raw records. All 16 mismatch categories were
zero, including the new fail-closed manifest-integrity category. The metadata-
only report is 1,830 bytes with SHA-256
`eb1987123b929ae807fb7503fc0554d70399b7c89247230d8e28f00ee9805d58`.
The first run's retained audit used the earlier 15-category tool; the reports
are therefore not described as byte-identical even though both passed and the
two independently generated manifests are byte-identical.

This is exact storage, identity, and structural-accounting evidence. It does
not change M7's partial status: full event-level timing/type/lane/duration truth
is still `not_compared`, `is_air` is not canonicalized, and
`tutorial_v2_map1` remains explicitly unresolved/uncertain.

## 2026-08-12 — Compact Odin Store 1.0 full acceptance

### Scope and unchanged evidence boundary

The Store migration used the current formal fingerprint:

```text
sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5
game directory: E:\SteamLibrary\steamapps\common\Muse Dash
Addressables:  1.21.20
build hash:    9ecc2d74a4045582f2aabf0f64c83581
catalog SHA:   61059d3983d68b9b9e06ca580155c56bdc378882d68ed6c4acd6894ce58d6242
settings SHA:  7dfad052f7bb4c3cd8aae77f6deadd3bf131d5bab7fdeaad21aefb1f0c258107
```

No new video or manual per-event review was possible in this run. The selected
M4 human evidence recorded earlier was not replaced or broadened. Store
acceptance instead proves that the storage migration preserves the exact disk
payload, source identity, parser result, grouping accounting, and previously
generated Canonical objects. M7 remains partial.

The second formal fingerprint `d910...33222` remains registered from its
completed Phase 1–9 evidence. Its old Steam depot was already removed under the
previous disk-space policy, so this run did not rebuild or claim a Store for
that unavailable resource set.

### Physical Store

`extract-store` was run directly against the game, not converted from the old
JSON tree. Every StageInfo `SerializedBytes` value was checked against its
candidate size/SHA, strictly parsed to EOF once, grouped, and written unchanged
as:

```text
MuseDashChartStore/payloads/sha256/<prefix>/<sha256>.odin
```

The SQLite index stores the complete StageInfo envelope after removing only
`SerializedBytes`. It also stores full metadata-only candidates, source
fingerprints, ALBUM/song/index rows, global note configs once per UID, and chart
to note-UID references. `tutorial_v2_map1` keeps its raw payload and is explicit
`uncertain`; its missing song identity is not converted into a false match.

Observed output:

```text
candidates / sources:             2,331 / 733
success / uncertain / failed:     2,330 / 1 / 0
payload count / bytes:            2,331 / 1,053,670,885
unique payload SHA count:         2,331
SQLite bytes:                     47,308,800
manifest bytes:                   595,949
Store files including audit:      2,334
Store bytes including audit:      1,101,577,861
raw records / logical events:     1,818,155 / 1,204,898
charts with observed sentinel:    992
logical Store digest:             0579d6943657c736bda9494f14a6c312ad44a2b9300b5ea858070a69aaa24668
```

All 2,331 payload SHA values are unique in this fingerprint, so content
addressing produces no within-version byte saving beyond avoiding repeated
expanded JSON. It still guarantees rerun and future cross-version deduplication.

The pre-cleanup schema `1.1.0` tree was measured at:

```text
D:\Projects\PythonP\MuseChartExtractor\extracted
files: 2,331
bytes: 14,088,644,042 (13.121072 GiB)
```

The Store is 7.8189% of that tree, below the 25% acceptance ceiling. The old
tree remained intact throughout Store construction, source audit, the first
determinism rerun, and the final streaming equivalence pass. It was eligible
for cleanup only after all of those gates passed; diagnostics and experimental
evidence were never cleanup targets.

### Independent Store audit

The fail-closed auditor processed payloads one at a time and did not retain a
library-wide byte/parse cache. With the game directory supplied it also
reopened all source bundles and compared PathID, object type, payload and
stripped StageInfo envelope:

```text
SQLite integrity_check:       ok
foreign key violations:       0
verified sources / charts:    733 / 2,331
candidate/chart/payload IDs:  exact
payload missing / extra:      0 / 0
raw / logical / sentinel:     1,818,155 / 1,204,898 / 992
all 13 mismatch categories:   0
```

The metadata-only report is 2,227 bytes with SHA-256
`c5a4c19b411fba35f130331720f1d33564a55f1b89fd23bccf376a8c6334426d`.
It is retained at both `diagnostics/store_audit.json` and the local-only
`MuseDashChartStore/audit/store_audit.json`; neither copy contains events or
payload bytes.

### Canonical 1.1 streaming equivalence

The comparison did not load the corpus into memory. For every successful chart
it verified the old manifest path/size/SHA, loaded one old JSON object, lazily
rebuilt the same chart from Store, encoded both with the same stable JSON
function, compared them, updated corpus hashes, and released the pair.

```text
success ID sets equal:             true
uncertain ID sets equal:           true (1 / 1)
compared / equivalent:             2,330 / 2,330
mismatch charts / total mismatch:  0 / 0
raw records on each side:          1,817,952
logical events on each side:       1,204,824
semantic bytes on each side:       14,086,035,191
canonical digest on each side:     621f8dbebabf388acce08e8cf6c54cbd1d3f5ea08c040e3af5dc4d42c52d67f7
```

The metadata-only equivalence report is 1,917 bytes with SHA-256
`e76d429685ece4c31c5adcab58326f01055a654800ad257b962ac3b71f428732`.
This proves the physical-format migration did not change Canonical `1.1.0`;
it does not prove that every game semantic field has been independently
interpreted.

### Same-directory determinism

A second `extract-store` run reused the same directory. During the run all
2,331 final payloads remained present and the staging payload count stayed zero;
the writer revalidated every existing payload size/SHA before reuse. The two
runs produced identical values:

```text
manifest:          595,949 bytes
manifest SHA:      53026764a56aa95fa6acb0204e6328b11ed630f7c55a8912154e3a7ce94d939d
SQLite:            47,308,800 bytes
SQLite SHA:        d3f653268a092f9356d5cb3948fa724d4abd3bef7300ae5f23011e79a7a49722
logical digest:    0579d6943657c736bda9494f14a6c312ad44a2b9300b5ea858070a69aaa24668
payload-set digest:2f00559c1b8761e0c8143eb384695eaab341007a0ad2581d152a0627cbf71533
```

All seven recorded determinism checks passed. This closes Phase 11 / M10 for
the current fingerprint while preserving the offline, read-only and generic
project boundary. These are the final values after rebuilding the same directory
with the fail-closed note-UID foreign key and parser/profile checks enabled; the
earlier pre-hardening index was not retained. The metadata-only determinism
report is 1,569 bytes with SHA-256
`e4ffa309aab38050d32017ef423ac3ffcb22c6b054a2c17313c3a26957ed4f32`.

### Post-acceptance cleanup and final safeguards

The final streaming equivalence run completed at 2026-08-12 03:42 local time
with the same 1,917-byte report and SHA-256
`e76d429685ece4c31c5adcab58326f01055a654800ad257b962ac3b71f428732`.
Only then was the exact literal path
`D:\Projects\PythonP\MuseChartExtractor\extracted` removed. Immediately before
deletion it resolved byte-for-byte to the expected path, contained 2,331 files
and 14,088,644,042 bytes, and had zero reparse points. The deletion did not use
the recycle bin. Afterwards the target no longer existed, while
`MuseDashChartStore/`, `diagnostics/`, and `experimental/` all remained.

The retained Store still has manifest SHA-256
`53026764a56aa95fa6acb0204e6328b11ed630f7c55a8912154e3a7ce94d939d`,
SQLite SHA-256
`d3f653268a092f9356d5cb3948fa724d4abd3bef7300ae5f23011e79a7a49722`,
and audit SHA-256
`c5a4c19b411fba35f130331720f1d33564a55f1b89fd23bccf376a8c6334426d`.
The final code review also added a fail-closed regression for nested symlinks or
Windows junctions in `.staging`; writer cleanup now rejects such a tree before
calling recursive deletion. The complete non-local suite after that change was
171 passed, 4 deselected, with 55 subtests passed.
