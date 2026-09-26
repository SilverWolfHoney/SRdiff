#!/usr/bin/env python3
# sophon_ldiff.py - 从官方 Sophon(ldiff) 下载差分包, 导出为独立可分享的差分包
#
# 特点: 全程只读官方 CDN, 不读取也不修改你的客户端(只用 config.ini 的版本号判断源版本);
#       导出结果是一个自包含目录, 可直接打包发给别人, 对方用 apply_ldiff.py 合并。
#
# 用法:
#   python sophon_ldiff.py --gamedir "<4.4.0客户端根目录>" --out "<差分包输出目录>" --cn
#   python sophon_ldiff.py --gamedir "<根目录>" --out "<目录>" --cn --dry     # 只看体积不下载
import json, sys, hashlib, time, argparse, urllib.request, pathlib, shutil, os, zstandard, itertools
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0"

B = "https://hyp-api.mihoyo.com/hyp/hyp-connect/api"
LAUNCHER_ID = "jGHBHlcOq1"
GAME_ID = "64kMb5iAWu"        # hkrpg_cn
WORKERS = 12
DL_RETRIES = 6

def http(url, post=None):
    data = json.dumps(post).encode() if post is not None else None
    headers = {"User-Agent": "Dsh-SophonLdiff/1.0", "Accept": "application/json"}
    if post is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)

