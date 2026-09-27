#!/usr/bin/env python3
# sophon_update.py - Sophon 式 星穹铁道(hkrpg_cn) 低→高 完整更新器(Python)
# 自动: 连官方 getBuild -> 检测目标版本 -> 比对本地 -> 下载缺失/变化文件 -> 组装写回
# 用法(全量升级, 推荐): python sophon_update.py --gamedir "<低版本客户端根目录>"
#    默认自动遍历所有资源类别(游戏资源+各语音); 也可用 --cat 只跑单类; --dry 只预览
import json, sys, hashlib, time, argparse, urllib.request, pathlib, shutil, os, zstandard
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.1"

B = "https://hyp-api.mihoyo.com/hyp/hyp-connect/api"
LAUNCHER_ID = "jGHBHlcOq1"
GAME_ID = "64kMb5iAWu"   # hkrpg_cn
WORKERS = 12
DL_RETRIES = 6

def http(url, post=None):
    data = json.dumps(post).encode() if post is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "Dsh-Sophon/1.0", "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)

def dl(url, verbose=False):
    # 带重试 + 短连接超时(快速失败重试), 显示进度(下载中的字节)
    last = None
    for attempt in range(DL_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Dsh-Sophon/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:   # 连接/读 20s
                chunks = []; got = 0
                total = int(r.headers.get("Content-Length", 0) or 0)
                if verbose and total:
                    prefix = f"  下载 {total/1e6:.1f}MB:"
                    sys.stdout.write(prefix); sys.stdout.flush()
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    chunks.append(b); got += len(b)
                    if verbose and total and (got % (4 << 20) == 0 or got == total):
                        sys.stdout.write(f" {got/1e6:.0f}/{total/1e6:.0f}MB")
                        sys.stdout.flush()
                if verbose:
                    sys.stdout.write("\n"); sys.stdout.flush()
                return b"".join(chunks)
        except Exception as e:
            last = e
            if verbose:
                print(f"  [重试{attempt+1}/{DL_RETRIES}] {e}")
            time.sleep(1 + attempt * 2)
    raise last

def cache_dir():
    # manifest 缓存放用户缓存区: 不随工具目录被删/被覆盖, 避免重复下大清单
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(pathlib.Path.home())
    return pathlib.Path(base) / "sophon_update_cache"

CACHE_DIR = cache_dir()

def load_manifest(manifest, dl_prefix):
    m_id = manifest["id"]
    # 本地缓存: 下过一次就存, 重跑秒读, 避免重复下载大 manifest
    cached = CACHE_DIR / (m_id + ".zst")
    if cached.is_file():
        raw = cached.read_bytes()
    else:
        raw = dl(dl_prefix + "/" + m_id, verbose=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(raw)
    dec = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    from manifest_pb2 import Manifest
    m = Manifest(); m.ParseFromString(dec)
    return m

# 下载+解压单个 chunk, 返回 (offset, data). 供并发线程调用.
# 用流式 decompressobj()(兼容帧头无内容大小的 zstd), 每线程独立实例.
def fetch_chunk(chunk, chunk_prefix):
    raw = dl(chunk_prefix + "/" + chunk.chunk_id)
    dec = zstandard.ZstdDecompressor().decompressobj()
    data = dec.decompress(raw) + dec.flush()
    return chunk.offset, data

def load_branches():
    # 官方同时给出 main(已上线) 与 pre_download(预下载, 未开服) 两套目标
    global _BRANCHES
    j = http(f"{B}/getGameBranches?game_ids[]={GAME_ID}&launcher_id={LAUNCHER_ID}")
    _BRANCHES = j["data"]["game_branches"][0]
    return _BRANCHES

def branch_by_tag(gb, tag):
    # diff_tags 给的是"版本号"(如 4.5.0)而不是分支名, 要按 tag 反查分支
    for v in gb.values():
        if isinstance(v, dict) and v.get("tag") == tag:
            return v
    return None

def pick_branch(gb, branch):
    # 取指定分支; 键名是 main / pre_download, 也接受 predownload 写法; 请求 main 缺失时退回 pre_download
    key = {"predownload": "pre_download", "pre-download": "pre_download"}.get(branch.lower(), branch)
    if key in gb:
        return gb[key]
    if key == "main" and "pre_download" in gb:
        return gb["pre_download"]
    return None

def ver_key(tag):
    # 把 "4.6.0" 变成 (4,6,0), 用来比大小(不用字符串比, 否则 "4.10" < "4.9")
    try:
        return tuple(int(x) for x in str(tag).split("."))
    except Exception:
        return (0,)

def local_version(gamedir):
    # 读本地客户端版本(config.ini 的 game_version), 用作"本地清单"的比对基准
    cfg = gamedir / "config.ini"
    if not cfg.is_file():
        return None
    try:
        for line in cfg.read_bytes().decode("utf-8-sig", errors="ignore").splitlines():
            if line.lower().startswith("game_version="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None

def src_manifest_index(br, cat):
    # 拿"本地版本"对应的官方清单, 建 文件名->md5 索引.
    # 有了它, 判断某个文件要不要更新只需比对两份清单的 md5, 不必读本地 100GB.
    tags = [t for t in (br.get("diff_tags") or []) if branch_by_tag(_BRANCHES, t)]
    if not tags:
        return {}
    try:
        src = branch_by_tag(_BRANCHES, tags[0])
        for m in get_build(src)["data"]["manifests"]:
            if m["category_id"] != cat["category_id"]:
                continue
            man = load_manifest(m["manifest"], m["manifest_download"]["url_prefix"])
            return {f.filename: f.md5.lower() for f in man.files}
    except Exception as e:
        print(f"  (本地清单 {tags[0]} 获取失败, 退回逐文件校验: {e})")
    return {}

def get_build(br):
    return http("https://api-takumi.mihoyo.com/downloader/sophon_chunk/api/getBuild"
                + f"?branch={br['branch']}&package_id={br['package_id']}&password={br['password']}")

def process_category(cat, gamedir, dry, br=None, src_tag=None, index_cache=None, verify=False):
    cid = cat["category_id"]
    cname = cat.get("category_name", cid)
    chunk_prefix = cat["chunk_download"]["url_prefix"]
    print(f"\n=== [{cid}] {cname} ===")
    man = load_manifest(cat["manifest"], cat["manifest_download"]["url_prefix"])
    total_files = len(man.files)
    print(f"  清单文件数: {total_files}  (并发下载: {WORKERS} 线程)")
    # ---- 第一遍: 统计需要组装的(缺失或内容不符), 拿到"总共需组装" ----
    idx = index_cache.get(cid) if index_cache is not None else None
    if idx is None and src_tag and index_cache is not None:
        idx = src_manifest_index(br, cat)      # 每类单独取本地版本清单, 取完缓存
        index_cache[cid] = idx
    use_index = bool(idx)
    if use_index:
        print(f"  正在比对清单 (本地 {src_tag} 的官方清单 vs 目标) ...")
    else:
        print("  正在统计需要更新的文件 (逐文件校验, 会读整个客户端) ...")
    need = []
    n_skip = 0
    n_indexed = 0
    dl_bytes = 0
    for i, fi in enumerate(man.files, 1):
        dst = gamedir / fi.filename
        if not dst.is_file():
            pass                                  # 缺文件一律重下, 无论清单怎么说
        elif use_index:
            old = idx.get(fi.filename)
            if old == fi.md5.lower():
                n_skip += 1; n_indexed += 1; continue    # 与本地版本清单一致 => 本就是新版, 不读盘
        elif dst.stat().st_size == fi.size:
            # --dry 默认只按大小估算(不读盘); 但加了 --verify 就必须真算 md5,
            # 否则"大小恰好相同、内容却是旧版"的文件会被误判为已是最新。
            if dry and not verify:
                n_skip += 1; continue
            try:
                if hashlib.md5(dst.read_bytes()).hexdigest().lower() == fi.md5.lower():
                    n_skip += 1; continue
            except Exception:
                pass
        if fi.chunks:
            need.append(fi)
            dl_bytes += sum(c.compressed_size for c in fi.chunks)   # 实际要下载的压缩体积
        if not use_index and (not dry or verify) and i % 2000 == 0:  # 校验本地要读完整包, 给个进度免得像卡住
            print(f"    统计中 {i}/{total_files} ... 需更新 {len(need)}")
    total_to_assemble = len(need)
    write_bytes = sum(f.size for f in need)                          # 解压后写入体积
    if use_index:
        print(f"  清单比对: {n_indexed} 个文件与本地版本一致 (未读盘)")
    print(f"  总共需要组装: {total_to_assemble}  (已正确跳过 {n_skip})")
    print(f"  需下载 {dl_bytes/2**30:.2f} GiB (压缩)  →  写入磁盘 {write_bytes/2**30:.2f} GiB")
    if dry:
        print(f"  [dry] 将组装 {total_to_assemble} 个 (未下载未写文件)")
        return total_to_assemble, dl_bytes
    # ---- 磁盘空间预检: 装不下就别白下 ----
    try:
        free = shutil.disk_usage(gamedir.absolute()).free
        if free < write_bytes:
            print(f"  [警告] 目标盘剩余 {free/2**30:.2f} GiB, 不足需写入的 {write_bytes/2**30:.2f} GiB, 可能中途写失败!")
        else:
            print(f"  磁盘预检: 剩余 {free/2**30:.2f} GiB, 足够写入 {write_bytes/2**30:.2f} GiB")
    except Exception:
        pass
    # ---- 第二遍: 逐文件组装下载 ----
    # 线程池整个类别只建一次: 每文件新建池在"小文件多"时要反复起停线程, 开销明显
    t0 = time.time()
    n_new = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fi in need:
            rel = fi.filename
            dst = gamedir / rel
            buf = bytearray(fi.size)
            # 并发下载+解压该文件的所有 chunks(把结果全部取出后再写, 保证异常不外泄)
            results = list(ex.map(lambda c: fetch_chunk(c, chunk_prefix), fi.chunks))
            for offset, data in results:
                buf[offset:offset+len(data)] = data
            if hashlib.md5(buf).hexdigest().lower() != fi.md5.lower():
                print(f"  [WARN] md5 校验失败: {rel}"); continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as fh: fh.write(buf)
            n_new += 1
            print(f"[{time.strftime('%H:%M:%S')}] 已组装 {n_new} / {total_to_assemble}  · {rel}")
    el = time.time() - t0
    print(f"  [{cid}] 完成: 已组装 {n_new}/{total_to_assemble}  已正确跳过 {n_skip}  耗时 {el:.0f}s")
    return n_new, dl_bytes

def set_config_version(gamedir, tag):
    # 更新 config.ini 的 game_version 为目标版本, 让启动器显示正确版本
    cfg = gamedir / "config.ini"
    if not cfg.is_file():
        print("  (未找到 config.ini, 跳过版本标记)")
        return
    lines = cfg.read_bytes().decode("utf-8", errors="ignore").splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if line.lower().startswith("game_version="):
            lines[i] = f"game_version={tag}\n"
            found = True
            break
    if found:
        cfg.write_bytes("".join(lines).encode("utf-8"))
        print(f"  [config] game_version -> {tag}")

def ask_clean_cache():
    # 交互: 问用户是否清理缓存目录; 用户说删就删(默认不删)
    if not CACHE_DIR.is_dir():
        return
    files = [f for f in CACHE_DIR.iterdir() if f.is_file()]
    if not files:
        return
    try:
        ans = input(f"\n检测到 sophon_cache 缓存 {len(files)} 个文件, 是否删除清理? [y/N]: ").strip().lower()
    except EOFError:
        return
    if ans in ("y", "yes"):
        for f in files:
            try: f.unlink()
            except Exception: pass
        print("  缓存已清理。")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__} (hkrpg_cn)")
    ap.add_argument("--branch", default="main",
                    help="main=已上线正式版(开服前停在上一版本); predownload=预下载(未开服的新版本, 装好等开服)")
    ap.add_argument("--gamedir", required=True, help="低版本客户端根目录(要升级的目标, 含 StarRail_Data)")
    ap.add_argument("--cat", default=None, help="只跑指定类别(如 10054); 缺省=自动遍历全部类别")
    ap.add_argument("--cn", action="store_true", help="中文版: 只升 游戏资源(10054)+中文语音(10055), 不装英/日/韩")
    ap.add_argument("--dry", action="store_true", help="只预览, 不下载不写文件")
    ap.add_argument("--verify", action="store_true",
                    help="强制逐文件 md5 校验(慢, 会读整个客户端); 默认走清单比对, 秒出结果")
    a = ap.parse_args()
    gamedir = pathlib.Path(a.gamedir)
    if not gamedir.is_dir():
        print("gamedir 不存在:", gamedir); sys.exit(1)

    print("解析分支 ...")
    gb = load_branches()
    br = pick_branch(gb, a.branch)
    if not br:
        print("找不到分支:", a.branch, " 可用:", ", ".join(gb.keys())); sys.exit(1)
    other = gb.get("pre_download") if br.get("branch") != "predownload" else gb.get("main")
    if a.branch == "main" and other and other.get("tag") != br.get("tag") and ver_key(other["tag"]) > ver_key(br["tag"]):
        # 只走默认分支时才提醒: 官方预下载里有更高版本, 想提前囤可以切过去
        print(f"  (提示: 官方另有 pre_download 分支 tag={other['tag']} 更高; 想提前下载可加 --branch predownload)")
    print(f"  目标版本: {br['tag']}   源(diff_tags): {br['diff_tags']}")

    bj = get_build(br)
    manifests = bj["data"]["manifests"]
    print(f"  资源类别: {len(manifests)} 个")

    if a.cat:
        cats = [m for m in manifests if m["category_id"] == a.cat]
    elif a.cn:
        cats = [m for m in manifests if m["category_id"] in ("10054", "10055")]
    else:
        cats = manifests
    if not cats:
        print("找不到类别:", a.cat); sys.exit(1)
    if a.cn:
        print("  中文版模式: 仅 游戏资源(10054) + 中文语音(10055)")

    # ---- 清单比对基准: 本地版本 == 官方某分支 tag 时, 用清单 md5 判断, 不读本地文件 ----
    src_tag = None
    lv = local_version(gamedir)
    # 防降级: 本地版本比目标版本还新时, 继续用这个目标会把客户端升回旧版
    if lv and ver_key(lv) > ver_key(br["tag"]):
        print(f"  [警告] 本地版本 {lv} 比目标版本 {br['tag']} 还新!")
        print(f"         继续会把你降级到 {br['tag']}。要升到更新的版本, 请加 --branch predownload")
        if br["tag"] != "pre_download":
            other = gb.get("pre_download")
            if other and ver_key(other.get("tag", "0")) > ver_key(br["tag"]):
                print(f"         (官方 pre_download 分支当前是 {other['tag']})")
    if a.verify:
        print("  --verify: 强制逐文件 md5 校验 (会读整个客户端, 较慢)")
    elif lv:
        if ver_key(lv) == ver_key(br["tag"]):
            print(f"  本地已是目标版本 {lv}: 按清单核对缺失文件(不校验内容, 要逐文件校验内容请加 --verify)")
        elif branch_by_tag(gb, lv):
            src_tag = lv
            print(f"  已读本地版本 {lv}: 用官方 {lv} 清单作比对基准, 不读本地包")
        else:
            print(f"  本地版本 {lv} 不在官方分支列表里, 退回逐文件校验")

    total=0
    dl_total=0
    index_cache = {} if src_tag else None
    start_all = time.time()
    for cat in cats:
        n, b = process_category(cat, gamedir, a.dry, br, src_tag, index_cache, a.verify)
        total += n; dl_total += b
    el_all = time.time() - start_all
    print(f"\n== 全部完成 == 共处理类别 {len(cats)} 个, 新增/重组文件合计: {total} 个, 需下载 {dl_total/2**30:.2f} GiB, 目标版本 {br['tag']}, 总耗时 {el_all:.0f}s ({el_all/60:.1f} 分钟)")
    if a.dry:
        print("(--dry 预览, 未下载未写文件)")
    else:
        set_config_version(gamedir, br["tag"])
        ask_clean_cache()

if __name__ == "__main__":
    main()
