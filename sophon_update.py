#!/usr/bin/env python3
# sophon_update.py - Sophon 式 星穹铁道(hkrpg_cn) 低→高 完整更新器(Python)
# 自动: 连官方 getBuild -> 检测目标版本 -> 比对本地 -> 下载缺失/变化文件 -> 组装写回
# 用法(全量升级, 推荐): python sophon_update.py --gamedir "<低版本客户端根目录>"
#    默认自动遍历所有资源类别(游戏资源+各语音); 也可用 --cat 只跑单类; --dry 只预览
import json, sys, hashlib, time, argparse, urllib.request, pathlib, zstandard
from concurrent.futures import ThreadPoolExecutor

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

CACHE_DIR = pathlib.Path(__file__).parent / "sophon_cache"

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

def get_branch(branch):
    j = http(f"{B}/getGameBranches?game_ids[]={GAME_ID}&launcher_id={LAUNCHER_ID}")
    gb = j["data"]["game_branches"][0]
    return gb["main"] if branch in gb else None

def process_category(cat, gamedir, dry):
    cid = cat["category_id"]
    cname = cat.get("category_name", cid)
    chunk_prefix = cat["chunk_download"]["url_prefix"]
    print(f"\n=== [{cid}] {cname} ===")
    man = load_manifest(cat["manifest"], cat["manifest_download"]["url_prefix"])
    total_files = len(man.files)
    print(f"  清单文件数: {total_files}  (并发下载: {WORKERS} 线程)")
    # ---- 第一遍: 统计需要组装的(缺失或内容不符), 拿到"总共需组装" ----
    print("  正在统计需要更新的文件 ...")
    need = []
    n_skip = 0
    for fi in man.files:
        dst = gamedir / fi.filename
        if dst.is_file() and dst.stat().st_size == fi.size:
            if dry:                     # dry 只按大小估算(快)
                n_skip += 1; continue
            try:
                if hashlib.md5(dst.read_bytes()).hexdigest().lower() == fi.md5.lower():
                    n_skip += 1; continue
            except Exception:
                pass
        if fi.chunks:
            need.append(fi)
    total_to_assemble = len(need)
    print(f"  总共需要组装: {total_to_assemble}  (已正确跳过 {n_skip})")
    if dry:
        print(f"  [dry] 将组装 {total_to_assemble} 个 (未下载未写文件)")
        return total_to_assemble
    # ---- 第二遍: 逐文件组装下载 ----
    t0 = time.time()
    n_new = 0
    for fi in need:
        rel = fi.filename
        dst = gamedir / rel
        buf = bytearray(fi.size)
        # 并发下载+解压该文件的所有 chunks
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
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
    return n_new

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
    ap.add_argument("--branch", default="main")
    ap.add_argument("--gamedir", required=True, help="低版本客户端根目录(要升级的目标, 含 StarRail_Data)")
    ap.add_argument("--cat", default=None, help="只跑指定类别(如 10054); 缺省=自动遍历全部类别")
    ap.add_argument("--cn", action="store_true", help="中文版: 只升 游戏资源(10054)+中文语音(10055), 不装英/日/韩")
    ap.add_argument("--dry", action="store_true", help="只预览, 不下载不写文件")
    a = ap.parse_args()
    gamedir = pathlib.Path(a.gamedir)
    if not gamedir.is_dir():
        print("gamedir 不存在:", gamedir); sys.exit(1)

    print("解析分支 ...")
    br = get_branch(a.branch)
    if not br:
        print("找不到分支:", a.branch); sys.exit(1)
    print(f"  目标版本: {br['tag']}   源(diff_tags): {br['diff_tags']}")

    bj = http("https://api-takumi.mihoyo.com/downloader/sophon_chunk/api/getBuild"
              + f"?branch={br['branch']}&package_id={br['package_id']}&password={br['password']}")
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

    total=0
    start_all = time.time()
    for cat in cats:
        total += process_category(cat, gamedir, a.dry)
    el_all = time.time() - start_all
    print(f"\n== 全部完成 == 共处理类别 {len(cats)} 个, 新增/重组文件合计: {total} 个, 目标版本 {br['tag']}, 总耗时 {el_all:.0f}s ({el_all/60:.1f} 分钟)")
    if a.dry:
        print("(--dry 预览, 未下载未写文件)")
    else:
        set_config_version(gamedir, br["tag"])
        ask_clean_cache()

if __name__ == "__main__":
    main()
