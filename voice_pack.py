#!/usr/bin/env python3
# voice_pack.py - 给客户端补充官方语音包(从官方 CDN 拉取, 装到任意客户端目录)
#
# 用途: 测试服/精简客户端常常不带语音包, 或者只有部分语言。
#       本工具直接从官方 CDN 拉当前版本的语音包写进去。
#
# 直接运行(不带参数)= 交互模式:
#     python voice_pack.py
# 也可以命令行:
#     python voice_pack.py --gamedir "<客户端根目录>" --lang cn,en --branch predownload --dry
import argparse, pathlib, sys, time

import sophon_update as S

__version__ = "1.2"

# 交互模式 + 打包成 exe 时, 结束时停一下, 免得双击运行看不到结果窗口就关了
_INTERACTIVE = False

def wait_exit(msg="按回车键关闭 ...", force=False):
    """打包成 exe 时, 出错或结束都停一下让用户看清输出; 命令行带参数调用不打扰。"""
    if not getattr(sys, "frozen", False):
        return
    if not (force or _INTERACTIVE):
        return
    try:
        input("\n" + msg)
    except Exception:
        pass

# 语音类别: id -> (语言代码, 显示名, 游戏内目录名)
LANGS = [
    ("10055", "cn", "中文", "Chinese(PRC)"),
    ("10056", "en", "英语", "English"),
    ("10057", "jp", "日语", "Japanese"),
    ("10058", "kr", "韩语", "Korean"),
]
BY_CODE = {c: (cid, name, sub) for cid, c, name, sub in LANGS}


def ask_menu(title, options, default=1):
    print(f"\n{title}")
    for i, (label, _v) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        try:
            raw = input(f"请选择 [1-{len(options)}] (默认 {default}): ").strip()
        except EOFError:
            return options[default - 1][1]
        if not raw:
            return options[default - 1][1]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][1]
        print(f"  请输入 1-{len(options)} 之间的数字")


def ask_path(prompt):
    while True:
        try:
            s = input(prompt).strip()
        except EOFError:
            return None
        s = s.strip().strip('"').strip("'").strip()
        if not s:
            print("  (不能为空)")
            continue
        p = pathlib.Path(s)
        if p.is_dir():
            return p
        print(f"  目录不存在: {p}")
        print(f"  提示: 可以把客户端文件夹从资源管理器拖进这个窗口。")


def voice_dir_status(gamedir, langs):
    """看看每个语言现在装了多少(只看文件数, 不读内容)。四种语言并排一行, 不占四行。"""
    parts = []
    for cid, code, name, sub in LANGS:
        d = gamedir / "StarRail_Data" / "Persistent" / "Audio" / "AudioPackage" / "Windows" / sub
        n = sum(1 for _ in d.rglob("*") if _.is_file()) if d.is_dir() else 0
        num = S.green(str(n)) if n >= 300 else (S.yellow(str(n)) if n else S.dim("0"))
        parts.append(f"{name} {num}")
    print(f"  {S.dim('语音包')}  " + "   ".join(parts))


def setup_official(cid_list, branch):
    """让 sophon_update 的分支表只暴露我们要的那个官方版本, 于是目标版本由我们决定"""
    gb = S.load_branches()
    key = {"main": "main", "pre_download": "pre_download"}.get(branch, branch)
    br = gb.get(key)
    if not br or not br.get("package_id"):
        print(f"  官方没有 {branch} 分支"); return None
    S._BRANCHES = {key: br}
    return br


