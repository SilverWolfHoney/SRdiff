# SRdiff

《崩坏：星穹铁道》**国服**客户端升级工具，两件事：

- **全量升级** —— 按官方 Sophon（chunk）分发，把任意旧版本客户端升到当前版本。
- **导出差分包** —— 按官方 ldiff（hdiffpatch）差分，把「低版本 → 高版本」的更新**只下载、不动客户端**，打包成能发给别人的离线升级包。

只用 Python 标准库 + 少量 pip 包，**不需要 hpatchz / hdiffz / SophonPatcher 等任何外部 exe**。

> ⚠️ 仅供学习与个人客户端维护使用。仓库**不含**任何游戏本体或官方资源，全部文件在运行时从官方 CDN 下载。

## 安装

```bash
pip install protobuf zstandard     # 全量升级 / 导出差分包
pip install hdiffpatch             # 应用差分包（用 exe 的接收方不需要）
```

Python 3.8+（开发环境 3.14）。

## 文件

| 文件 | 用途 |
|---|---|
| `sophon_update.py` | 全量升级（就地更新客户端） |
| `sophon_ldiff.py` | 导出官方差分包（只下载，不碰客户端） |
| `apply_ldiff.py` | 应用差分包（**发给别人的就是这个**） |
| `build_exe.py` | 把 `apply_ldiff.py` 打包成单文件 exe |
| `manifest*.proto` / `*_pb2.py` | 官方清单的 proto 定义与生成物（日常不用管） |
| `gen_pb2.py` | 改了 `.proto` 后才需要跑：重新生成 `*_pb2.py` |

## 用法

### 一、全量升级客户端

```bash
python sophon_update.py --gamedir "<客户端根目录>" --cn          # 加 --dry 先预览体积
```

### 二、导出差分包（自己用）

```bash
python sophon_ldiff.py --gamedir "<客户端根目录>" --out "<差分包目录>" --cn
```

`--gamedir` 只用来读 `config.ini` 判断源版本，**不读也不改任何游戏文件**。输出的包是完整的离线升级包（体积随版本变化，分享前建议自己压成 7z）。

### 三、应用差分包（接收方）

把差分包目录 + `SRdiff_apply.exe` 一起发过去：

```
SRdiff_apply.exe                                   # 双击，按提示填两个路径（可拖拽文件夹）
SRdiff_apply.exe --gamedir <客户端> --pack <差分包>   # 或命令行
```

exe 里已含全部依赖（含 HDiffPatch 原生代码），**对方不需要 Python / pip / 任何外部 exe**。自己生成（约 9.3 MB）：

```bash
pip install pyinstaller hdiffpatch
python build_exe.py
```

用 Python 跑也一样：`python apply_ldiff.py --gamedir <客户端> --pack <差分包>`（先加 `--dry` 预览）。

## 常用参数

| 参数 | 适用 | 说明 |
|---|---|---|
| `--cn` | 升级 / 导出 | 只处理 游戏资源(10054) + 中文语音(10055) |
| `--cat 10054` | 升级 / 导出 | 只处理单个类别（优先级高于 `--cn`） |
| `--branch predownload` | 升级 / 导出 | 用官方**预下载**版本（未开服的新版）；默认 `main` = 已上线版本 |
| `--dry` | 全部 | 只统计体积，不下载不写文件 |
| `--verify` | 升级 | 强制逐文件 md5 校验（慢，默认走清单比对） |
| `--src 4.4.0` | 导出 | 强制指定源版本（默认读 `config.ini`） |
| `--no-new` | 导出 | 只打包补丁段，体积小很多，但对方得自己从官方补全新增文件 |
| `--force` | 应用 | 跳过源版本检查 |

**导出时请一次导完所有需要的类别**：它们共用一份 `manifest.json`，分次导出到同一目录会互相覆盖。

## 原理

**chunk（全量）**：`getGameBranches` 拿到目标版本的 `package_id`/`password` → `getBuild` 拿到各类别清单 → 清单里每个文件由若干 zstd 压缩的 chunk 组成，按 `offset` 拼起来就是完整文件。只下载「缺失或内容不符」的文件，所以删掉过往资源也不影响。

**ldiff（差分）**：`getPatchBuild` 给出另一套清单，为**改动过**的文件提供补丁，每条补丁指明它在 CDN 上某个「补丁池文件」里的位置（`patch_offset`/`patch_length`）。官方 CDN 支持 HTTP Range，所以只下补丁段；补丁段是标准 HDiffPatch 格式（`HDIFF13&` + zstd），用 `hdiffpatch` 直接应用。

省流量实测量级（完整 4.5.0 客户端 → 4.6.0，国服 `--cn`）：全量升级需下载 18.55 GiB，走差分只需 **12.10 GiB**（补丁段 3.34 + 新增文件 8.76），省 6.45 GiB。差分省的是「内容改动」那部分；**新增文件省不掉**（官方不为不存在的文件生成补丁）。详见 [NOTES.md](NOTES.md)。

## 限制

- **只能升一档**：官方只对 `diff_tags` 里那一个相邻版本出补丁（4.5.0 只认 4.4.0）。跨版本没有差分包，请用全量升级。
- **源文件必须原样**：补丁按源文件内容生成，被汉化 / MOD 改过的文件打不上（工具会列出，走全量补齐即可，**不会写坏文件**）。
- **新增文件没有补丁**：本地根本没有的文件，官方做不出补丁，只能整份下载 —— 默认导出会把它们一起打进包里。
- **不影响的情况**：删过往资源、删没装的语音包，都完全不影响差分包应用（实测 1390 个过往资源、6.8 GiB，没有一个在补丁清单里）。

## 注意

- 升级/应用完成后会自动把 `config.ini` 的 `game_version` 写成目标版本。
- 覆盖前会替换同名文件，**建议先备份客户端**；进游戏后若还提示补少量资源，交给官方启动器即可。
- 目标版本从官方接口实时读取，**同一个工具可反复用于后续升级**（4.6→4.7、4.7→4.8…），开服自动生效，不用改代码。
- 下载走官方 CDN，速度取决于你的网络；某些地区可能需要代理。

## License

MIT，见 [LICENSE](LICENSE)。`manifest_ldiff.proto` 的注释保留上游出处（DGP-Studio/Snap.Hutao，MIT）；应用端依赖 [hdiffpatch](https://pypi.org/project/hdiffpatch/) 为 Apache-2.0。