def dl_range(url, start, length, verbose=False):
    # 只取补丁文件里的一个片段: 官方 CDN 支持 HTTP Range, 不用为整个补丁池付流量
    last = None
    for attempt in range(DL_RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Dsh-SophonLdiff/1.0",
                "Range": f"bytes={start}-{start + length - 1}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                buf = bytearray()
                while len(buf) < length:
                    b = r.read(min(1 << 20, length - len(buf)))
                    if not b:
                        break
                    buf += b
                if len(buf) != length:
                    raise IOError(f"短读: 期望 {length} 实际 {len(buf)}")
                return bytes(buf)
        except Exception as e:
            last = e
            if verbose:
                print(f"    [重试{attempt+1}/{DL_RETRIES}] {e}")
            time.sleep(1 + attempt * 2)
    raise last

def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(pathlib.Path.home())
    return pathlib.Path(base) / "sophon_update_cache"

CACHE_DIR = cache_dir()

def load_manifest_zst(m_id, url_prefix, tag=""):
    # 清单缓存: 重跑不用重复下大清单; 与 chunk 清单分开命名, 避免 id 撞车
    cached = CACHE_DIR / ("ldiff_" + m_id + ".zst")
    if cached.is_file():
        raw = cached.read_bytes()
    else:
        print(f"  下载清单 {m_id} ...")
        with urllib.request.urlopen(urllib.request.Request(
                url_prefix + "/" + m_id, headers={"User-Agent": "Dsh-SophonLdiff/1.0"}), timeout=180) as r:
            raw = r.read()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(raw)
    return zstandard.ZstdDecompressor().decompressobj().decompress(raw)

def get_build(br, api, post=None):
    return http("https://api-takumi.mihoyo.com/downloader/sophon_chunk/api/" + api
                + f"?branch={br['branch']}&package_id={br['package_id']}&password={br['password']}", post)

def load_branches():
    j = http(f"{B}/getGameBranches?game_ids[]={GAME_ID}&launcher_id={LAUNCHER_ID}")
    return j["data"]["game_branches"][0]

def pick_branch(gb, branch):
    key = {"predownload": "pre_download", "pre-download": "pre_download"}.get(branch.lower(), branch)
    if key in gb:
        return gb[key]
    if key == "main" and "pre_download" in gb:
        return gb["pre_download"]
    return None

def ver_key(tag):
    try:
        return tuple(int(x) for x in str(tag).split("."))
    except Exception:
        return (0,)

def local_version(gamedir):
    cfg = gamedir / "config.ini"
    if not cfg.is_file():
        return None
    for line in cfg.read_bytes().decode("utf-8-sig", errors="ignore").splitlines():
        if line.lower().startswith("game_version="):
            return line.split("=", 1)[1].strip()
    return None

def human(n):
    return f"{n/2**30:.2f} GiB" if n >= 2**30 else f"{n/2**20:.1f} MiB"

def collect(cat, src, dry, outdir, workers, limit=0):
    """
    处理一个类别: 解析官方差分清单, 取"本地版本 -> 目标版本"的补丁段,
    按补丁池分组下载并落盘。返回统计信息 dict。
    """
    from manifest_ldiff_pb2 import DiffManifest
    cid = cat["category_id"]
    cname = cat.get("category_name", cid)
    print(f"\n=== [{cid}] {cname} ===")

    dec = load_manifest_zst(cat["manifest"]["id"], cat["manifest_download"]["url_prefix"])
    dm = DiffManifest()
    dm.ParseFromString(dec)
    print(f"  差分清单: {len(dm.files)} 个文件条目, 待删除 {len(dm.files_delete)} 组")

    prefix = cat["diff_download"]["url_prefix"]

    # ---- 按源版本挑出需要打补丁的文件 ----
    # 关键: 一个池的段在池内首尾相接、从 offset 0 开始, 只在"该池的段被完整收集"时成立。
    #       所以 --limit 按【池】计数, 并且同一个池的文件必须一次收完, 否则池会残缺、
    #       导出文件里的偏移就不再等于原始 offset。
    picked, skipped_unchanged, skipped_nosrc = [], 0, 0
    pool_seen, limited = set(), False
    for v in dm.files:
        pat = None
        for p in v.patches:
            if p.key == src:
                pat = p.info
                break
        if pat is None or pat.patch_length <= 0 or (not pat.original_hash and v.size <= 0):
            skipped_unchanged += 1        # 该文件在源版本里没被改动(或为空)
            continue
        new_pool = pat.patch_id not in pool_seen
        if limit and new_pool:
            if len(pool_seen) >= limit:
                limited = True
                break
            pool_seen.add(pat.patch_id)
        if not pat.original_hash:
            skipped_nosrc += 1            # 源文件本就不存在, 直接重建
        picked.append((v, pat))

    # ---- 按补丁池分组, 组内按 offset 排序(顺序追加 => 导出文件里 offset 即段位置) ----
    if limited:
        print(f"  [--limit] 只取了前 {limit} 个补丁池(冒烟测试用, 导出的包不完整)")
    pools = {}
    for v, pat in picked:
        pools.setdefault(pat.patch_id, []).append((pat.patch_offset, pat.patch_length, v, pat))

    dl_bytes = sum(pat.patch_length for _v, pat in picked)
    new_bytes = sum(v.size for v, _pat in picked)
    print(f"  改动文件 {len(picked)} 个 (其中 {skipped_nosrc} 个源文件本就不存在, 可直接重建)")
    print(f"  未改动/不适用 {skipped_unchanged} 个")
    print(f"  补丁池 {len(pools)} 个, 需下载 {human(dl_bytes)} (仅补丁段), 应用后产出 {human(new_bytes)}")

    if dry:
        return {"files": len(picked), "pools": len(pools), "dl": dl_bytes,
                "new": new_bytes, "records": [], "deletes": dm.files_delete, "dry": True}

    # ---- 磁盘预检 ----
    try:
        probe = outdir if outdir.exists() else outdir.parent
        free = shutil.disk_usage(probe.absolute()).free
        if free < dl_bytes:
            print(f"  [警告] 输出盘剩余 {human(free)}, 不足需写入的 {human(dl_bytes)}, 可能中途失败!")
        else:
            print(f"  磁盘预检: 剩余 {human(free)}, 足够写入 {human(dl_bytes)}")
    except Exception:
        pass

    pool_dir = outdir / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    print(f"  开始下载 {len(pools)} 个补丁池 ({human(dl_bytes)}, 仅补丁段, 并发 {workers} 线程) ...")

    # ---- 导出前逐池校验: 段必须按 offset 升序、互不重叠 ----
    # 导出文件按 offset 摆放(空隙补零), 所以必须没有重叠; 有重叠的池无法安全表达, 直接拒绝。
    bad_pools = []
    for pid, segs in pools.items():
        segs.sort(key=lambda x: x[0])
        prev_end = 0
        for off, ln, v, _pat in segs:
            if off < prev_end:
                bad_pools.append((pid, f"段重叠: {v.filename} 于 {off}"))
                break
            prev_end = off + ln
    if bad_pools:
        print(f"\n  [错误] {len(bad_pools)} 个补丁池结构异常, 已排除:")
        for pid, why in bad_pools[:5]:
            print(f"    {pid[:24]}… {why}")
        print("         请反馈此问题(官方打包方式可能已变化)。")
        for pid, _why in bad_pools:
            pools.pop(pid, None)
        if not pools:
            print("  [错误] 没有任何可用的补丁池, 中止。"); sys.exit(1)

    records = []
    counter = itertools.count(1)      # 并发下自增必须原子, 不能用 done[0] += 1
    total_pools = len(pools)
    t0 = time.time()
    gaps = [0]                        # 段间空洞总量(字节), 用于提示导出体积

    def do_pool(item):
        pid, segs = item
        segs.sort(key=lambda x: x[0])
        end = max(off + ln for off, ln, _v, _pat in segs)
        want = sum(ln for _off, ln, _v, _pat in segs)
        gaps[0] += end - want
        dst = pool_dir / pid
        # 断点续传: 只有大小正好等于"按 offset 摆放后的大小"才算下完
        if dst.is_file() and dst.stat().st_size == end:
            n = next(counter)
            print(f"[{time.strftime('%H:%M:%S')}] 已存在 {n}/{total_pools} · {pid[:16]}…")
        else:
            url = prefix + "/" + pid
            tmp = dst.with_name(dst.name + ".part")   # 别用 with_suffix: 池名里有多个点会撞车
            try:
                with open(tmp, "wb") as fh:
                    for off, ln, _v, _pat in segs:     # 按 offset 摆放, 中间的空隙留零
                        fh.seek(off)
                        fh.write(dl_range(url, off, ln))
                if tmp.stat().st_size != end:
                    raise IOError(f"导出池大小 {tmp.stat().st_size} != 期望 {end}")
                tmp.replace(dst)
            except BaseException:
                tmp.unlink(missing_ok=True)           # 失败别留残file, 否则下次误判为已完成
                raise
            n = next(counter)
            print(f"[{time.strftime('%H:%M:%S')}] 已下载 {n}/{total_pools} · {pid[:16]}… ({human(end)})")
        for off, ln, v, pat in segs:
            records.append({
                "filename": v.filename,
                "size": v.size,
                "md5": v.hash.lower(),
                "pool": pid,
                "offset": off,
                "length": ln,
                "original_size": pat.original_size,
                "original_md5": pat.original_hash.lower(),
            })

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do_pool, pools.items()))

    el = time.time() - t0
    print(f"  [{cid}] 完成: {len(picked)} 个文件, {len(pools)} 个补丁池, 耗时 {el:.0f}s")
    if gaps[0]:
        print(f"  [提示] 补丁池内有 {human(gaps[0])} 空隙(补零填充), 导出体积略大于纯补丁段"
              f"(正常完整导出时空隙为 0)")
    return {"files": len(picked), "pools": len(pools), "dl": dl_bytes, "new": new_bytes,
            "records": records, "deletes": dm.files_delete, "dry": False, "gaps": gaps[0]}

