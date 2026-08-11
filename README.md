# MuseDashChartExtractor

MuseDashChartExtractor 是一个纯 Python、只读、离线的 Muse Dash 官方谱面提取研究项目。

项目目标是从用户自行拥有的 Windows 版 Muse Dash 本地安装资源中，定位、解析、验证并导出官方谱面数据。它不会启动游戏、安装 Mod、注入 DLL、读取运行时内存或修改游戏文件，也不提供任何官方资源下载或再分发。

> 当前状态：Phase 0–10 和 M0–M9 已在一个 exact 本机资源 fingerprint 上达到。首个公共版本 [v0.1.0](https://github.com/DDZmumo/MuseChartExtractor/releases/tag/v0.1.0) 对应 revision `9158640`，其 Windows/Linux、Python 3.10–3.13 测试、package 和 release jobs 均由 GitHub Actions 真实通过。2,331 张 StageInfo 谱面全部可严格离线解析和分组；2,330 张已恢复 song/chart 身份并导出，`tutorial_v2_map1` 明确保留为 unresolved/uncertain。历史 schema `1.0.0` 的两轮完整批量运行得到字节相同的 manifest；当前 Canonical Chart schema `1.1.0` 已完成一次全量刷新和独立逐文件审计。M7 仍只是三张 Urban Magic 谱的 source、结构、raw accounting 与 aggregate combo 部分验证，不是全库逐事件 100% 精确声明。正式支持仅限 [supported-versions.md](docs/supported-versions.md) 列出的完整 fingerprint。

## 开发路线

严格按 [ROADMAP.md](ROADMAP.md) 推进：先建立可复现的资源 inventory，再调查 Unity / Addressables、发现候选、恢复序列化结构，最终以离线解析出第一张真实官方谱面作为首个核心成功门槛。

## 环境

- Python 3.10+
- 用户自行拥有并安装的 Windows 版 Muse Dash（只有本机资源调查需要）

资源 inventory 本身只使用 Python 标准库；Phase 2 的只读 Unity metadata probe 使用 UnityPy。开发环境可安装：

```powershell
python -m pip install -e ".[dev]"
```

`0.1.0` 的发布状态以项目的
[GitHub Releases](https://github.com/DDZmumo/MuseChartExtractor/releases) 为准。
本地构建成功不等于公共发布，也不表示已经上传到 Python 包索引：

```powershell
python -m build
python -m pip install dist\musedash_chart_extractor-0.1.0-py3-none-any.whl
musedash-chart-extractor --help
```

首个 GitHub Release wheel 可直接安装；项目当前未声明已发布到 PyPI：

```powershell
python -m pip install "https://github.com/DDZmumo/MuseChartExtractor/releases/download/v0.1.0/musedash_chart_extractor-0.1.0-py3-none-any.whl"
```

## 使用

从源码运行帮助：

```powershell
$env:PYTHONPATH = "src"
python -m musedash_chart_extractor --help
```

生成只读资源 inventory：

```powershell
$env:PYTHONPATH = "src"
python -m musedash_chart_extractor scan `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output-dir diagnostics
```

输出：

- `diagnostics/resource_inventory.jsonl`
- `diagnostics/resource_summary.json`

summary 包含一个与安装盘符无关的组合 SHA-256 fingerprint；它由稳定排序的相对路径、文件大小和各文件 SHA-256 共同生成，用于判断两次扫描是否对应同一组本地资源。

Phase 2 metadata probe（只枚举，不导出对象内容）：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor probe `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output-dir diagnostics
```

额外输出：

- `diagnostics/bundle_inventory.jsonl`
- `diagnostics/serialized_file_inventory.jsonl`
- `diagnostics/object_type_summary.json`
- `diagnostics/addressables_index.json`

生成有来源、可解释评分的 StageInfo 候选清单：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor candidates `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output-dir diagnostics
```

输出 `diagnostics/chart_candidates.jsonl`。评分表示当前结构信号中实际出现的比例，不是“属于谱面的概率”；每条记录均保留 source SHA-256、container、PathID、MonoScript 身份、证据和反证，但不会写出 `SerializedBytes`。

严格恢复一个候选对象的 Odin 结构并生成有限样本诊断：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor inspect-stageinfo `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --source "MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/music_urban_magic_assets_all.bundle" `
  --path-id 8668625138739021960 `
  --output-dir diagnostics
```

输出 `diagnostics/field_hypotheses.jsonl`。解析器只接受已由实际字节证明的 Odin tag、类型和字段顺序；未知 tag、截断、计数不符或节点失配会产生带 offset 和字段上下文的错误，不会猜测后继续。

Phase 5 的本地实验提取：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor extract `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --source "MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/music_urban_magic_assets_all.bundle" `
  --path-id 8668625138739021960 `
  --output experimental/first_chart.json
```

`experimental/first_chart.json` 是本机官方数据、已被 Git 忽略，当前 schema 不稳定且未 canonicalize。它保留完整 StageInfo envelope、原始 Odin 记录、`notedata.json` 中实际使用的配置以及未知字段；逻辑对象按 exact `configData.time`、config ID 和原始 base index 稳定排列，原始 record index 仍作为 provenance 保留。它不能作为稳定导出格式，也不得提交仓库。无媒体内容的 M4 验证摘要位于 `diagnostics/first_chart_validation.json`。

恢复歌曲、难度和 chart 索引：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor index `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output diagnostics/song_chart_index.json
```

索引器以 Addressables StageInfo primary key 为 `chart_id`、ALBUM `uid` 为 `song_id`、末尾 `_mapN` 为 `difficulty_id`；它会校验唯一 bundle dependency 和 source SHA。无法连接的 chart 保留在 `unresolved_charts`，不会被静默跳过。真实 ALBUM 文件包含注释和尾逗号，因此由 JSON5 parser 读取后再做严格字段验证。

将 Phase 5/6 证据转换为 Canonical Chart：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor canonicalize `
  --raw-chart experimental/first_chart.json `
  --song-index diagnostics/song_chart_index.json `
  --validation-report diagnostics/first_chart_validation.json `
  --output experimental/first_chart_canonical.json `
  --report diagnostics/canonicalization_report.json
```

Canonical JSON 仍含本机官方数据并保持 Git ignored。`1.1.0` 使用单一 raw-record table，event 仅通过原始 index 引用，不再重复嵌入完整 record/group；稳定字段、可重建无损规则和 unknown/raw 保真边界见 [docs/schema.md](docs/schema.md)。`canonicalization_report.json` 只保存哈希、计数和无损检查，不保存事件内容。

对一张或多张 Canonical Chart 做结构、来源和独立数量参考验证：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor validate `
  --chart experimental/urban_magic_map1_canonical.json `
  --chart experimental/urban_magic_map2_canonical.json `
  --chart experimental/first_chart_canonical.json `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --reference-file diagnostics/validation_references.json `
  --output diagnostics/validation_report.json `
  --markdown-output diagnostics/validation_report.md
```

验证器严格检查 exact Decimal、事件顺序、duration/end、source SHA-256，以及原始 record index 集合是否被 event 与 sentinel 精确覆盖。当前公开参考只提供最终 combo，因此报告中的 `matched`、`missing_offline`、`extra_offline`、`timing_delta`、`type_mismatch`、`lane_mismatch`、`duration_delta` 七类逐事件差异都明确为 `not_compared`；aggregate 数量一致不会被描述成逐事件一致。

Phase 9 的全量 metadata-only census 已完成：733 个 bundle 中的 2,331 个 StageInfo 均严格解析到 EOF，并通过 `composite-neutral-base-negative-id-singleton-v2` 分组规则。`configData.id < 0` 的记录逐条独立；base 由两个 long-press state flag 均为 false 判定，不再使用已被反例推翻的 `endIndex==0` 假设。

批量导出：

```powershell
.\.venv\Scripts\python.exe -m musedash_chart_extractor extract-all `
  --game-dir "E:\SteamLibrary\steamapps\common\Muse Dash" `
  --output extracted
```

`extract-all` 会先重新扫描完整安装 fingerprint，再要求 candidates、song index 和 grouping census 精确一致。输出位于 `extracted/charts/<song_id>/<chart_id>.json`，并在最后写 `extracted/manifest.json`。当前支持 fingerprint 的实盘结果为 2,330 success、1 uncertain、0 failed、1,204,898 logical events；输出及 manifest 均为本机官方衍生数据，已被 Git 忽略，不得再分发。

历史 Canonical schema `1.0.0` 曾完成两轮全库确定性验证。当前 release
candidate 已原地完成一次 `1.1.0` 全量刷新：2,330 个文件共
14,086,037,521 bytes，manifest 为 2,606,521 bytes、SHA-256
`20d1bcd8f9a733614d9e0ab968abe855220c34e7e4bb2d2f390916d3426db4ea`。
独立审计重新读取并哈希全部文件，missing / extra / size / SHA / schema /
layout / event-reference mismatch 均为 0。相比旧树减少 10,651,836,598 bytes
（43.059%）。当前 `1.1.0` 全库只运行一次；不把历史 `1.0.0` 的双跑 hash
冒充为新布局的双跑证据。

## Python API 与 Exporter

```python
from musedash_chart_extractor import (
    CsvExporter,
    JsonExporter,
    MuseDashInstallation,
)

game = MuseDashInstallation.open(r"E:\SteamLibrary\steamapps\common\Muse Dash")
charts = game.extract_charts(output_dir="extracted", diagnostics_dir="diagnostics")

for chart in charts:  # 从本地 JSON 逐张惰性读取
    JsonExporter(indent=None).export(chart, f"exports/{chart['chart_id']}.json")
    CsvExporter().export(chart, f"exports/{chart['chart_id']}.csv")
```

`JsonExporter` 保留完整 Canonical Model。`CsvExporter` 是明确的扁平事件视图，使用 exact Decimal 计算毫秒值；它不会修改或替代内部 raw/unknown 数据。未知 fingerprint 的 `open()` 仍可返回安装对象用于判断；正式提取会抛出 `UnknownGameVersionError`。默认只能先用显式的 `scan` / `probe` 研究命令收集证据，不能强套已知 parser。需要继续做候选或结构研究时必须显式传入 `--allow-unsupported-research`；这只产生 diagnostic evidence，不会把该版本注册为正式支持。

默认诊断输出位于当前工作目录的 `diagnostics/`。为保证只读边界，任何输出目录或文件都不能位于游戏安装目录内部。

## 测试

无需游戏资源的测试：

```powershell
pytest -m "not local_game" -q
```

真实安装测试只读运行，并通过环境变量显式启用：

```powershell
$env:MUSEDASH_GAME_DIR = "E:\SteamLibrary\steamapps\common\Muse Dash"
$env:MUSEDASH_EXTRACTED_DIR = (Resolve-Path extracted).Path
pytest -m local_game -q
```

这类测试不会要求 CI 拥有 Muse Dash，也不会上传本地资源。

## 法律与数据边界

本仓库只包含提取器源码、文档和人工构造 fixture。请勿提交完整官方谱面、AssetBundle、歌曲音频、Texture、DLC 内容或可还原这些内容的 dump。用户必须自行拥有并指定本机 Muse Dash 安装。

## License

源码以 [MIT License](LICENSE) 发布。Muse Dash 及其相关名称和资产归各自权利人所有；本项目与游戏开发商或发行商无隶属关系。