def main():
    global _INTERACTIVE
    ap = argparse.ArgumentParser(description="给客户端补充官方语音包(交互运行更省事)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__} (hkrpg_cn)")
    ap.add_argument("--gamedir", default=None, help="要补充语音包的客户端根目录(含 StarRail_Data)")
    ap.add_argument("--lang", default=None, help="语言代码, 逗号分隔: cn,en,jp,kr; 缺省则交互选择")
    ap.add_argument("--branch", default=None, help="main=已上线版本; predownload=预下载版本; 缺省交互选择")
    ap.add_argument("--dry", action="store_true", help="只统计, 不下载不写文件")
    ap.add_argument("--verify", action="store_true",
                    help="强制逐文件 md5 校验(慢); 默认也用逐文件校验, 因为要精确找出缺失的语音文件")
    ap.add_argument("--force", action="store_true",
                    help="跳过客户端根目录结构校验(确认目标目录特殊时才用)")
    a = ap.parse_args()

    interactive = a.gamedir is None or a.lang is None or a.branch is None
    _INTERACTIVE = interactive
    if interactive:
        print(f"\n{S.bold(S.cyan('SRdiff'))} {S.dim('·')} 语音包补充 {S.dim('·')} 星穹铁道(国服)")
        print(S.dim("-" * 52))

    # ---- 客户端目录: 先确认它真是客户端根目录, 再问别的(免得白答一堆问题) ----
    if a.gamedir is None:
        gamedir = S.ask_client_root("\n要补充语音包的客户端根目录 (含 StarRail_Data): ", a.force)
        if gamedir is None:
            print("已取消。"); wait_exit(); return
    else:
        gamedir = pathlib.Path(a.gamedir)
        if not gamedir.is_dir():
            print("目录不存在:", gamedir); wait_exit(); sys.exit(1)
        # 命令行给的是明确指令, 不静默改写; 但这条检查必须在问语言/联网之前
        if not a.force and not S.is_client_root(gamedir):
            cands = S.find_client_root(gamedir)
            print(f"\n[错误] {S.client_root_problem(gamedir, cands)}")
            if cands:
                print(f'       请改用: --gamedir "{cands[0]}"')
            wait_exit(force=True); sys.exit(2)

    lv = S.local_version(gamedir)
    print(f"\n  {S.dim('客户端')}  {gamedir}")
    print(f"  {S.dim('版本  ')}  {lv if lv else S.dim('(读不到 config.ini)')}")
    voice_dir_status(gamedir, LANGS)

    # ---- 要哪种语言 ----
    if a.lang:
        codes = [c.strip().lower() for c in a.lang.split(",") if c.strip()]
    else:
        picked = ask_menu("要补充哪些语言的语音包?", [
            ("中文 (Chinese(PRC))", ["cn"]),
            ("英语 (English)", ["en"]),
            ("日语 (Japanese)", ["jp"]),
            ("韩语 (Korean)", ["kr"]),
            ("全部四种语言", ["cn", "en", "jp", "kr"]),
        ])
        codes = picked
    bad = [c for c in codes if c not in BY_CODE]
    if bad:
        print(f"  不认识的语言代码: {bad}  (可用: cn,en,jp,kr)"); wait_exit(); sys.exit(1)

    # ---- 取哪个版本的语音包 ----
    if a.branch:
        branch = {"predownload": "pre_download"}.get(a.branch.lower(), a.branch.lower())
    else:
        branch = ask_menu("要从哪个版本的语音包拉取?", [
            ("官方已上线版本 (和正式服一致的语音)", "main"),
            ("官方预下载版本 (还没开服的新版本)", "pre_download"),
        ])

    print(f"\n  {S.dim('查询官方版本 ...')}")
    try:
        if not setup_official([BY_CODE[c][0] for c in codes], branch):
            wait_exit(); sys.exit(1)
    except Exception as e:
        print(f"  查询失败: {e}"); wait_exit(); sys.exit(1)

    # ---- 先预览还是直接下载 ----
    if a.dry:
        dry = True
    elif interactive:
        dry = ask_menu("要先看看要下载多少吗?", [
            ("先预览, 不下载任何东西", True),
            ("直接开始下载", False),
        ], default=1)
    else:
        dry = False

    cat_ids = [BY_CODE[c][0] for c in codes]
    print(f"\n  {S.dim('处理')} {', '.join(BY_CODE[c][1] for c in codes)}")
    t0 = time.time()

    # 强制逐文件校验(verify=True): 语音包只有几百个文件, 读一遍很快;
    # 靠版本号做清单比对在这里不可靠 —— 测试服/精简版的版本号常与正式服不同,
    # 万一"恰好相同"就会误判成"语音包已存在"而不去补。
    def run_update(is_dry, do_verify):
        try:
            S._do_update(gamedir, branch, cat_ids, is_dry, do_verify, None, a.force)
        except S.ClientRootError as e:
            # 目标不是客户端根目录时立刻收手: 否则会白下十几 GB 并写进游戏读不到的位置
            print(f"\n[中止] {e}")
            wait_exit(force=True)
            sys.exit(2)

    run_update(dry, True)

    if dry:
        try:
            ans = input("\n要现在正式下载吗? [y/N]: ").strip().lower()
        except EOFError:
            ans = "n"
        if ans in ("y", "yes"):
            print()
            run_update(False, False)
        else:
            print("已取消。")
    print(f"\n  {S.dim('语音包目录')} {gamedir / 'StarRail_Data' / 'Persistent' / 'Audio' / 'AudioPackage' / 'Windows'}")
    wait_exit()


if __name__ == "__main__":
    main()
