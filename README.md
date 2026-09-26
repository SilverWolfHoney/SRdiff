# SRdiff

《崩坏：星穹铁道》**国服**客户端升级工具，两件事：

1. **全量升级**：用官方 **Sophon（chunk）** 分发机制，把任意旧版本客户端升到官方当前版本。
2. **导出官方差分包**：用官方 **ldiff（hdiffpatch）** 差分机制，把「本地版本 → 目标版本」的补丁**只下载、不动客户端**，打包成一个可以分享给别人的差分包。

两者都**只用 Python 标准库 + 少量 pip 包**，不需要 hpatchz / hdiffz / SophonPatcher 等外部 exe，游戏目录里也**不需要预先放任何文件**。

> ⚠️ 仅供学习与个人客户端维护使用。仓库**不含**任何游戏本体或官方资源，全部文件在运行时从官方 CDN 下载。请自行承担使用风险。

## 目录结构

```
SRdiff/
├── sophon_update.py        # 工具一：全量升级（就地更新客户端）
├── sophon_ldiff.py         # 工具二：导出官方差分包（只下载，不改客户端）
├── apply_ldiff.py          # 工具二配套：把导出的差分包合并到客户端（发给别人用这个）
├── build_exe.py            # 把 apply_ldiff.py 打包成单文件 exe（可选）
├── manifest.proto          # Sophon 清单（chunk）的 proto 定义
├── manifest_pb2.py         # 由 manifest.proto 生成（已提交，日常不用管）
├── manifest_ldiff.proto    # 官方差分包（ldiff）清单定义
├── manifest_ldiff_pb2.py   # 由 manifest_ldiff.proto 生成
└── gen_pb2.py              # 改了 .proto 后才需要跑：重新生成上面的 *_pb2.py
```

## 依赖

```bash
pip install protobuf zstandard           # 工具一（全量升级）
pip install hdiffpatch                   # 工具二的应用端（apply_ldiff.py）才需要
```

- Python 3.8+（开发环境为 3.14）。
- `hdiffpatch` 只在**你自己这边**需要：应用差分包（方式 B）、或打包 exe（`build_exe.py`）。它是 HDiffPatch C++ 库的 Python 封装（Apache-2.0），**有 Windows / macOS / Linux 预编译 wheel**（含 cp314），装完即用，**不需要 hpatchz.exe**。
- **接收方用打包好的 `SRdiff_apply.exe` 时，什么都不用装**（Python、pip、依赖全部已在 exe 内）。
- `grpcio-tools` **只有**在修改 `.proto` 后重新生成 `*_pb2.py` 时才需要：`pip install grpcio-tools && python gen_pb2.py`。
- 生成的 `*_pb2.py` 捆绑了 protobuf 运行时版本校验；若你的 `protobuf` 版本过旧导致导入报错，用 `gen_pb2.py` 重新生成即可（它会使用你本机的 protoc）。

## 用法

### 一、全量升级（就地更新客户端）

先看清要下多少（只看不写，也不联网下大文件）：

```bash
python sophon_update.py --gamedir "<客户端根目录>" --cn --dry
```

确认没问题后正式升级：

```bash
# ★中文版（推荐）：游戏资源(10054) + 中文语音(10055)，不装英/日/韩
python sophon_update.py --gamedir "<客户端根目录>" --cn

# 全量：游戏资源 + 中/英/日/韩 全部语音
python sophon_update.py --gamedir "<客户端根目录>"

# 预下载尚未开服的新版本（比正式版高一档，先下好等开服）
python sophon_update.py --gamedir "<客户端根目录>" --cn --branch predownload

# 只升单类
python sophon_update.py --gamedir "<客户端根目录>" --cat 10054   # 游戏资源
python sophon_update.py --gamedir "<客户端根目录>" --cat 10055   # 中文语音
```

- `--gamedir`：**要升级的客户端根目录**，即含 `StarRail_Data\` 的那个文件夹（必填，填你自己的路径）。
- `--cat`：只跑指定类别（如 `10054`）；缺省 = 自动遍历该版本的全部类别。优先级高于 `--cn`。
- `--cn`：中文版模式，只跑 游戏资源(10054) + 中文语音(10055)。
- `--branch`：`main`（默认，官方**已上线**版本）/ `predownload`（官方**预下载**版本，接受 `pre_download` 写法）。
- `--dry`：只统计文件数与下载体积，不下载、不写文件。
- `--verify`：强制逐文件 md5 校验（慢，会读整个客户端），不使用清单比对。

跑完后工具会自动把 `config.ini` 的 `game_version` 写成目标版本；**只在 `--dry` 之外**会顺带询问是否清理 manifest 缓存。

### 二、导出官方差分包（只下载，不动客户端）

先看要下多少（不下载、不写文件）：

```bash
python sophon_ldiff.py --gamedir "<客户端根目录>" --out "<差分包目录>" --cn --dry
```

确认后正式导出：

```bash
# ★中文版（推荐）：游戏资源(10054) + 中文语音(10055)
python sophon_ldiff.py --gamedir "<客户端根目录>" --out "<差分包目录>" --cn

