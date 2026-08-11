<div align="center">

# MuseDashChartExtractor

[![GitHub release](https://img.shields.io/github/v/release/DDZmumo/MuseChartExtractor?display_name=tag&sort=semver)](https://github.com/DDZmumo/MuseChartExtractor/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/DDZmumo/MuseChartExtractor/total?color=4c6ef5)](https://github.com/DDZmumo/MuseChartExtractor/releases/latest)
[![CI](https://github.com/DDZmumo/MuseChartExtractor/actions/workflows/ci.yml/badge.svg)](https://github.com/DDZmumo/MuseChartExtractor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/github/license/DDZmumo/MuseChartExtractor)](LICENSE)

**纯 Python、只读、离线的 Muse Dash 官方谱面提取器。**

从用户自行拥有的 Windows 版 Muse Dash 本地资源中定位、解析、验证并导出谱面，
不启动游戏，不安装 Mod，不注入 DLL，不读取运行时内存，也不修改游戏文件。

[快速开始](#快速开始) · [功能](#功能) · [工作原理](#工作原理) · [支持状态](#支持状态) · [项目文档](#项目文档)

</div>

---

## 快速开始

### 安装

需要 Python 3.10 或更高版本，以及用户自行安装的 Windows 版 Muse Dash。
当前项目通过 GitHub Releases 分发，尚未声明发布到 PyPI。

#### 稳定版 `v0.1.0`

该版本包含第一个正式资源 profile 和对应的 Phase 1-9 提取流程：

```powershell
python -m pip install "https://github.com/DDZmumo/MuseChartExtractor/releases/download/v0.1.0/musedash_chart_extractor-0.1.0-py3-none-any.whl"
musedash-chart-extractor --version
```

#### 当前 `main`（Unreleased）

第二个正式资源 profile、未知 fingerprint 的完整 research batch 和独立全量审计目前只在
当前源码中提供：

```powershell
git clone https://github.com/DDZmumo/MuseChartExtractor.git
cd MuseChartExtractor
python -m pip install -e ".[dev]"
```

除明确标注 `v0.1.0` 的内容外，下文描述当前 `main` 的能力。

### 扫描本地资源

安装目录可位于任意 Steam Library，不需要固定盘符：

```powershell
$GameDir = "D:\SteamLibrary\steamapps\common\Muse Dash"

musedash-chart-extractor scan `
  --game-dir $GameDir `
  --output-dir diagnostics
```

扫描只读取文件并生成稳定排序的 inventory 与完整内容 fingerprint：

- `diagnostics/resource_inventory.jsonl`
- `diagnostics/resource_summary.json`

### 完整提取

正式批量提取要求同一 fingerprint 下的 Unity inventory、StageInfo candidates、
song index 和 grouping census 全部通过门禁：

```powershell
musedash-chart-extractor probe --game-dir $GameDir --output-dir diagnostics
musedash-chart-extractor candidates --game-dir $GameDir --output-dir diagnostics
musedash-chart-extractor index --game-dir $GameDir --output diagnostics/song_chart_index.json
musedash-chart-extractor grouping-census --game-dir $GameDir --output-dir diagnostics
musedash-chart-extractor extract-all --game-dir $GameDir --output extracted
```

结果写入 `extracted/charts/<song_id>/<chart_id>.json`，最后原子写入
`extracted/manifest.json`。完整无损 JSON 体积较大：最新 fingerprint 的 2,330 张谱面
约占 13.1 GiB。重复验证应原地覆盖，不要保留重复输出树。

> [!IMPORTANT]
> `extracted/` 含用户本机官方衍生数据，已被 Git 忽略。请勿提交、发布或再分发完整谱面、
> AssetBundle、音频、Texture、DLC 内容或可还原这些内容的 dump。

全部子命令、输出文件和研究模式说明见
[CLI Reference](docs/cli-reference.md)。

## 功能

- **纯离线 Disk-to-Parser**：不依赖游戏进程、ModLoader、Hook 或内存读取。
- **确定性资源清单**：记录相对路径、大小、SHA-256、magic 与完整安装 fingerprint。
- **Unity / Addressables 解析**：枚举 bundle、serialized file、对象类型与 Addressables 关系。
- **严格 Odin Binary 恢复**：未知 tag、截断、计数或节点失配会带上下文失败，不猜格式。
- **可解释候选发现**：每个 candidate 保留 score、evidence、counterevidence、PathID 与来源哈希。
- **歌曲与难度索引**：从 Addressables、StageInfo 与 ALBUM 配置恢复稳定 song/chart 关系。
- **Canonical Chart schema 1.1**：单一 raw-record table，event 仅通过原始 index 引用。
- **未知信息保真**：未知字段与未知 type 不会被删除或强行映射。
- **验证与独立审计**：检查来源 SHA、Decimal、事件结构、raw index 闭包与全量文件 manifest。
- **通用输出接口**：内置 JSON、CSV 与 Python API，不绑定 MusePlay、YOLO 或 AutoPlay。
- **版本 fail-closed**：未知 fingerprint 默认只能 scan/probe，不会静默套用已知 parser。

## 工作原理

```mermaid
flowchart LR
    A["Muse Dash 本地资源"] --> B["Resource Scanner"]
    B --> C["Unity / Addressables"]
    C --> D["Chart Discovery"]
    D --> E["Odin Chart Parser"]
    E --> F["Song / Difficulty Index"]
    F --> G["Canonical Chart 1.1"]
    G --> H["Validation"]
    H --> I["JSON / CSV / Python API"]
```

项目始终遵循：**先证明，再抽象；先解析一张，再解析全部；先保真，再做转换。**
任何关于磁盘格式的结论都必须指向具体文件、bundle、PathID、对象类型、字段或可复现实验。

## 支持状态

| 项目 | 当前状态 |
|---|---|
| ROADMAP | Phase 0–10 完成，M0–M9 达到 |
| Canonical schema | `1.1.0` |
| 正式资源 profiles | 2 个 exact inventory fingerprints |
| 最新实盘 | 2,331 candidates；2,330 success；1 uncertain；0 failed |
| 最新全量输出 | 1,204,824 exported events；1,817,952 raw records |
| 确定性 | 两轮 manifest 逐字节相同，16 类最终审计 mismatch 均为 0 |
| 语义验证 | M7 partial，不宣称全库逐事件 100% 对照 |

正式支持按完整安装 fingerprint 判定，而不是按游戏营销版本或 Steam BuildID 猜测：

| Fingerprint | StageInfo | 结果 | 发布状态 |
|---|---:|---|---|
| `1821d79e…f0ab5` | 2,331 | 2,330 exported + 1 uncertain | `v0.1.0` |
| `d9108183…33222` | 2,305 | 2,304 exported + 1 uncertain | 当前源码，尚未发布 |

详细 fingerprint、Addressables build hash 与兼容边界见
[Supported Resource Versions](docs/supported-versions.md)。

> [!NOTE]
> “精确核对”目前表示磁盘来源、文件哈希、结构解析、raw-record accounting 和重复执行
> 确定性已闭环。独立逐事件参考尚未覆盖全部谱面，因此 timing/type/lane/duration 的全库
> 比较仍明确为 `not_compared`；`tutorial_v2_map1` 也继续保留为 unresolved/uncertain。

## Python API

```python
from musedash_chart_extractor import CsvExporter, JsonExporter, MuseDashInstallation

game = MuseDashInstallation.open(r"D:\SteamLibrary\steamapps\common\Muse Dash")
charts = game.extract_charts(
    output_dir="extracted",
    diagnostics_dir="diagnostics",
)

for chart in charts:
    JsonExporter(indent=None).export(chart, f"exports/{chart['chart_id']}.json")
    CsvExporter().export(chart, f"exports/{chart['chart_id']}.csv")
```

`JsonExporter` 保留完整 Canonical Model。`CsvExporter` 是明确的扁平事件视图，
不会修改或替代内部 raw/unknown 数据。`extract_charts()` 使用已生成的 diagnostics 门禁文件；
完整顺序见 [CLI Reference](docs/cli-reference.md#完整批量提取流程)。

## 项目文档

| 文档 | 内容 |
|---|---|
| [ROADMAP.md](ROADMAP.md) | 主执行规范、Phase 验收门槛与里程碑 |
| [CLI Reference](docs/cli-reference.md) | 全部命令、输出与本地研究流程 |
| [Canonical Schema](docs/schema.md) | schema `1.1.0`、raw 引用与保真规则 |
| [Supported Versions](docs/supported-versions.md) | 正式 fingerprints 与未知版本策略 |
| [Validation Scope](docs/validation.md) | 结构、aggregate 与逐事件验证边界 |
| [Architecture](docs/architecture.md) | 模块职责与 Disk-to-Parser 边界 |
| [Reverse-engineering Notes](docs/reverse-engineering-notes.md) | 真实文件证据、反例与研究记录 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、fixture 与证据要求 |
| [CHANGELOG.md](CHANGELOG.md) | 版本和未发布变更 |

## 开发与测试

```powershell
python -m pip install -e ".[dev]"
python -m pytest -m "not local_game" -q
python -m compileall -q src tests tools
python -m build
python tools/audit_release_archives.py dist/*
```

真实安装测试通过 `MUSEDASH_GAME_DIR` 显式启用；CI 不需要也不会获得 Muse Dash 资源。
贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 这是不是 Mod 或运行时工具？

**不是。**

MuseDashChartExtractor 只读取用户指定的本地安装文件。它不会启动 Muse Dash、安装 Mod、
注入 DLL、扫描内存、调用运行时组件或修改游戏文件。项目也不提供游戏资源下载、DRM 绕过
或官方内容再分发能力。

---

源码以 [MIT License](LICENSE) 发布。Muse Dash 及其相关名称和资产归各自权利人所有；
本项目与游戏开发商或发行商无隶属关系，也不代表其立场。