def delete_list(files_delete, src):
    # 官方规定了本版本需要删除的旧文件, 收集起来交给应用端处理
    out = []
    for d in files_delete:
        if d.key != src:
            continue
        for info in d.info.list:
            out.append(info.filename)
    return out

def main():
    ap = argparse.ArgumentParser(description="导出官方 Sophon ldiff 差分包(不改客户端)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__} (hkrpg_cn)")
    ap.add_argument("--gamedir", required=True, help="低版本客户端根目录(含 StarRail_Data); 只读它的 config.ini 判断源版本")
    ap.add_argument("--out", required=True, help="差分包输出目录(会创建)")
    ap.add_argument("--branch", default="main", help="main=已上线版本(默认); predownload=预下载版本")
    ap.add_argument("--cat", default=None, help="只导出指定类别(如 10054); 缺省=自动")
    ap.add_argument("--cn", action="store_true", help="只导出 游戏资源(10054)+中文语音(10055)")
    ap.add_argument("--src", default=None, help="强制指定源版本(如 4.4.0); 缺省=读本地 config.ini")
    ap.add_argument("--workers", type=int, default=WORKERS, help=f"并发下载线程数(默认 {WORKERS})")
    ap.add_argument("--dry", action="store_true", help="只统计体积, 不下载不写文件")
    ap.add_argument("--limit", type=int, default=0,
                    help="每类最多取多少个补丁池(0=不限); 用于小规模试跑, 会导出不完整的包")
    a = ap.parse_args()

    gamedir = pathlib.Path(a.gamedir)
    if not gamedir.is_dir():
        print("gamedir 不存在:", gamedir); sys.exit(1)
    outdir = pathlib.Path(a.out)

    print("解析分支 ...")
    gb = load_branches()
    br = pick_branch(gb, a.branch)
    if not br:
        print("找不到分支:", a.branch, " 可用:", ", ".join(gb.keys())); sys.exit(1)
    tgt = br["tag"]
    diff_tags = list(br.get("diff_tags") or [])
    print(f"  目标版本: {tgt}   官方提供差分的源版本: {diff_tags or '(无)'}")

    # ---- 源版本判定: 官方只对 diff_tags 里的版本出补丁 ----
    src = a.src or local_version(gamedir)
    if not src:
        print("  [错误] 读不到本地 config.ini 的 game_version, 请用 --src 指定源版本"); sys.exit(1)
    print(f"  源版本: {src}" + ("" if a.src else "  (读自 config.ini)"))
    if not diff_tags:
        print(f"  [错误] 官方对 {tgt} 没有提供任何差分包, 只能走全量升级(sophon_update.py)"); sys.exit(1)
    if src not in diff_tags:
        print(f"  [错误] 官方只提供 {diff_tags} -> {tgt} 的差分包, 源版本 {src} 不在其中。")
        print(f"         跨版本无法差分, 请用 sophon_update.py 全量升级, 或改用 --src {'/'.join(diff_tags)} 导出那一版差分包。")
        sys.exit(1)

    pb = get_build(br, "getPatchBuild", {})      # POST + body {} 才是正确调用方式
    gbld = get_build(br, "getBuild")
    manifests = pb["data"]["manifests"]
    tgt_build = gbld["data"]
    print(f"  资源类别: {len(manifests)} 个   (build_id={pb['data'].get('build_id')})")

    if a.cat:
        cats = [m for m in manifests if m["category_id"] == a.cat]
    elif a.cn:
        cats = [m for m in manifests if m["category_id"] in ("10054", "10055")]
    else:
        cats = manifests
    if not cats:
        print("找不到类别:", a.cat); sys.exit(1)

    # 目标版本各文件的 md5 索引(用于自检: 导出记录必须与目标清单一致)
    tgt_md5 = {}
    for m in tgt_build["manifests"]:
        from manifest_pb2 import Manifest
        man = Manifest()
        man.ParseFromString(load_manifest_zst(m["manifest"]["id"], m["manifest_download"]["url_prefix"]))
        for f in man.files:
            tgt_md5[f.filename] = f.md5.lower()

    start = time.time()
    stats, all_records, all_deletes = [], [], set()
    for cat in cats:
        st = collect(cat, src, a.dry, outdir, a.workers, a.limit)
        stats.append(st)
        all_records.extend(st["records"])
        all_deletes.update(delete_list(st["deletes"], src))

    dl_total = sum(s["dl"] for s in stats)
    new_total = sum(s["new"] for s in stats)
    el = time.time() - start
    print(f"\n== 汇总 == 类别 {len(cats)} 个, 改动文件 {sum(s['files'] for s in stats)} 个, "
          f"补丁池 {sum(s['pools'] for s in stats)} 个")
    print(f"  需下载(仅补丁段): {human(dl_total)}   应用后产出: {human(new_total)}   耗时 {el:.0f}s")

    if a.dry:
        print("\n(--dry 预览: 未下载、未写文件)")
        return

    # ---- 自检: 导出记录必须与目标版本官方清单逐一对上, 否则包是坏的 ----
    if not all_records:
        print("  [警告] 没有任何改动文件, 差分包是空的 —— 目标版本可能与本地版本相同?")
    else:
        bad = [r for r in all_records if r["md5"] != tgt_md5.get(r["filename"])]
        if bad:
            print(f"  [错误] {len(bad)} 条记录与目标版本 {tgt} 官方清单不符, 例如 {bad[0]['filename']}")
            print("         这是工具或清单解析有问题, 拒绝写出可能损坏的差分包。")
            sys.exit(1)
        print(f"  自检通过: {len(all_records)} 条记录均与目标版本 {tgt} 官方清单一致")

    # ---- 写清单 ----
    manifest = {
        "format": "sophon-ldiff-pack",
        "format_version": 1,
        "game": "hkrpg_cn",
        "src_version": src,
        "target_version": tgt,
        "target_build_id": pb["data"].get("build_id"),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pool_dir": "pool",
        # 池文件内按记录的 offset 摆放补丁段(空隙补零), 所以 offset 始终可直接用于读取
        "patches": sorted(all_records, key=lambda r: r["filename"]),
        "delete": sorted(all_deletes),
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    (outdir / "如何应用.txt").write_text(
        f"""SRdiff 差分包  {src} -> {tgt}
=====================================
内容: 官方 Sophon 差分补丁, 共 {len(all_records)} 个文件, 下载体积 {human(dl_total)}。
      本体在 pool\\ 目录(每个文件是一个"补丁池", 段的位置见 manifest.json)。

要求: 你的客户端必须是 {src} 版本(未改动过)。补丁按原文件内容生成,
      源文件被改过就会打不上(应用脚本会报出来, 那个文件需要单独全量更新)。

应用: 两种方式任选其一。

  A) 推荐: 直接双击 SRdiff_apply.exe (什么都不用安装)
     双击后按提示填入"客户端根目录"和"差分包目录"(可直接把文件夹拖进窗口)。
     也可以命令行:
         SRdiff_apply.exe --gamedir "<你的客户端根目录>" --pack "<本目录>"

  B) 用 Python 脚本:
         pip install hdiffpatch
         python apply_ldiff.py --gamedir "<你的客户端根目录>" --pack "<本目录>"

先预览会改哪些文件(不写盘): 加 --dry 参数。

注意:
  - 本包必须完整。若它是用 --limit 试跑导出的, 只能覆盖其中一部分文件。
  - 所有类别必须一次性导出。分次导出到同一目录会互相覆盖, 包会不完整。
  - 应用脚本会先校验每个源文件的 md5, 只对得上的文件打补丁;
    打完再校验目标 md5, 通过才替换原文件。全程可中断, 重跑自动跳过已完成的部分。
""", encoding="utf-8")

    print(f"\n差分包已导出: {outdir}")
    print(f"  {outdir / 'manifest.json'}   ({len(all_records)} 条补丁记录, {len(all_deletes)} 个待删文件)")
    print(f"  {outdir / 'pool'}            ({len(list((outdir / 'pool').iterdir()))} 个补丁池)")
    print(f"  {outdir / '如何应用.txt'}")
    print(f"  别忘了把 apply_ldiff.py 一起打包发给别人")

if __name__ == "__main__":
    main()
