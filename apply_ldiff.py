#!/usr/bin/env python3
# apply_ldiff.py - 把 SRdiff 导出的官方 ldiff 差分包应用到客户端
#
# 依赖: pip install hdiffpatch      (纯 wheel, 无需 hpatchz/hdiffz 等外部 exe)
#
# 用法:
#   python apply_ldiff.py --gamedir "<客户端根目录>" --pack "<差分包目录>" --dry   # 先预览
#   python apply_ldiff.py --gamedir "<客户端根目录>" --pack "<差分包目录>"         # 正式应用
import json, sys, hashlib, time, argparse, pathlib, shutil
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0"

def md5_file(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

_INTERACTIVE = False

def wait_exit(msg="按回车键关闭 ..."):
    # 只有"双击 exe 后无参数交互运行"结束时才停一下, 否则窗口会立刻消失;
    # 命令行带参数调用不打扰, 便于脚本/批处理使用。
    if not (_INTERACTIVE and getattr(sys, "frozen", False)):
        return
    try:
        input("\n" + msg)
    except Exception:
        pass

def human(n):
    return f"{n/2**30:.2f} GiB" if n >= 2**30 else f"{n/2**20:.1f} MiB"

def safe_rel(root, rel):
    # 防目录穿越: 清单里的路径必须落在客户端目录内
    p = (root / rel).resolve()
    r = root.resolve()
    if not str(p).startswith(str(r)):
        raise ValueError(f"非法路径: {rel}")
    return p

def local_version(gamedir):
    cfg = gamedir / "config.ini"
    if not cfg.is_file():
        return None
    for line in cfg.read_bytes().decode("utf-8-sig", errors="ignore").splitlines():
        if line.lower().startswith("game_version="):
            return line.split("=", 1)[1].strip()
    return None

def set_config_version(gamedir, tag):
    cfg = gamedir / "config.ini"
    if not cfg.is_file():
        print("  (未找到 config.ini, 跳过版本标记)")
        return
    lines = cfg.read_bytes().decode("utf-8", errors="ignore").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.lower().startswith("game_version="):
            lines[i] = f"game_version={tag}\n"
            cfg.write_bytes("".join(lines).encode("utf-8"))
            print(f"  [config] game_version -> {tag}")
            return
    print("  (config.ini 里没有 game_version, 跳过)")

def ask_path(prompt, must_be_file=None):
    """交互式问路径; 支持把文件夹/文件直接拖进控制台窗口(会带引号)"""
    while True:
        try:
            s = input(prompt).strip()
        except EOFError:
            return None
        s = s.strip().strip('"').strip("'").strip()      # 去掉拖拽产生的引号与空格
        if not s:
            print("  (不能为空)")
            continue
        p = pathlib.Path(s)
        if p.is_dir():
            return p
        if must_be_file and (p / must_be_file).is_file():
            return p
        print(f"  路径无效: {p}")
        print(f"  提示: 可以直接把文件夹从资源管理器拖进这个窗口, 会自动填入路径。")


def main():
    global _INTERACTIVE
    ap = argparse.ArgumentParser(description="应用 SRdiff 官方 ldiff 差分包")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--gamedir", default=None, help="客户端根目录(含 StarRail_Data); 缺省则交互式询问")
    ap.add_argument("--pack", default=None, help="差分包目录(含 manifest.json); 缺省则交互式询问")
    ap.add_argument("--dry", action="store_true", help="只检查与预览, 不写任何文件")
    ap.add_argument("--workers", type=int, default=8, help="并发校验线程数(默认 8)")
    ap.add_argument("--force", action="store_true",
                    help="跳过源版本检查(例如你已经手动把 config.ini 改过了)")
    a = ap.parse_args()

    try:
        import hdiffpatch
    except ImportError:
        print("缺少依赖, 请先安装:  pip install hdiffpatch")
        print("(如果你拿到的是打包好的 exe, 说明打包时没带上 hdiffpatch, 请反馈)")
        wait_exit()
        sys.exit(1)

    # ---- 无参数时进入交互模式(双击 exe 即可用) ----
    _INTERACTIVE = a.gamedir is None or a.pack is None
    if _INTERACTIVE:
        print("=" * 62)
        print("  SRdiff 差分包应用工具")
        print("=" * 62)
        print("按提示输入两个路径。可以直接把文件夹从资源管理器拖进这个窗口。\n")
    if a.gamedir is None:
        a.gamedir = ask_path("客户端根目录 (含 StarRail_Data 的那个文件夹): ")
        if a.gamedir is None:
            return
    if a.pack is None:
        a.pack = ask_path("差分包目录 (含 manifest.json 的文件夹): ", must_be_file="manifest.json")
        if a.pack is None:
            return

    gamedir = pathlib.Path(a.gamedir)
    pack = pathlib.Path(a.pack)
    if not gamedir.is_dir():
        print("gamedir 不存在:", gamedir); wait_exit(); sys.exit(1)
    mpath = pack / "manifest.json"
    if not mpath.is_file():
        print("差分包不完整, 找不到:", mpath); wait_exit(); sys.exit(1)

    man = json.loads(mpath.read_text(encoding="utf-8"))
    src, tgt = man.get("src_version"), man.get("target_version")
    patches = man.get("patches") or []
    deletes = man.get("delete") or []
    pool_dir = pack / man.get("pool_dir", "pool")

    print(f"差分包: {src} -> {tgt}   补丁文件 {len(patches)} 个, 待删文件 {len(deletes)} 个")
    print(f"客户端: {gamedir}")

    lv = local_version(gamedir)
    if lv:
        print(f"  本地版本: {lv}")
        if not a.force:
            if lv == tgt:
                print(f"  本地已是目标版本 {tgt}; 仍会按清单核对并补齐缺失/损坏的文件")
            elif lv != src:
                print(f"  [错误] 差分包要求源版本 {src}, 本地是 {lv}。")
                print(f"         请换用 {lv} -> {tgt} 的差分包, 或先全量升级到 {src};")
                print(f"         确实要继续可加 --force (打不上的文件会被列出)。")
                wait_exit()
                sys.exit(1)
    else:
        print("  (读不到本地版本, 跳过源版本检查)")

    # ---- 第一遍: 校验每个文件的当前状态 ----
    print("\n正在校验本地文件 (读盘, 文件多时会慢) ...")
    todo, already, missing, corrupt, from_new = [], [], [], [], []
    new_files = man.get("new_files") or []      # 整份打包的新增文件(本地没有, 无补丁可用)
    newset = {r["filename"] for r in new_files}
    lock = [0]

    def check(rec):
        dst = safe_rel(gamedir, rec["filename"])
        if not dst.is_file():
            # 本地没有这个文件。三种情况:
            #   a) 包内的新增文件已经整份带了内容 -> 由新增文件负责, 不算缺失
            #   b) 补丁自带全部内容(original_md5 为空) -> 直接重建
            #   c) 补丁需要源文件但本地没有     -> 真缺, 只能全量补
            if rec["filename"] in newset:
                from_new.append(rec)
            elif not rec["original_md5"] and rec["original_size"] == 0:
                todo.append(rec)
                lock[0] += 1
            else:
                missing.append(rec)
            return
        size = dst.stat().st_size
        if size == rec["size"] and md5_file(dst) == rec["md5"]:
            already.append(rec); return              # 已经是目标版本
        if not rec["original_md5"]:
            # 包内新增文件随后会整份覆盖它, 属于正常(旧版残留), 不算异常
            if rec["filename"] in newset:
                return
            corrupt.append((rec, f"该文件本应缺失, 但本地存在(大小 {size})")); return
        if size != rec["original_size"]:
            corrupt.append((rec, f"大小 {size} != 源大小 {rec['original_size']}")); return
        if md5_file(dst) != rec["original_md5"]:
            corrupt.append((rec, "md5 与本版本源文件不符(可能被改过)")); return
        todo.append(rec)
        lock[0] += 1
        if lock[0] % 500 == 0:
            print(f"    已校验 {lock[0]} 个待打补丁文件 ...")

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(check, patches))

    print(f"  待打补丁 {len(todo)} 个, 已是新版本 {len(already)} 个, "
          f"由包内新增文件提供 {len(from_new)} 个, 缺失 {len(missing)} 个, 源文件异常 {len(corrupt)} 个")
    if from_new:
        print(f"  [提示] {len(from_new)} 个文件本地没有、官方补丁也要求源文件, "
              f"但包内的新增文件已整份带了内容, 随后会直接复制到位")
    if missing:
        print(f"  [提示] {len(missing)} 个文件本包覆盖不到, 需要走全量更新(见文末清单)")
    if corrupt:
        print(f"  [提示] {len(corrupt)} 个文件源内容不符, 打不了补丁, 需要全量更新")

    if new_files:
        print(f"  另有整份打包的新增文件 {len(new_files)} 个 (本地没有, 无补丁可用, 直接复制)")

    if a.dry:
        for rec in todo[:20]:
            print(f"    将更新: {rec['filename']}")
        if len(todo) > 20:
            print(f"    ... 另有 {len(todo)-20} 个")
        for rec in new_files[:20]:
            print(f"    将新增: {rec['filename']}")
        if len(new_files) > 20:
            print(f"    ... 另有 {len(new_files)-20} 个新增文件")
        print(f"\n(--dry 预览: 未写任何文件)")
        wait_exit()
        return

    # ---- 新增文件: 整份复制到客户端 ----
    n_copied = n_new_skip = 0
    new_bad = []
    if new_files:
        fdir = pack / man.get("files_dir", "files")
        print(f"\n开始复制新增文件 (共 {len(new_files)} 个) ...")
        for i, rec in enumerate(new_files, 1):
            srcf = safe_rel(fdir, rec["filename"])
            dst = safe_rel(gamedir, rec["filename"])
            try:
                if not srcf.is_file():
                    new_bad.append((rec["filename"], "包内缺少该文件")); continue
                if srcf.stat().st_size != rec["size"] or md5_file(srcf) != rec["md5"]:
                    new_bad.append((rec["filename"], "包内文件校验不通过")); continue
                if dst.is_file() and dst.stat().st_size == rec["size"] and md5_file(dst) == rec["md5"]:
                    n_new_skip += 1; continue          # 已经装过了(重跑)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(srcf, dst)
                n_copied += 1
            except Exception as e:
                new_bad.append((rec["filename"], f"{type(e).__name__}: {e}"))
            if i % 50 == 0 or i == len(new_files):
                print(f"[{time.strftime('%H:%M:%S')}] 新增 {i} / {len(new_files)} · {rec['filename']}")
        print(f"新增文件完成: 复制 {n_copied}, 已存在跳过 {n_new_skip}, 失败 {len(new_bad)}")
    if missing:
        print(f"\n[提示] 有 {len(missing)} 个文件本包覆盖不到(本地没有、官方也不为它们出补丁)。")
        print(f"       补救: 用全量升级工具从官方补齐这些文件即可(差分包已经把其余文件省掉了)。")

    # ---- 第二遍: 逐个应用补丁 ----
    print(f"\n开始应用补丁 (共 {len(todo)} 个文件) ...")
    t0 = time.time()
    ok = fail = 0
    failed = []
    for i, rec in enumerate(todo, 1):
        dst = safe_rel(gamedir, rec["filename"])
        try:
            old = dst.read_bytes() if rec["original_md5"] else b""
            seg = pool_dir.joinpath(rec["pool"]).read_bytes()
            new = hdiffpatch.apply(old, seg[rec["offset"]: rec["offset"] + rec["length"]])
        except Exception as e:
            failed.append((rec["filename"], f"{type(e).__name__}: {e}")); fail += 1
            print(f"  [失败] {rec['filename']}: {e}")
            continue
        if len(new) != rec["size"] or hashlib.md5(new).hexdigest() != rec["md5"]:
            failed.append((rec["filename"], "应用后 md5/大小不符")); fail += 1
            print(f"  [失败] {rec['filename']}: 应用后校验不通过")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(new)
        ok += 1
        if i % 20 == 0 or i == len(todo):
            print(f"[{time.strftime('%H:%M:%S')}] 已应用 {i} / {len(todo)} (失败 {fail}) · {rec['filename']}")

    print(f"\n补丁应用完成: 成功 {ok}, 失败 {fail}, 耗时 {time.time()-t0:.0f}s")

    # ---- 删除官方要求移除的旧文件 ----
    if deletes:
        n = 0
        for rel in deletes:
            p = safe_rel(gamedir, rel)
            if p.is_file():
                p.unlink(); n += 1
        print(f"已删除官方标记的旧文件: {n} / {len(deletes)}")

    if fail == 0 and not missing and not corrupt and not new_bad:
        set_config_version(gamedir, tgt)
        print(f"\n完成: 客户端已更新到 {tgt}")
    else:
        print(f"\n部分文件未能更新 (补丁失败 {fail}, 缺失 {len(missing)}, 源异常 {len(corrupt)}, "
              f"新增失败 {len(new_bad)})。")
        print(f"config.ini 未改动。这些文件请用全量更新补齐:")
        print(f"   Python:      python sophon_update.py --gamedir \"<客户端根目录>\"")
        print(f"   (只补这些文件, 其余文件差分包已经处理好了; 补完再跑一次本程序即可)")
        print(f"未处理清单:")
        for rec in missing[:50]:
            print(f"  缺失 {rec['filename']}")
        for rec, why in corrupt[:50]:
            print(f"  异常 {rec['filename']}  ({why})")
        for fn, why in failed[:50]:
            print(f"  失败 {fn}  ({why})")
        for fn, why in new_bad[:50]:
            print(f"  新增失败 {fn}  ({why})")
    wait_exit()

if __name__ == "__main__":
    main()