# 全部类别
python sophon_ldiff.py --gamedir "<客户端根目录>" --out "<差分包目录>"

# 只导出单类
python sophon_ldiff.py --gamedir "<客户端根目录>" --out "<差分包目录>" --cat 10054
```

- `--gamedir`：**只读它的 `config.ini`** 来判断源版本（例如 `4.4.0`），**不读也不改任何游戏文件**。
- `--out`：差分包输出目录（会创建）。
- `--branch` / `--cn` / `--cat`：含义同工具一。
- `--src`：强制指定源版本（默认从 `config.ini` 读）。
- `--limit N`：每个类别只取前 N 个补丁池（用于小规模试跑；导出的是**不完整**的包，正式用别加）。按「补丁池」而不是按文件截断，保证池内补丁段的偏移语义始终成立。
- **一次导完你要的全部类别**：所有类别写进同一个 `--out` 目录、共用一份 `manifest.json`，分次导出到同一目录会互相覆盖（每次都会重置清单与池文件）。

导出结果是自包含的一个目录：

```
<差分包目录>/
├── manifest.json     # 补丁记录：目标文件、md5、所在池、偏移、长度、源文件 md5
├── pool/             # 补丁池（每个文件含若干补丁段，段位置见 manifest.json）
└── 如何应用.txt       # 给接收方的说明
```

把它和 `apply_ldiff.py` 一起打包发给别人即可。接收方有两种用法：

**方式 A：直接用 exe（对方什么都不用装）**

把 `SRdiff_apply.exe` 和差分包一起发过去，双击运行，按提示分别填「客户端根目录」和「差分包目录」（可以直接把文件夹从资源管理器拖进窗口）：

```
SRdiff_apply.exe                              # 双击：交互式询问两个路径
SRdiff_apply.exe --gamedir <客户端> --pack <差分包>   # 或命令行
```

exe 里已经打进了差分包应用所需的全部依赖（含 HDiffPatch 原生代码），**接收方不需要 Python、不需要 pip、不需要任何外部 exe**。

自己生成这个 exe（约 9.3 MB）：

```bash
pip install pyinstaller hdiffpatch
python build_exe.py            # 产物: dist\SRdiff_apply.exe
```

**方式 B：直接跑 Python 脚本**

```bash
pip install hdiffpatch
python apply_ldiff.py --gamedir "<他的客户端根目录>" --pack "<差分包目录>" --dry   # 先预览
python apply_ldiff.py --gamedir "<他的客户端根目录>" --pack "<差分包目录>"         # 正式合并
```

两种方式行为完全一致：会**先校验每个源文件的 md5**（对不上就跳过并报出来，绝不写坏文件），打完补丁再校验目标 md5，通过才替换原文件；可中断、可重跑。

## 原理

官方 Sophon 按 **chunk** 分发文件，流程是：

1. `getGameBranches` → 拿到目标版本（如 `4.5.0`）的 `package_id` / `password` / `branch` / `tag`。
2. `getBuild` → 该版本所有类别的清单，每个文件含它的 chunk 列表、下载基址和 md5。
3. 每个文件由若干 chunk（zstd 压缩片段）组成，按各自 `offset` 拼起来就是完整文件。

工具遍历清单，对「当前安装里**缺失或内容不符**」的文件：**并发下载 chunk → zstd 解压 → 按偏移组装 → md5 校验 → 写回**；已正确的直接跳过。

所以**删掉「过往资源」也不影响**：缺什么就按目标版本清单从官方重建，不依赖本地旧文件。

### 差分（ldiff）

同一个目标版本，官方另外提供一套**差分包**（`getPatchBuild` 接口）：

1. 差分清单（`DiffManifest`）为每个**改动过**的文件列出若干补丁，按源版本 `key` 区分（例如只有 `4.4.0`）。
2. 每条补丁给出它在 CDN 上某个**补丁池文件**里的位置：`patch_id` + `patch_offset` + `patch_length`。
3. 补丁段本身是标准 HDiffPatch 格式（`HDIFF13&` + zstd），用 `hdiffpatch` 直接应用即可，**不需要 hpatchz.exe**。
4. 官方 CDN 支持 HTTP Range，所以只下**补丁段**，不必为整个补丁池付流量。

以 `4.4.0 → 4.5.0` 实测（国服）：

| 类别 | 全量下载 | 走差分只下补丁段 | 备注 |
|---|---|---|---|
| 10054 游戏资源 | 90.17 GiB | **6.98 GiB** | 改动 2604 个文件 |
| 10055 中文语音 | 11.20 GiB | **0.48 GiB** | 改动 144 个文件 |

## 差分包的限制（重要）

- **只能升一档**：官方只对 `main.diff_tags` 列出的那一个相邻版本出补丁（`4.5.0` 只认 `4.4.0`）。`4.3.0 → 4.5.0` 必须走全量升级，没有跨版本差分包。
- **源文件必须原样**：补丁按源文件内容生成，源文件被改过（汉化 / MOD / 破解）就打不上。应用端会明确报出这些文件，让它们走全量更新即可。
- 新增文件没有补丁，仍要全量下载，所以差分省的是「改动文件」那部分。
- 源文件本就不存在时（官方清单里 `original_hash` 为空），补丁自带全部内容，可直接重建。

## 类别

| 类别 | 内容 |
|---|---|
| 10054 | 游戏资源（主体，必需） |
| 10055 / 10056 / 10057 / 10058 | 中 / 英 / 日 / 韩 语音 |

只升 10054 游戏就能进；语音包可选，缺了只影响对应语言的配音。

## 为什么有时升不到「最新版本」

官方同时给两套目标：`main`（**已上线**，能进游戏）和 `pre_download`（**预下载**，还没开服）。
新版本开服前 `main` 停在上一版本（于是显示 `4.4.0 → 4.5.0`），新版本只存在于 `pre_download`。
工具默认走 `main`，因此默认只升到「已上线的最新版」；要提前拿新版就加 `--branch predownload`。

> 预下载装完**要等官方开服才能进游戏**；开服前启动器显示旧版本属正常。

## 性能与健壮性

- **清单比对（快）**：读本地 `config.ini` 的版本号，取该版本的**官方清单**与目标清单比 md5，判断哪些文件要更新——3 秒出结果，**不读本地 100GB**。本地版本不在官方分支列表时才退回逐文件校验。
  > 代价：清单比对假定本地文件与官方一致。若装过汉化 / MOD / 破解改动，被改过的文件会被判为「已是最新」而跳过；要保底请加 `--verify` 强制逐文件校验。
- **并发下载**：chunk 多线程下载+解压（`WORKERS = 12`，文件顶部可调），线程池按类别复用；慢网络靠并发提速。
- **自动重试**：`DL_RETRIES = 6`，偶发断连 / SSL 错误自愈。
- **磁盘预检**：正式下载前比对目标盘剩余空间与待写入体积，不够会告警，避免下到一半写失败。
- **断点续传**：已写对的文件自动跳过，中断后重跑接着补。
- **完整性校验**：每个文件按官方 md5 校验，不通过则告警跳过，绝不写入错误内容。
- **进度输出**：先给出「需要组装的文件数」和「需下载体积」，之后每个文件一行 `[时间戳] 已组装 X / Y · 文件名`（Y 为该类别真正要更新的文件数，不是清单总数）；每类结束与整体结束都显示耗时。
- **manifest 缓存**：`sophon_update.py` 与 `sophon_ldiff.py` 共用 `%LOCALAPPDATA%\sophon_update_cache`（取不到时退 `%TEMP%`），重跑不重复下载大清单；工具一跑完会询问是否清理。
- **差分包**：按补丁池分组、只下补丁段（HTTP Range），已下完的池自动跳过（断点续传）；导出结束会自检每条记录的目标 md5 与目标版本官方清单是否一致，不一致直接报错，避免发出坏包。
- **合并端**：应用前校验源文件 md5（对不上就跳过并列出，绝不写坏文件），应用后再校验目标 md5 与大小，通过才落盘。

## 说明 / 注意

- 只更新「缺失或变化」的文件，下载体积 ≈ 一次真实更新。
- 覆盖前会替换同名文件，**建议先整体备份客户端**；跑完启动游戏确认，若还触发少量资源下载，交给官方启动器补即可。
- 工具从官方 `getGameBranches` 自动读取目标版本，**同一工具可反复用于后续升级**（4.5→4.6、4.6→4.7…），新版本开服自动生效，无需改代码。
- 程序按 `hkrpg_cn` **国服**写死（`GAME_ID` / `LAUNCHER_ID` / 语音类别 id）。其它区服或其它米哈游游戏需改文件顶部这几个常量。
- 下载走官方 CDN，速度取决于你的网络；某些地区可能需要代理。
- `sophon_ldiff.py` **全程不碰客户端**：只读 `config.ini` 拿版本号，不读游戏文件、不写任何游戏内容，导出物全部落在 `--out` 目录里。分享给别人的包必须包含 `apply_ldiff.py`。

## 已知限制 / 技术债

- 清单比对不适合被改动过的客户端（见上，用 `--verify` 兜底）。
- md5 校验失败的文件会被跳过，且下次重跑仍会被判为「需要更新」而重新下载（不会误标为已完成）。
- 单个文件需整体放进内存（超大文件峰值内存 ≈ 该文件大小）；差分应用同理（源文件与补丁段都在内存里）。
- `apply_ldiff.py` 目前是**顺序**应用补丁（未多线程），改动文件多时比下载慢，但可中断重跑。
- 差分包**只能覆盖 `diff_tags` 里那一个源版本**，无法为多个历史版本一次性出包（官方不提供）。
- 应用端依赖 `hdiffpatch`（Apache-2.0）；若不想多一个依赖，也可把官方补丁段交给 `hpatchz` 处理，但需要按 `patch_offset`/`patch_length` 自行切段（hpatchz 不接受尾部有额外数据）。

## License

MIT，见 [LICENSE](LICENSE)。仓库内 `manifest_ldiff.proto` 的注释保留了上游出处（DGP-Studio/Snap.Hutao，MIT）；应用端依赖的 [hdiffpatch](https://pypi.org/project/hdiffpatch/) 为 Apache-2.0。
